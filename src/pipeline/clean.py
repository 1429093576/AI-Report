"""Data cleaning pipeline step."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.harness import PipelineContext
from src.schemas import CleanNewsItem, RawNewsItem

from .utils import (
    is_on_report_date,
    model_list_payload,
    path_for,
    report_timezone,
    report_timezone_name,
    require_json_list,
    write_json,
)


_WHITESPACE_RE = re.compile(r"\s+")
_TITLE_PUNCTUATION_RE = re.compile(r"[^\w\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_TRACKING_QUERY_PREFIXES = ("utm_",)
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "gbraid",
    "wbraid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ref",
    "ref_src",
    "spm",
}


@dataclass(frozen=True)
class _Candidate:
    item: CleanNewsItem
    original_index: int
    canonical_url: str
    title_fingerprint: str


def run(context: PipelineContext) -> list[CleanNewsItem]:
    """Clean, normalize, and deduplicate raw AI news items."""

    raw_items = _raw_items(context)
    candidates: list[_Candidate] = []
    current_report_timezone_name = report_timezone_name(context)
    current_report_timezone = report_timezone(context)
    filtered_non_report_date = 0
    filtered_incomplete = 0

    for index, item in enumerate(raw_items):
        title = _normalize_text(item.title)
        source = _normalize_text(item.source)
        url = _normalize_url(item.url)
        summary = _normalize_text(item.summary)
        content = _normalize_text(item.content)
        if not title or not source or not url or not (summary or content):
            filtered_incomplete += 1
            continue
        if not is_on_report_date(item.published_at, context.run_date, current_report_timezone):
            filtered_non_report_date += 1
            continue

        title_fingerprint = _title_fingerprint(title)
        content_hash = _content_hash(title)
        canonical_url = _canonical_url(url)
        metadata = dict(item.metadata)
        metadata["canonical_url"] = canonical_url
        metadata["title_fingerprint"] = title_fingerprint
        candidates.append(
            _Candidate(
                item=CleanNewsItem(
                    id=item.id,
                    title=title,
                    source=source,
                    url=url,
                    published_at=item.published_at,
                    source_type=item.source_type,
                    language=item.language,
                    summary=summary,
                    content=content,
                    metadata=metadata,
                    content_hash=content_hash,
                ),
                original_index=index,
                canonical_url=canonical_url,
                title_fingerprint=title_fingerprint,
            )
        )

    cleaned, dedupe_audit = _dedupe_candidates(candidates)
    quality = _clean_quality(
        raw_count=len(raw_items),
        candidate_count=len(candidates),
        cleaned=cleaned,
        filtered_incomplete=filtered_incomplete,
        filtered_non_report_date=filtered_non_report_date,
        dedupe_audit=dedupe_audit,
    )
    output_path = path_for(context, "cleaned")
    write_json(output_path, model_list_payload(cleaned))
    context.add_artifact("cleaned", output_path)
    context.set("cleaned_items", cleaned)
    context.set("cleaned_count", len(cleaned))
    context.set("clean_report_timezone", current_report_timezone_name)
    context.set("clean_filtered_non_report_date_count", filtered_non_report_date)
    context.set("clean_quality", quality)
    if not cleaned:
        raise ValueError("no report-date news items remain after cleaning")
    return cleaned


def _raw_items(context: PipelineContext) -> list[RawNewsItem]:
    items = context.get("raw_items")
    if items is None:
        items = require_json_list(path_for(context, "raw"))
    return [
        item if isinstance(item, RawNewsItem) else RawNewsItem.model_validate(item)
        for item in items
    ]


def _normalize_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value or "").strip()


def _normalize_url(value: str) -> str:
    return (value or "").strip()


def _content_hash(title: str) -> str:
    fingerprint = _title_fingerprint(title)
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()


def _title_fingerprint(title: str) -> str:
    lowered = title.lower()
    normalized = _TITLE_PUNCTUATION_RE.sub(" ", lowered)
    return _normalize_text(normalized)


def _canonical_url(value: str) -> str:
    url = _normalize_url(value)
    if not url:
        return ""
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return url.lower()

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    elif netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]

    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_pairs = sorted(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not _is_tracking_query_key(key)
        ]
    )
    query = urlencode(query_pairs, doseq=True)
    return urlunsplit((scheme, netloc, path, query, ""))


def _is_tracking_query_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_QUERY_KEYS or any(
        lowered.startswith(prefix) for prefix in _TRACKING_QUERY_PREFIXES
    )


def _dedupe_candidates(candidates: list[_Candidate]) -> tuple[list[CleanNewsItem], list[dict[str, Any]]]:
    groups = _dedupe_groups(candidates)
    cleaned: list[CleanNewsItem] = []
    audit: list[dict[str, Any]] = []
    for group in groups:
        winner = max(group, key=_quality_sort_key)
        cleaned.append(winner.item)
        if len(group) == 1:
            continue
        discarded = [candidate for candidate in group if candidate is not winner]
        reasons = _duplicate_reasons(group)
        audit.append(
            {
                "kept_id": winner.item.id,
                "kept_url": winner.item.url,
                "reasons": reasons,
                "discarded": [
                    {
                        "id": candidate.item.id,
                        "url": candidate.item.url,
                        "source": candidate.item.source,
                    }
                    for candidate in discarded
                ],
            }
        )
    cleaned.sort(key=lambda item: _candidate_index(candidates, item.id))
    return cleaned, audit


def _dedupe_groups(candidates: list[_Candidate]) -> list[list[_Candidate]]:
    parent = list(range(len(candidates)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_canonical_url: dict[str, int] = {}
    by_title: dict[str, int] = {}
    for index, candidate in enumerate(candidates):
        if candidate.canonical_url in by_canonical_url:
            union(by_canonical_url[candidate.canonical_url], index)
        else:
            by_canonical_url[candidate.canonical_url] = index

        if candidate.title_fingerprint in by_title:
            union(by_title[candidate.title_fingerprint], index)
        else:
            by_title[candidate.title_fingerprint] = index

    grouped: dict[int, list[_Candidate]] = {}
    for index, candidate in enumerate(candidates):
        grouped.setdefault(find(index), []).append(candidate)
    return list(grouped.values())


def _duplicate_reasons(group: list[_Candidate]) -> list[str]:
    reasons: list[str] = []
    canonical_urls = [candidate.canonical_url for candidate in group]
    titles = [candidate.title_fingerprint for candidate in group]
    hashes = [candidate.item.content_hash for candidate in group]
    if len(set(canonical_urls)) < len(canonical_urls):
        reasons.append("canonical_url")
    if len(set(titles)) < len(titles):
        reasons.append("title_fingerprint")
    if len(set(hashes)) < len(hashes):
        reasons.append("content_hash")
    return reasons


def _quality_sort_key(candidate: _Candidate) -> tuple[int, int, int, int, int, int]:
    item = candidate.item
    metadata = item.metadata
    return (
        _origin_score(item),
        1 if metadata.get("content_source") == "article_html" else 0,
        0 if metadata.get("full_content_error") else 1,
        len(item.content or ""),
        len(item.summary or ""),
        -candidate.original_index,
    )


def _origin_score(item: CleanNewsItem) -> int:
    metadata = item.metadata
    family = str(metadata.get("source_family") or "").lower()
    fetched_via = str(metadata.get("fetched_via") or "").lower()
    canonical_url = str(metadata.get("canonical_url") or item.url).lower()
    if family == "aggregator" or "news.google.com" in canonical_url:
        return 0
    if fetched_via in {"rss", "arxiv_api", "github_releases", "hackernews_api"}:
        return 2
    return 1


def _candidate_index(candidates: list[_Candidate], item_id: str) -> int:
    for candidate in candidates:
        if candidate.item.id == item_id:
            return candidate.original_index
    return 0


def _clean_quality(
    *,
    raw_count: int,
    candidate_count: int,
    cleaned: list[CleanNewsItem],
    filtered_incomplete: int,
    filtered_non_report_date: int,
    dedupe_audit: list[dict[str, Any]],
) -> dict[str, Any]:
    duplicate_count = sum(len(group["discarded"]) for group in dedupe_audit)
    full_content_errors: dict[str, int] = {}
    fallback_count = 0
    short_content_count = 0
    aggregator_count = 0
    for item in cleaned:
        metadata = item.metadata
        error = metadata.get("full_content_error")
        if error:
            key = str(error)
            full_content_errors[key] = full_content_errors.get(key, 0) + 1
        if metadata.get("content_source") != "article_html":
            fallback_count += 1
        if len(item.content or "") < 200:
            short_content_count += 1
        if str(metadata.get("source_family") or "").lower() == "aggregator":
            aggregator_count += 1

    warning_reasons: list[str] = []
    if cleaned and fallback_count / len(cleaned) > 0.5:
        warning_reasons.append("high_fallback_content_ratio")
    if cleaned and short_content_count / len(cleaned) > 0.5:
        warning_reasons.append("high_short_content_ratio")
    if candidate_count and duplicate_count / candidate_count > 0.5:
        warning_reasons.append("high_duplicate_ratio")
    status = "warning" if warning_reasons else "ok"

    return {
        "status": status,
        "warning_reasons": warning_reasons,
        "raw_count": raw_count,
        "candidate_count": candidate_count,
        "cleaned_count": len(cleaned),
        "filtered_incomplete_count": filtered_incomplete,
        "filtered_non_report_date_count": filtered_non_report_date,
        "duplicate_count": duplicate_count,
        "dedupe_groups": dedupe_audit,
        "full_content_error_count": sum(full_content_errors.values()),
        "full_content_errors": full_content_errors,
        "fallback_content_count": fallback_count,
        "short_content_count": short_content_count,
        "aggregator_count": aggregator_count,
    }
