"""Source adapter interfaces and implementations."""

from __future__ import annotations

import email.utils
import hashlib
import json
import os
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote_plus, urlparse

import feedparser
import requests


DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_ITEMS = 10
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_FULL_CONTENT_MIN_CHARS = 400
DEFAULT_RETRY_ATTEMPTS = 1
DEFAULT_RETRY_BACKOFF_SECONDS = 0.0
DEFAULT_RETRY_STATUS_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_USER_AGENT = "daily-ai-insight-engine/0.1"
SOURCE_MODES_MULTI = {"configured", "multi_source", "multi"}
SOURCE_MODES_ONLINE = {"online", "live", "remote"}
SOURCE_MODES_LOCAL_FIXTURE = {"local_fixture", "fixture", "offline_fixture"}
SOURCE_INHERITED_CONFIG_KEYS = {
    "candidate_keywords",
    "fetch_full_content",
    "full_content_min_chars",
    "full_content_max_chars",
    "retry_attempts",
    "retry_backoff_seconds",
    "retry_status_codes",
    "timeout_seconds",
}


class SourceAdapter(ABC):
    """Base interface for data source adapters."""

    @abstractmethod
    def collect(self) -> list[dict[str, Any]]:
        """Collect raw items from a source."""


class LocalJsonSourceAdapter(SourceAdapter):
    """Collect raw items from a local JSON array file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def collect(self) -> list[dict[str, Any]]:
        """Read and return source items from the configured JSON file."""

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{self.path} must contain a JSON array")

        items: list[dict[str, Any]] = []
        for index, item in enumerate(payload):
            if not isinstance(item, dict):
                raise ValueError(f"{self.path} item {index} must be a JSON object")
            items.append(item)
        return items


class HTTPSourceAdapter(SourceAdapter):
    """Base adapter for HTTP-backed sources."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        self.config = dict(config)
        self.session = session if session is not None else requests.Session()
        self.name = _non_empty(self.config.get("name"), "source")
        self.family = _str_value(self.config.get("family"), "unknown")
        self.source = _str_value(self.config.get("source"), self.name)
        self.source_type = _str_value(self.config.get("source_type"), "news")
        self.language = _str_value(self.config.get("language"), "en")
        self.max_items = _positive_int(self.config.get("max_items"), DEFAULT_MAX_ITEMS)
        self.lookback_days = _positive_int(
            self.config.get("lookback_days"),
            DEFAULT_LOOKBACK_DAYS,
        )
        self.timeout_seconds = _positive_float(
            self.config.get("timeout_seconds"),
            DEFAULT_TIMEOUT_SECONDS,
        )
        self.retry_attempts = _positive_int(
            self.config.get("retry_attempts"),
            DEFAULT_RETRY_ATTEMPTS,
        )
        self.retry_backoff_seconds = _non_negative_float(
            self.config.get("retry_backoff_seconds"),
            DEFAULT_RETRY_BACKOFF_SECONDS,
        )
        self.retry_status_codes = _status_code_set(
            self.config.get("retry_status_codes"),
            DEFAULT_RETRY_STATUS_CODES,
        )
        self.fetch_full_content = _bool_value(self.config.get("fetch_full_content"), False)
        self.full_content_min_chars = _positive_int(
            self.config.get("full_content_min_chars"),
            DEFAULT_FULL_CONTENT_MIN_CHARS,
        )
        self.full_content_max_chars = _optional_positive_int(
            self.config.get("full_content_max_chars"),
        )
        self.headers = {"User-Agent": DEFAULT_USER_AGENT}
        self.request_attempts = 0
        self.request_errors: list[dict[str, Any]] = []

    def _get(self, url: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(1, self.retry_attempts + 1):
            start = perf_counter()
            response = None
            self.request_attempts += 1
            try:
                response = self.session.get(
                    url,
                    headers=self._headers(),
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                return response
            except Exception as exc:
                last_error = exc
                status_code = _response_status_code(response)
                retryable = self._should_retry(exc, status_code, attempt)
                self._record_request_error(
                    url=url,
                    attempt=attempt,
                    response=response,
                    error=exc,
                    retryable=retryable,
                    duration_ms=_elapsed_ms(start),
                )
                if retryable:
                    self._sleep_before_retry(attempt)
                    continue
                raise

        if last_error is not None:  # pragma: no cover - defensive fallback
            raise last_error
        raise RuntimeError(f"{self.name} request failed without an error")

    def _get_json(self, url: str) -> Any:
        response = self._get(url)
        return response.json()

    def _headers(self) -> dict[str, str]:
        return dict(self.headers)

    def reset_diagnostics(self) -> None:
        self.request_attempts = 0
        self.request_errors = []

    def _should_retry(
        self,
        error: Exception,
        status_code: int | None,
        attempt: int,
    ) -> bool:
        if attempt >= self.retry_attempts:
            return False
        if status_code in self.retry_status_codes:
            return True
        return isinstance(error, (requests.Timeout, requests.ConnectionError))

    def _record_request_error(
        self,
        *,
        url: str,
        attempt: int,
        response: Any,
        error: Exception,
        retryable: bool,
        duration_ms: int,
    ) -> None:
        status_code = _response_status_code(response)
        event: dict[str, Any] = {
            "url": url,
            "attempt": attempt,
            "error_type": error.__class__.__name__,
            "message": _short_message(str(error)),
            "retryable": retryable,
            "duration_ms": duration_ms,
        }
        if status_code is not None:
            event["status_code"] = status_code
        retry_after = _response_header(response, "retry-after")
        if retry_after:
            event["retry_after"] = retry_after
        if status_code == 429:
            event["category"] = "rate_limited"
        elif status_code is not None:
            event["category"] = "http_error"
        else:
            event["category"] = "network_error"
        self.request_errors.append(event)

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds <= 0:
            return
        time.sleep(self.retry_backoff_seconds * attempt)

    def _base_metadata(
        self,
        *,
        source_url: str,
        external_id: str,
        fetched_via: str,
    ) -> dict[str, Any]:
        return {
            "source_name": self.name,
            "source_family": self.family,
            "source_url": source_url,
            "external_id": external_id,
            "fetched_via": fetched_via,
        }

    def _within_window(self, published_at: str) -> bool:
        published = _parse_datetime(published_at)
        if published is None:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        return published >= cutoff

    def _raw_item(
        self,
        *,
        title: str,
        url: str,
        published_at: str,
        summary: str = "",
        content: str = "",
        external_id: str = "",
        source_url: str = "",
        fetched_via: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        item_id = f"raw-{self.name}-{_stable_id(external_id or url or title)}"
        item_metadata = self._base_metadata(
            source_url=source_url or url,
            external_id=external_id or url,
            fetched_via=fetched_via,
        )
        if metadata:
            item_metadata.update(dict(metadata))
        return {
            "id": item_id,
            "title": title,
            "source": self.source,
            "url": url,
            "published_at": published_at,
            "source_type": self.source_type,
            "language": self.language,
            "summary": summary,
            "content": content or summary or title,
            "metadata": item_metadata,
        }

    def _full_content(
        self,
        url: str,
        fallback: str,
        *,
        fallback_source: str,
    ) -> tuple[str, dict[str, Any]]:
        """Return article page text when configured, otherwise the fallback text."""

        fallback_text = _text(fallback)
        metadata: dict[str, Any] = {
            "content_source": fallback_source,
            "content_chars": len(fallback_text),
            "full_content_enabled": self.fetch_full_content,
        }
        if not self.fetch_full_content:
            return fallback_text, metadata
        if not _is_http_url(url):
            metadata["full_content_error"] = "non_http_url"
            return fallback_text, metadata

        try:
            response = self._get(url)
        except Exception as exc:
            metadata["full_content_error"] = f"{exc.__class__.__name__}: {exc}"
            return fallback_text, metadata

        content_type = _response_header(response, "content-type")
        if content_type and "html" not in content_type.lower():
            metadata["full_content_error"] = f"unsupported_content_type: {content_type}"
            return fallback_text, metadata

        extracted = _extract_article_text(getattr(response, "text", ""))
        metadata["full_content_chars"] = len(extracted)
        metadata["full_content_url"] = getattr(response, "url", url)
        if len(extracted) < self.full_content_min_chars:
            metadata["full_content_error"] = "extracted_text_too_short"
            return fallback_text, metadata
        if len(extracted) <= len(fallback_text):
            metadata["full_content_error"] = "extracted_text_not_longer_than_fallback"
            return fallback_text, metadata

        content, truncated = _maybe_truncate_text(extracted, self.full_content_max_chars)
        metadata["content_source"] = "article_html"
        metadata["content_truncated"] = truncated
        metadata["content_chars"] = len(content)
        return content, metadata


class RSSSourceAdapter(HTTPSourceAdapter):
    """Collect raw items from an RSS or Atom feed."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        super().__init__(config, session=session)
        self.url = _non_empty(self.config.get("url"), f"{self.name}.url")
        keywords = self.config.get("candidate_keywords", [])
        self.candidate_keywords = [str(keyword).lower() for keyword in keywords if str(keyword).strip()]

    def collect(self) -> list[dict[str, Any]]:
        """Fetch and map feed entries to raw news item dictionaries."""

        self.reset_diagnostics()
        response = self._get(self.url)
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
            raise ValueError(f"{self.name} returned an invalid RSS/Atom feed")

        items: list[dict[str, Any]] = []
        for entry in parsed.entries:
            item = self._entry_to_item(entry)
            if item is not None:
                items.append(item)
            if len(items) >= self.max_items:
                break
        return items

    def _entry_to_item(self, entry: Any) -> dict[str, Any] | None:
        title = _text(getattr(entry, "title", ""))
        link = _text(getattr(entry, "link", ""))
        if not title or not link:
            return None

        published_at = _entry_datetime(entry)
        if not self._within_window(published_at):
            return None

        summary = _html_to_text(
            _text(
                getattr(
                    entry,
                    "summary",
                    getattr(entry, "description", ""),
                )
            )
        )
        if self.candidate_keywords and not _contains_candidate_keywords(
            title,
            summary,
            self.candidate_keywords,
        ):
            return None
        content = _entry_content(entry) or summary
        content, content_metadata = self._full_content(
            link,
            content,
            fallback_source="rss_feed",
        )
        external_id = _text(getattr(entry, "id", "")) or _text(getattr(entry, "guid", ""))
        return self._raw_item(
            title=title,
            url=link,
            published_at=published_at,
            summary=summary,
            content=content,
            external_id=external_id or link,
            source_url=self.url,
            fetched_via="rss",
            metadata=content_metadata,
        )


class GoogleNewsRSSSourceAdapter(RSSSourceAdapter):
    """Collect Google News search results through its public RSS endpoint."""

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        normalized = dict(config)
        if not normalized.get("url"):
            query = _non_empty(normalized.get("query"), f"{normalized.get('name', 'source')}.query")
            language = _str_value(normalized.get("language"), "en").lower()
            hl = "zh-CN" if language == "zh" else "en-US"
            ceid = "CN:zh-Hans" if language == "zh" else "US:en"
            normalized["url"] = (
                "https://news.google.com/rss/search?"
                f"q={quote_plus(query)}&hl={hl}&gl={ceid.split(':')[0]}&ceid={ceid}"
            )
        super().__init__(normalized, session=session)


class ArxivSourceAdapter(HTTPSourceAdapter):
    """Collect arXiv entries from the public Atom API."""

    API_URL = "https://export.arxiv.org/api/query"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        super().__init__(config, session=session)
        self.query = _non_empty(self.config.get("query"), f"{self.name}.query")

    def collect(self) -> list[dict[str, Any]]:
        """Fetch and map arXiv Atom entries."""

        self.reset_diagnostics()
        url = (
            f"{self.API_URL}?search_query={quote_plus(self.query)}"
            f"&start=0&max_results={self.max_items}&sortBy=submittedDate&sortOrder=descending"
        )
        response = self._get(url)
        parsed = feedparser.parse(response.content)
        items: list[dict[str, Any]] = []
        for entry in parsed.entries:
            item = self._entry_to_item(entry, url)
            if item is not None:
                items.append(item)
        return items

    def _entry_to_item(self, entry: Any, source_url: str) -> dict[str, Any] | None:
        title = _text(getattr(entry, "title", ""))
        link = _text(getattr(entry, "link", "")) or _text(getattr(entry, "id", ""))
        if not title or not link:
            return None

        published_at = _entry_datetime(entry)
        if not self._within_window(published_at):
            return None

        summary = _html_to_text(_text(getattr(entry, "summary", "")))
        external_id = _text(getattr(entry, "id", "")) or link
        return self._raw_item(
            title=title,
            url=link,
            published_at=published_at,
            summary=summary,
            content=summary,
            external_id=external_id,
            source_url=source_url,
            fetched_via="arxiv_api",
            metadata={
                "query": self.query,
                "content_source": "arxiv_summary",
                "content_chars": len(summary),
                "authors": _entry_authors(entry),
                "categories": _entry_categories(entry),
                "primary_category": _entry_primary_category(entry),
                "pdf_url": _entry_pdf_url(entry),
            },
        )


class GitHubReleasesSourceAdapter(HTTPSourceAdapter):
    """Collect releases from public GitHub repositories."""

    API_ROOT = "https://api.github.com/repos"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        super().__init__(config, session=session)
        repos = self.config.get("repos")
        if not isinstance(repos, list) or not repos:
            raise ValueError(f"{self.name}.repos must be a non-empty list")
        self.repos = [str(repo).strip() for repo in repos if str(repo).strip()]
        self.token = _config_or_env(self.config, "token", "GITHUB_TOKEN")
        self.per_repo_max_items = _positive_int(
            self.config.get("per_repo_max_items"),
            max(1, self.max_items),
        )
        self.repo_errors: list[dict[str, Any]] = []
        self.rate_limit_errors: list[dict[str, Any]] = []

    def collect(self) -> list[dict[str, Any]]:
        """Fetch releases from configured public repositories."""

        self.reset_diagnostics()
        repo_items: list[list[dict[str, Any]]] = []
        self.repo_errors: list[dict[str, Any]] = []
        self.rate_limit_errors = []
        for repo in self.repos:
            url = f"{self.API_ROOT}/{repo}/releases?per_page={self.per_repo_max_items}"
            try:
                response = self.session.get(
                    url,
                    headers=self._headers(),
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:
                self.repo_errors.append(
                    {
                        "repo": repo,
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                continue
            if response.status_code in {403, 429}:
                self.rate_limit_errors.append(_github_rate_limit_error(repo, response))
                continue
            try:
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                self.repo_errors.append(
                    {
                        "repo": repo,
                        "status_code": getattr(response, "status_code", None),
                        "error": f"{exc.__class__.__name__}: {exc}",
                    }
                )
                continue
            if not isinstance(payload, list):
                self.repo_errors.append(
                    {
                        "repo": repo,
                        "error": f"{self.name} GitHub response for {repo} must be a list",
                    }
                )
                continue
            current_items: list[dict[str, Any]] = []
            for release in payload:
                if not isinstance(release, dict):
                    continue
                item = self._release_to_item(repo, release, url)
                if item is not None:
                    current_items.append(item)
            repo_items.append(current_items)

        return _round_robin_items(repo_items, self.max_items)

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        headers["Accept"] = "application/vnd.github+json"
        return headers

    def _release_to_item(
        self,
        repo: str,
        release: Mapping[str, Any],
        source_url: str,
    ) -> dict[str, Any] | None:
        title = _text(release.get("name")) or _text(release.get("tag_name"))
        html_url = _text(release.get("html_url"))
        if not title or not html_url:
            return None

        published_at = _text(release.get("published_at")) or _utc_now_iso()
        if not self._within_window(published_at):
            return None

        body = _html_to_text(_text(release.get("body")))
        external_id = _text(release.get("id")) or f"{repo}:{title}"
        return self._raw_item(
            title=f"{repo} {title}",
            url=html_url,
            published_at=published_at,
            summary=body[:500],
            content=body,
            external_id=external_id,
            source_url=source_url,
            fetched_via="github_releases",
            metadata={
                "repo": repo,
                "tag_name": _text(release.get("tag_name")),
                "content_source": "github_release_body",
                "content_chars": len(body),
            },
        )


class HackerNewsSourceAdapter(HTTPSourceAdapter):
    """Collect Hacker News stories from the public Firebase API."""

    API_ROOT = "https://hacker-news.firebaseio.com/v0"

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        session: Any = None,
    ) -> None:
        super().__init__(config, session=session)
        lists = self.config.get("lists", ["topstories"])
        if not isinstance(lists, list) or not lists:
            raise ValueError(f"{self.name}.lists must be a non-empty list")
        self.lists = [str(name).strip() for name in lists if str(name).strip()]
        keywords = self.config.get("keywords", [])
        self.keywords = [str(keyword).lower() for keyword in keywords if str(keyword).strip()]
        self.include_discussions = _bool_value(self.config.get("include_discussions"), True)

    def collect(self) -> list[dict[str, Any]]:
        """Fetch and map HN stories."""

        self.reset_diagnostics()
        items: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for list_name in self.lists:
            ids = self._story_ids(list_name)
            for story_id in ids:
                if story_id in seen_ids:
                    continue
                seen_ids.add(story_id)
                story = self._story(story_id)
                item = self._story_to_item(story, list_name)
                if item is not None:
                    items.append(item)
                if len(items) >= self.max_items:
                    return items
        return items

    def _story_ids(self, list_name: str) -> list[int]:
        response = self._get(f"{self.API_ROOT}/{list_name}.json")
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"{self.name} {list_name} response must be a list")
        return [int(story_id) for story_id in payload[: self.max_items * 5]]

    def _story(self, story_id: int) -> Mapping[str, Any]:
        response = self._get(f"{self.API_ROOT}/item/{story_id}.json")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"{self.name} item {story_id} response must be an object")
        return payload

    def _story_to_item(
        self,
        story: Mapping[str, Any],
        list_name: str,
    ) -> dict[str, Any] | None:
        if story.get("type") != "story":
            return None
        title = _text(story.get("title"))
        if not title:
            return None
        text = _html_to_text(_text(story.get("text")))
        story_kind = _hackernews_story_kind(title, story)
        keyword_haystack = f"{title} {text}".lower()
        if self.keywords and not any(keyword in keyword_haystack for keyword in self.keywords):
            return None

        story_id = str(story.get("id"))
        url = _text(story.get("url")) or f"https://news.ycombinator.com/item?id={story_id}"
        is_external_link = not _is_hackernews_url(url)
        if not self.include_discussions and not is_external_link:
            return None
        published_at = _epoch_to_iso(story.get("time"))
        if not self._within_window(published_at):
            return None

        score = story.get("score")
        comments = story.get("descendants")
        summary = _hackernews_summary(
            story_kind=story_kind,
            score=score,
            comments=comments,
            text=text,
        )
        content, content_metadata = self._full_content(
            url,
            text or summary,
            fallback_source="hackernews_summary",
        )
        content_metadata.update(
            {
                "hn_id": story_id,
                "hn_list": list_name,
                "hn_story_kind": story_kind,
                "is_external_link": is_external_link,
                "score": score,
                "comments": comments,
            }
        )
        return self._raw_item(
            title=title,
            url=url,
            published_at=published_at,
            summary=summary,
            content=content,
            external_id=story_id,
            source_url=f"{self.API_ROOT}/{list_name}.json",
            fetched_via="hackernews_api",
            metadata=content_metadata,
        )


class CompositeSourceAdapter(SourceAdapter):
    """Collect from multiple sources, skipping individual source failures."""

    def __init__(self, adapters: list[SourceAdapter]) -> None:
        self.adapters = adapters
        self.errors: list[dict[str, str]] = []
        self.source_metrics: list[dict[str, Any]] = []

    def collect(self) -> list[dict[str, Any]]:
        """Collect items from each configured adapter."""

        if not self.adapters:
            raise ValueError("no enabled sources configured")

        items: list[dict[str, Any]] = []
        self.errors = []
        self.source_metrics = []
        for adapter in self.adapters:
            start = perf_counter()
            try:
                source_items = adapter.collect()
            except Exception as exc:
                error = _source_error(adapter, exc)
                self.errors.append(error)
                self.source_metrics.append(
                    _source_metric(
                        adapter,
                        status=_failed_source_status(adapter),
                        item_count=0,
                        duration_ms=_elapsed_ms(start),
                        errors=[error],
                    )
                )
                continue

            items.extend(source_items)
            self.source_metrics.append(
                _source_metric(
                    adapter,
                    status=_source_status(adapter, len(source_items)),
                    item_count=len(source_items),
                    duration_ms=_elapsed_ms(start),
                )
            )

        if not items:
            detail = "; ".join(f"{item['source']}: {item['error']}" for item in self.errors)
            raise ValueError(f"all enabled sources failed or returned no items: {detail}")
        return items


def create_source_adapter(
    config: Mapping[str, Any] | None = None,
    *,
    session: Any = None,
) -> SourceAdapter:
    """Create a source adapter from pipeline configuration."""

    root = dict(config or {})
    mode_config = root.get("mode", {})
    source_mode = "local_json"
    if isinstance(mode_config, Mapping):
        source_mode = _str_value(mode_config.get("source"), "local_json")

    if source_mode == "local_json":
        return LocalJsonSourceAdapter(_local_path(root))
    if source_mode == "rss":
        return RSSSourceAdapter(_source_config(_single_source(root, "rss"), root), session=session)
    if source_mode in SOURCE_MODES_LOCAL_FIXTURE:
        return CompositeSourceAdapter(
            _enabled_adapters(root, session=session, selection="local_fixture")
        )
    if source_mode in SOURCE_MODES_ONLINE:
        return CompositeSourceAdapter(
            _enabled_adapters(root, session=session, selection="online")
        )
    if source_mode in SOURCE_MODES_MULTI:
        return CompositeSourceAdapter(_enabled_adapters(root, session=session))

    raise ValueError(f"unsupported source mode: {source_mode}")


def _enabled_adapters(
    config: Mapping[str, Any],
    *,
    session: Any = None,
    selection: str | None = None,
) -> list[SourceAdapter]:
    sources = config.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    adapters: list[SourceAdapter] = []
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        if source.get("enabled") is False:
            continue
        if selection == "online" and _is_local_fixture_source(source):
            continue
        if selection == "local_fixture" and not _is_local_fixture_source(source):
            continue
        adapters.append(_adapter_for_source(source, root_config=config, session=session))
    return adapters


def _is_local_fixture_source(source: Mapping[str, Any]) -> bool:
    source_type = _str_value(source.get("type"), "")
    source_family = _str_value(source.get("family"), "")
    return source_type == "local_json" or source_family == "local_fixture"


def _adapter_for_source(
    source: Mapping[str, Any],
    *,
    root_config: Mapping[str, Any],
    session: Any = None,
) -> SourceAdapter:
    source_config = _source_config(source, root_config)
    source_type = _str_value(source_config.get("type"), "")
    if source_type == "local_json":
        path = source_config.get("path") or _local_path(root_config)
        return LocalJsonSourceAdapter(path)
    if source_type == "rss":
        return RSSSourceAdapter(source_config, session=session)
    if source_type == "google_news_rss":
        return GoogleNewsRSSSourceAdapter(source_config, session=session)
    if source_type == "arxiv_api":
        return ArxivSourceAdapter(source_config, session=session)
    if source_type == "github_releases":
        return GitHubReleasesSourceAdapter(source_config, session=session)
    if source_type == "hackernews_api":
        return HackerNewsSourceAdapter(source_config, session=session)
    raise ValueError(f"unsupported source type: {source_type}")


def _source_config(
    source: Mapping[str, Any],
    root_config: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(source)
    pipeline_config = root_config.get("pipeline", {})
    if not isinstance(pipeline_config, Mapping):
        return merged
    for key in SOURCE_INHERITED_CONFIG_KEYS:
        if key in pipeline_config and key not in merged:
            merged[key] = pipeline_config[key]
    return merged


def _single_source(config: Mapping[str, Any], expected_type: str) -> Mapping[str, Any]:
    sources = config.get("sources", [])
    if isinstance(sources, list):
        for source in sources:
            if (
                isinstance(source, Mapping)
                and source.get("enabled") is not False
                and _str_value(source.get("type"), "") == expected_type
            ):
                return source
    raise ValueError(f"no enabled {expected_type} source configured")


def _local_path(config: Mapping[str, Any]) -> str:
    paths = config.get("paths", {})
    if isinstance(paths, Mapping) and paths.get("raw"):
        return str(paths["raw"])
    return "data/raw/ai_news_raw.json"


def _entry_datetime(entry: Any) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc).isoformat()
    for attr in ("published", "updated", "created"):
        value = _text(getattr(entry, attr, ""))
        parsed = _parse_datetime(value)
        if parsed is not None:
            return parsed.isoformat()
    return _utc_now_iso()


def _entry_content(entry: Any) -> str:
    content = getattr(entry, "content", None)
    if isinstance(content, list) and content:
        first = content[0]
        if isinstance(first, Mapping):
            return _html_to_text(_text(first.get("value")))
        return _html_to_text(_text(getattr(first, "value", "")))
    return ""


def _entry_authors(entry: Any) -> list[str]:
    authors = getattr(entry, "authors", None)
    values: list[str] = []
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, Mapping):
                name = _text(author.get("name"))
            else:
                name = _text(getattr(author, "name", ""))
            if name:
                values.append(name)
    if values:
        return values
    author = getattr(entry, "author", "")
    return [_text(author)] if _text(author) else []


def _entry_categories(entry: Any) -> list[str]:
    tags = getattr(entry, "tags", None)
    values: list[str] = []
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, Mapping):
                term = _text(tag.get("term"))
            else:
                term = _text(getattr(tag, "term", ""))
            if term and term not in values:
                values.append(term)
    return values


def _entry_primary_category(entry: Any) -> str:
    primary = getattr(entry, "arxiv_primary_category", None)
    if isinstance(primary, Mapping):
        return _text(primary.get("term"))
    term = _text(getattr(primary, "term", ""))
    if term:
        return term
    categories = _entry_categories(entry)
    return categories[0] if categories else ""


def _entry_pdf_url(entry: Any) -> str:
    links = getattr(entry, "links", None)
    if not isinstance(links, list):
        return ""
    for link in links:
        if isinstance(link, Mapping):
            href = _text(link.get("href"))
            title = _text(link.get("title")).lower()
            link_type = _text(link.get("type")).lower()
        else:
            href = _text(getattr(link, "href", ""))
            title = _text(getattr(link, "title", "")).lower()
            link_type = _text(getattr(link, "type", "")).lower()
        if href and (title == "pdf" or "pdf" in link_type or "/pdf/" in href):
            return href
    return ""


def _github_rate_limit_error(repo: str, response: Any) -> dict[str, Any]:
    reset_at = ""
    reset_value = _response_header(response, "x-ratelimit-reset")
    try:
        reset_at = datetime.fromtimestamp(int(reset_value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        reset_at = ""
    return {
        "repo": repo,
        "status_code": getattr(response, "status_code", None),
        "reason": _text(getattr(response, "reason", "")),
        "rate_limit_remaining": _response_header(response, "x-ratelimit-remaining"),
        "rate_limit_reset_at": reset_at,
    }


def _source_metric(
    adapter: SourceAdapter,
    *,
    status: str,
    item_count: int,
    duration_ms: int,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metric: dict[str, Any] = {
        "source": _source_name(adapter),
        "adapter_type": adapter.__class__.__name__,
        "status": status,
        "items": item_count,
        "duration_ms": duration_ms,
        "attempts": _int_attr(adapter, "request_attempts"),
        "errors": list(errors or []),
        "warnings": [],
    }
    request_errors = getattr(adapter, "request_errors", None)
    if isinstance(request_errors, list):
        metric["request_errors"] = list(request_errors)
    repo_errors = getattr(adapter, "repo_errors", None)
    if isinstance(repo_errors, list) and repo_errors:
        metric["errors"].extend(_source_nested_errors(adapter, "repo", repo_errors))
    rate_limit_errors = getattr(adapter, "rate_limit_errors", None)
    if isinstance(rate_limit_errors, list) and rate_limit_errors:
        metric["errors"].extend(_source_nested_errors(adapter, "rate_limit", rate_limit_errors))
    return metric


def _source_status(adapter: SourceAdapter, item_count: int) -> str:
    repo_errors = getattr(adapter, "repo_errors", None)
    rate_limit_errors = getattr(adapter, "rate_limit_errors", None)
    has_repo_errors = isinstance(repo_errors, list) and bool(repo_errors)
    has_rate_limits = isinstance(rate_limit_errors, list) and bool(rate_limit_errors)
    if item_count <= 0:
        if has_rate_limits and not has_repo_errors:
            return "rate_limited"
        return "empty"
    if has_repo_errors or has_rate_limits:
        return "partial"
    return "succeeded"


def _failed_source_status(adapter: SourceAdapter) -> str:
    request_errors = getattr(adapter, "request_errors", None)
    if isinstance(request_errors, list) and any(
        isinstance(error, dict) and error.get("category") == "rate_limited"
        for error in request_errors
    ):
        return "rate_limited"
    return "failed"


def _source_error(adapter: SourceAdapter, error: Exception) -> dict[str, str]:
    return {
        "source": _source_name(adapter),
        "error": f"{error.__class__.__name__}: {_short_message(str(error))}",
    }


def _source_nested_errors(
    adapter: SourceAdapter,
    category: str,
    errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source = _source_name(adapter)
    nested: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        current = {"source": source, "category": category}
        current.update(error)
        if "error" in current:
            current["error"] = _short_message(str(current["error"]))
        nested.append(current)
    return nested


def _source_name(adapter: SourceAdapter) -> str:
    return _text(getattr(adapter, "name", "")) or adapter.__class__.__name__


def _int_attr(obj: Any, name: str) -> int:
    try:
        parsed = int(getattr(obj, name, 0))
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _round_robin_items(groups: list[list[dict[str, Any]]], max_items: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    index = 0
    while len(items) < max_items:
        added = False
        for group in groups:
            if index >= len(group):
                continue
            items.append(group[index])
            added = True
            if len(items) >= max_items:
                break
        if not added:
            break
        index += 1
    return items


def _hackernews_story_kind(title: str, story: Mapping[str, Any]) -> str:
    lower_title = title.lower()
    if lower_title.startswith("ask hn:"):
        return "ask_hn"
    if lower_title.startswith("show hn:"):
        return "show_hn"
    if not _text(story.get("url")):
        return "internal_discussion"
    return "external_link"


def _is_hackernews_url(url: str) -> bool:
    parsed = urlparse(_text(url))
    return parsed.netloc.lower() in {"news.ycombinator.com", "www.news.ycombinator.com"}


def _hackernews_summary(
    *,
    story_kind: str,
    score: Any,
    comments: Any,
    text: str,
) -> str:
    label = {
        "ask_hn": "Ask HN discussion",
        "show_hn": "Show HN submission",
        "internal_discussion": "Hacker News discussion",
        "external_link": "Hacker News linked story",
    }.get(story_kind, "Hacker News story")
    summary = f"{label} with score {score or 0} and {comments or 0} comments."
    if text:
        return f"{summary} {text}"
    return summary


class _ArticleTextParser(HTMLParser):
    """Small dependency-free article text extractor for news pages."""

    BLOCK_TAGS = {
        "p",
        "li",
        "h1",
        "h2",
        "h3",
        "blockquote",
        "pre",
    }
    CONTAINER_TAGS = {"article", "main"}
    SKIP_TAGS = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "iframe",
        "form",
        "nav",
        "header",
        "footer",
        "aside",
        "button",
    }
    CONTAINER_HINTS = (
        "article",
        "content",
        "story",
        "post",
        "entry",
        "body",
        "main",
    )

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._container_depth = 0
        self._tag_stack: list[tuple[str, bool]] = []
        self._current_tag = ""
        self._current_parts: list[str] = []
        self.blocks: list[str] = []
        self.container_blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return

        is_container = tag in self.CONTAINER_TAGS or self._has_container_hint(attrs)
        self._tag_stack.append((tag, is_container))
        if is_container:
            self._container_depth += 1
        if tag in self.BLOCK_TAGS:
            self._current_tag = tag
            self._current_parts = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in self.SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)
            return

        if tag == self._current_tag:
            self._finish_block()

        for index in range(len(self._tag_stack) - 1, -1, -1):
            current_tag, _ = self._tag_stack[index]
            if current_tag == tag:
                removed = self._tag_stack[index:]
                self._tag_stack = self._tag_stack[:index]
                self._container_depth = max(
                    0,
                    self._container_depth - sum(1 for _, is_container in removed if is_container),
                )
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not self._current_tag:
            return
        self._current_parts.append(data)

    def close(self) -> None:
        self._finish_block()
        super().close()

    def _finish_block(self) -> None:
        if not self._current_tag:
            return
        text = _normalize_whitespace(" ".join(self._current_parts))
        if _is_useful_article_block(text):
            self.blocks.append(text)
            if self._container_depth:
                self.container_blocks.append(text)
        self._current_tag = ""
        self._current_parts = []

    def _has_container_hint(self, attrs: list[tuple[str, str | None]]) -> bool:
        for name, value in attrs:
            if name.lower() not in {"id", "class", "role", "itemprop"}:
                continue
            haystack = (value or "").lower()
            if any(hint in haystack for hint in self.CONTAINER_HINTS):
                return True
        return False


def _extract_article_text(html: str) -> str:
    text = _text(html)
    if not text:
        return ""

    parser = _ArticleTextParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return ""

    container_text = "\n\n".join(_dedupe_blocks(parser.container_blocks))
    all_text = "\n\n".join(_dedupe_blocks(parser.blocks))
    if len(container_text) >= min(400, len(all_text)):
        return container_text
    return all_text


def _is_useful_article_block(text: str) -> bool:
    if not text:
        return False
    if not _has_sufficient_block_text(text):
        return False
    lower = text.lower()
    blocked_phrases = (
        "accept cookies",
        "all rights reserved",
        "sign up",
        "subscribe to",
        "privacy policy",
        "terms of service",
    )
    if any(phrase in lower for phrase in blocked_phrases):
        return False
    return len(text) >= 35 or text.endswith((".", "!", "?", "。", "！", "？"))


def _has_sufficient_block_text(text: str) -> bool:
    words = text.split()
    if len(words) >= 5:
        return True

    cjk_chars = sum(1 for char in text if _is_cjk_character(char))
    return cjk_chars >= 12


def _is_cjk_character(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _dedupe_blocks(blocks: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for block in blocks:
        key = block.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(block)
    return deduped


def _contains_candidate_keywords(title: str, summary: str, keywords: list[str]) -> bool:
    haystack = f"{_text(title)} {_text(summary)}".lower()
    return any(keyword in haystack for keyword in keywords)


def _normalize_whitespace(value: str) -> str:
    return _fix_punctuation_spacing(" ".join(value.split()))


def _parse_datetime(value: str) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_email = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        parsed = parsed_email
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _epoch_to_iso(value: Any) -> str:
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return _utc_now_iso()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _html_to_text(value: str) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        class Stripper(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str) -> None:
                self.parts.append(data)

        stripper = Stripper()
        stripper.feed(text)
        return _fix_punctuation_spacing(" ".join(" ".join(stripper.parts).split()))
    except Exception:
        return _fix_punctuation_spacing(" ".join(text.split()))


def _fix_punctuation_spacing(value: str) -> str:
    return re.sub(r"\s+([.,;:!?])", r"\1", value)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _response_header(response: Any, name: str) -> str:
    headers = getattr(response, "headers", {})
    if not isinstance(headers, Mapping):
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return _text(value)
    return ""


def _is_http_url(value: str) -> bool:
    parsed = urlparse(_text(value))
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _maybe_truncate_text(value: str, max_chars: int | None) -> tuple[str, bool]:
    if max_chars is None:
        return value, False
    if len(value) <= max_chars:
        return value, False
    cutoff = max(1, max_chars)
    truncated = value[:cutoff].rstrip()
    last_break = max(truncated.rfind("\n\n"), truncated.rfind(". "), truncated.rfind("。"))
    if last_break > cutoff // 2:
        truncated = truncated[: last_break + 1].rstrip()
    return truncated, True


def _str_value(value: Any, default: str) -> str:
    text = _text(value)
    return text if text else default


def _non_empty(value: Any, label: str) -> str:
    text = _text(value)
    if not text:
        raise ValueError(f"{label} must not be empty")
    return text


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, parsed)


def _optional_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, parsed)


def _positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.1, parsed)


def _non_negative_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, parsed)


def _status_code_set(value: Any, default: set[int]) -> set[int]:
    if not isinstance(value, list):
        return set(default)
    parsed: set[int] = set()
    for item in value:
        try:
            status_code = int(item)
        except (TypeError, ValueError):
            continue
        if 100 <= status_code <= 599:
            parsed.add(status_code)
    return parsed or set(default)


def _response_status_code(response: Any) -> int | None:
    try:
        return int(getattr(response, "status_code", None))
    except (TypeError, ValueError):
        return None


def _elapsed_ms(start: float) -> int:
    return max(0, round((perf_counter() - start) * 1000))


def _short_message(value: str, max_chars: int = 300) -> str:
    text = _normalize_whitespace(value)
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _bool_value(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _config_or_env(config: Mapping[str, Any], config_key: str, env_key: str) -> str:
    value = config.get(config_key)
    if value is None:
        value = os.getenv(env_key)
    return _text(value)
