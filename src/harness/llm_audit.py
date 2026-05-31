"""Deterministic audit helpers for LLM-generated evidence."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from src.schemas import CleanNewsItem, DailyInsightReport, EvidenceSource, StructuredNewsItem


ALLOWED_EVIDENCE_FIELDS = {"title", "summary", "content"}
SUPPORTED = "supported"
MISSING_SOURCE = "missing_source"
MISSING_QUOTE = "missing_quote"
INVALID_FIELD = "invalid_field"


def audit_structured_evidence(
    items: Iterable[StructuredNewsItem],
    sources: Iterable[CleanNewsItem],
    *,
    run_id: str,
) -> dict[str, Any]:
    """Audit extraction evidence against the cleaned source text."""

    source_index = {source.id: source for source in sources}
    audits: list[dict[str, Any]] = []

    for item in items:
        evidence_audits = _audit_structured_item_evidence(item, source_index)
        supported_count = sum(
            1 for evidence in evidence_audits if evidence["status"] == SUPPORTED
        )
        audits.append(
            {
                "item_id": item.id,
                "title": item.title,
                "source_item_id": _raw_source_id(item),
                "status": SUPPORTED if supported_count else MISSING_QUOTE,
                "supported_evidence_count": supported_count,
                "required_supported_evidence_count": 1,
                "evidence": evidence_audits,
            }
        )

    return _audit_report(
        run_id=run_id,
        audit_type="structured_evidence",
        records=audits,
        blocked_records=[
            {
                "item_id": audit["item_id"],
                "reason": "missing_supported_evidence",
            }
            for audit in audits
            if audit["supported_evidence_count"] < audit["required_supported_evidence_count"]
        ],
    )


def filter_report_by_supported_evidence(
    report: DailyInsightReport,
    items: Iterable[StructuredNewsItem],
    *,
    run_id: str,
) -> tuple[DailyInsightReport, dict[str, Any]]:
    """Remove report analysis items that lack supported structured evidence."""

    item_index = {item.id: item for item in items}
    records: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    top_events = []
    for index, event in enumerate(report.top_events):
        evidence_audits = _analysis_evidence_sources(
            event.evidence_sources,
            [event.item_id],
            item_index,
        )
        supported = _has_supported_analysis_evidence(evidence_audits)
        record = _analysis_record(
            "top_events",
            index,
            event.title,
            [event.item_id],
            evidence_audits,
        )
        records.append(record)
        if supported:
            top_events.append(
                event.model_copy(
                    update={
                        "evidence_sources": _supported_evidence_sources(
                            event.evidence_sources,
                            evidence_audits,
                        )
                    }
                )
            )
        else:
            excluded.append(record)

    deep_dives = []
    for index, section in enumerate(report.deep_dives):
        evidence_audits = _analysis_evidence_sources(
            section.evidence_sources,
            [section.item_id],
            item_index,
        )
        supported = _has_supported_analysis_evidence(evidence_audits)
        record = _analysis_record(
            "deep_dives",
            index,
            section.item_id,
            [section.item_id],
            evidence_audits,
        )
        records.append(record)
        if supported:
            deep_dives.append(
                section.model_copy(
                    update={
                        "evidence_sources": _supported_evidence_sources(
                            section.evidence_sources,
                            evidence_audits,
                        )
                    }
                )
            )
        else:
            excluded.append(record)

    trend_insights = []
    for index, insight in enumerate(report.trend_insights):
        evidence_audits = _analysis_evidence_sources(
            insight.evidence_sources,
            insight.evidence_item_ids,
            item_index,
        )
        supported = _has_supported_analysis_evidence(evidence_audits)
        record = _analysis_record(
            "trend_insights",
            index,
            insight.title,
            insight.evidence_item_ids,
            evidence_audits,
        )
        records.append(record)
        if supported:
            trend_insights.append(
                insight.model_copy(
                    update={
                        "evidence_sources": _supported_evidence_sources(
                            insight.evidence_sources,
                            evidence_audits,
                        )
                    }
                )
            )
        else:
            excluded.append(record)

    risk_insights = []
    for index, insight in enumerate(report.risk_insights):
        evidence_audits = _analysis_evidence_sources(
            insight.evidence_sources,
            insight.evidence_item_ids,
            item_index,
        )
        supported = _has_supported_analysis_evidence(evidence_audits)
        record = _analysis_record(
            "risk_insights",
            index,
            insight.title,
            insight.evidence_item_ids,
            evidence_audits,
        )
        records.append(record)
        if supported:
            risk_insights.append(
                insight.model_copy(
                    update={
                        "evidence_sources": _supported_evidence_sources(
                            insight.evidence_sources,
                            evidence_audits,
                        )
                    }
                )
            )
        else:
            excluded.append(record)

    opportunity_insights = []
    for index, insight in enumerate(report.opportunity_insights):
        evidence_audits = _analysis_evidence_sources(
            insight.evidence_sources,
            insight.evidence_item_ids,
            item_index,
        )
        supported = _has_supported_analysis_evidence(evidence_audits)
        record = _analysis_record(
            "opportunity_insights",
            index,
            insight.title,
            insight.evidence_item_ids,
            evidence_audits,
        )
        records.append(record)
        if supported:
            opportunity_insights.append(
                insight.model_copy(
                    update={
                        "evidence_sources": _supported_evidence_sources(
                            insight.evidence_sources,
                            evidence_audits,
                        )
                    }
                )
            )
        else:
            excluded.append(record)

    filtered = report.model_copy(
        update={
            "top_events": top_events,
            "deep_dives": deep_dives,
            "trend_insights": trend_insights,
            "risk_insights": risk_insights,
            "opportunity_insights": opportunity_insights,
        }
    )
    return filtered, _audit_report(
        run_id=run_id,
        audit_type="analysis_evidence",
        records=records,
        blocked_records=[
            {
                "section": record["section"],
                "index": record["index"],
                "title": record["title"],
                "reason": "missing_supported_evidence",
                "evidence_item_ids": record["evidence_item_ids"],
            }
            for record in excluded
        ],
    )


def merge_audit_reports(
    *,
    run_id: str,
    reports: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Merge step audit reports into one run-level report."""

    sections: dict[str, Any] = {}
    total_claims = 0
    supported_claims = 0
    unsupported_claims = 0
    blocked_records: list[dict[str, Any]] = []

    for report in reports:
        audit_type = str(report.get("audit_type") or "").strip()
        if audit_type:
            sections[audit_type] = report
        total_claims += _int(report.get("total_claims"))
        supported_claims += _int(report.get("supported_claims"))
        unsupported_claims += _int(report.get("unsupported_claims"))
        blocked = report.get("blocked_records")
        if isinstance(blocked, list):
            blocked_records.extend(record for record in blocked if isinstance(record, dict))

    return {
        "run_id": run_id,
        "generated_at": _utc_now_iso(),
        "total_claims": total_claims,
        "supported_claims": supported_claims,
        "unsupported_claims": unsupported_claims,
        "blocked_count": len(blocked_records),
        "blocked_records": blocked_records,
        "sections": sections,
    }


def _supported_evidence_sources(
    existing_sources: list[EvidenceSource],
    evidence_audits: list[dict[str, Any]],
) -> list[EvidenceSource]:
    if existing_sources:
        supported_keys = {
            _evidence_key(audit)
            for audit in evidence_audits
            if audit.get("status") == SUPPORTED
        }
        return [
            source
            for source in existing_sources
            if _evidence_key(source.model_dump(mode="json")) in supported_keys
        ]

    return [
        EvidenceSource(
            source_item_id=str(audit.get("source_item_id") or ""),
            evidence_field=str(audit.get("evidence_field") or audit.get("matched_field") or ""),
            evidence_quote=str(audit.get("evidence_quote") or ""),
            claim=str(audit.get("claim") or ""),
        )
        for audit in evidence_audits
        if audit.get("status") == SUPPORTED
    ]


def _evidence_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(value.get("source_item_id") or ""),
        str(value.get("evidence_field") or value.get("matched_field") or ""),
        _normalize_match_text(str(value.get("evidence_quote") or "")),
    )


def _audit_structured_item_evidence(
    item: StructuredNewsItem,
    source_index: dict[str, CleanNewsItem],
) -> list[dict[str, Any]]:
    if item.evidence_sources:
        return [
            _audit_evidence_source(source, source_index, item_id=item.id)
            for source in item.evidence_sources
        ]

    source_item_id = _raw_source_id(item)
    return [
        _audit_legacy_quote(
            source_item_id=source_item_id,
            quote=quote,
            claim=item.summary,
            source_index=source_index,
            item_id=item.id,
        )
        for quote in item.evidence
    ]


def _analysis_evidence_sources(
    explicit_sources: list[EvidenceSource],
    evidence_item_ids: list[str],
    item_index: dict[str, StructuredNewsItem],
) -> list[dict[str, Any]]:
    if explicit_sources:
        referenced_items = [
            item_index[item_id] for item_id in evidence_item_ids if item_id in item_index
        ]
        return [
            _audit_report_evidence_source(source, referenced_items)
            for source in explicit_sources
        ]

    audits: list[dict[str, Any]] = []
    for item_id in evidence_item_ids:
        item = item_index.get(item_id)
        if item is None:
            audits.append(
                {
                    "status": MISSING_SOURCE,
                    "item_id": "",
                    "source_item_id": item_id,
                    "evidence_field": "",
                    "evidence_quote": "",
                    "claim": "",
                    "matched_field": "",
                    "match_type": "",
                }
            )
            continue
        if item.evidence_sources:
            audits.extend(
                {
                    "status": SUPPORTED,
                    "item_id": item.id,
                    "source_item_id": source.source_item_id,
                    "evidence_field": source.evidence_field,
                    "evidence_quote": source.evidence_quote,
                    "claim": source.claim,
                    "matched_field": source.evidence_field,
                    "match_type": "structured_item",
                }
                for source in item.evidence_sources
            )
            continue
        if item.evidence:
            audits.append(
                {
                    "status": SUPPORTED,
                    "item_id": item.id,
                    "source_item_id": item.id,
                    "evidence_field": "evidence",
                    "evidence_quote": item.evidence[0],
                    "claim": item.summary,
                    "matched_field": "evidence",
                    "match_type": "legacy_structured_item",
                }
            )
        else:
            audits.append(
                {
                    "status": MISSING_QUOTE,
                    "item_id": item.id,
                    "source_item_id": item.id,
                    "evidence_field": "",
                    "evidence_quote": "",
                    "claim": item.summary,
                    "matched_field": "",
                    "match_type": "",
                }
            )
    return audits


def _audit_report_evidence_source(
    source: EvidenceSource,
    referenced_items: list[StructuredNewsItem],
) -> dict[str, Any]:
    for item in referenced_items:
        if _source_matches_item_evidence(source, item):
            return {
                "status": SUPPORTED,
                "item_id": item.id,
                "source_item_id": source.source_item_id,
                "evidence_field": source.evidence_field,
                "evidence_quote": source.evidence_quote,
                "claim": source.claim,
                "matched_field": source.evidence_field,
                "match_type": "validated_item_evidence",
            }

    return {
        "status": MISSING_QUOTE,
        "item_id": "",
        "source_item_id": source.source_item_id,
        "evidence_field": source.evidence_field,
        "evidence_quote": source.evidence_quote,
        "claim": source.claim,
        "matched_field": "",
        "match_type": "",
    }


def _source_matches_item_evidence(
    source: EvidenceSource,
    item: StructuredNewsItem,
) -> bool:
    source_ids = {item.id, _raw_source_id(item)}
    if source.source_item_id.startswith("structured-"):
        source_ids.add(f"raw-{source.source_item_id.removeprefix('structured-')}")
    if source.source_item_id not in source_ids:
        return False

    if item.evidence_sources:
        for candidate in item.evidence_sources:
            if (
                _same_source_id(candidate.source_item_id, source.source_item_id)
                and candidate.evidence_field == source.evidence_field
                and _same_quote(candidate.evidence_quote, source.evidence_quote)
            ):
                return True
        return False

    return any(_same_quote(quote, source.evidence_quote) for quote in item.evidence)


def _audit_evidence_source(
    evidence: EvidenceSource,
    source_index: dict[str, CleanNewsItem],
    *,
    item_id: str,
) -> dict[str, Any]:
    source = source_index.get(evidence.source_item_id)
    if source is None and evidence.source_item_id.startswith("structured-"):
        source = source_index.get(f"raw-{evidence.source_item_id.removeprefix('structured-')}")
    if source is None:
        return _evidence_audit_payload(evidence, item_id, MISSING_SOURCE)

    field = evidence.evidence_field.strip()
    if field not in ALLOWED_EVIDENCE_FIELDS:
        return _evidence_audit_payload(evidence, item_id, INVALID_FIELD)

    haystack = str(getattr(source, field, "") or "")
    if _contains_quote(haystack, evidence.evidence_quote):
        return _evidence_audit_payload(
            evidence,
            item_id,
            SUPPORTED,
            matched_field=field,
            match_type="normalized_substring",
        )

    return _evidence_audit_payload(evidence, item_id, MISSING_QUOTE, matched_field=field)


def _audit_legacy_quote(
    *,
    source_item_id: str,
    quote: str,
    claim: str,
    source_index: dict[str, CleanNewsItem],
    item_id: str,
) -> dict[str, Any]:
    source = source_index.get(source_item_id)
    if source is None:
        return {
            "item_id": item_id,
            "source_item_id": source_item_id,
            "evidence_field": "",
            "evidence_quote": quote,
            "claim": claim,
            "status": MISSING_SOURCE,
            "matched_field": "",
            "match_type": "",
        }

    for field in ("content", "summary", "title"):
        if _contains_quote(str(getattr(source, field, "") or ""), quote):
            return {
                "item_id": item_id,
                "source_item_id": source_item_id,
                "evidence_field": field,
                "evidence_quote": quote,
                "claim": claim,
                "status": SUPPORTED,
                "matched_field": field,
                "match_type": "legacy_normalized_substring",
            }

    return {
        "item_id": item_id,
        "source_item_id": source_item_id,
        "evidence_field": "",
        "evidence_quote": quote,
        "claim": claim,
        "status": MISSING_QUOTE,
        "matched_field": "",
        "match_type": "",
    }


def _evidence_audit_payload(
    evidence: EvidenceSource,
    item_id: str,
    status: str,
    *,
    matched_field: str = "",
    match_type: str = "",
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source_item_id": evidence.source_item_id,
        "evidence_field": evidence.evidence_field,
        "evidence_quote": evidence.evidence_quote,
        "claim": evidence.claim,
        "status": status,
        "matched_field": matched_field,
        "match_type": match_type,
    }


def _has_supported_analysis_evidence(evidence_audits: list[dict[str, Any]]) -> bool:
    return any(audit.get("status") == SUPPORTED for audit in evidence_audits)


def _analysis_record(
    section: str,
    index: int,
    title: str,
    evidence_item_ids: list[str],
    evidence_audits: list[dict[str, Any]],
) -> dict[str, Any]:
    supported_count = sum(1 for audit in evidence_audits if audit.get("status") == SUPPORTED)
    return {
        "section": section,
        "index": index,
        "title": title,
        "evidence_item_ids": list(evidence_item_ids),
        "status": SUPPORTED if supported_count else MISSING_QUOTE,
        "supported_evidence_count": supported_count,
        "required_supported_evidence_count": 1,
        "evidence": evidence_audits,
    }


def _audit_report(
    *,
    run_id: str,
    audit_type: str,
    records: list[dict[str, Any]],
    blocked_records: list[dict[str, Any]],
) -> dict[str, Any]:
    supported_claims = sum(1 for record in records if record.get("status") == SUPPORTED)
    total_claims = len(records)
    return {
        "run_id": run_id,
        "audit_type": audit_type,
        "generated_at": _utc_now_iso(),
        "total_claims": total_claims,
        "supported_claims": supported_claims,
        "unsupported_claims": total_claims - supported_claims,
        "blocked_count": len(blocked_records),
        "blocked_records": blocked_records,
        "records": records,
    }


def _raw_source_id(item: StructuredNewsItem) -> str:
    if item.id.startswith("structured-"):
        return f"raw-{item.id.removeprefix('structured-')}"
    return item.id


def _contains_quote(text: str, quote: str) -> bool:
    normalized_text = _normalize_match_text(text)
    normalized_quote = _normalize_match_text(quote)
    return bool(normalized_quote) and normalized_quote in normalized_text


def _same_quote(left: str, right: str) -> bool:
    return _normalize_match_text(left) == _normalize_match_text(right)


def _same_source_id(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.startswith("structured-"):
        left = f"raw-{left.removeprefix('structured-')}"
    if right.startswith("structured-"):
        right = f"raw-{right.removeprefix('structured-')}"
    return left == right


def _normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("“", '"').replace("”", '"')
    normalized = normalized.replace("‘", "'").replace("’", "'")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().casefold()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
