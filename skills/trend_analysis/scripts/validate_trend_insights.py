#!/usr/bin/env python3
"""Validate topic trend insights for Daily AI Insight Engine outputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError


@dataclass
class Finding:
    severity: str
    message: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _add_repo_to_path() -> None:
    root = _repo_root()
    root_text = str(root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"File not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON in {path}: {exc}") from exc


def _as_list(payload: Any, label: str) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    raise SystemExit(f"{label} must be a JSON object or array")


def _normalize(value: str) -> str:
    return value.strip().lower()


def _schema_errors(prefix: str, exc: ValidationError) -> list[Finding]:
    findings: list[Finding] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        message = error.get("msg", "validation error")
        findings.append(Finding("error", f"{prefix} schema {location}: {message}"))
    return findings


def _validate_items(payload: Any) -> tuple[list[Any], list[Finding]]:
    from src.schemas import StructuredNewsItem

    items: list[Any] = []
    findings: list[Finding] = []

    for index, raw_item in enumerate(_as_list(payload, "structured news"), start=1):
        try:
            item = StructuredNewsItem.model_validate(raw_item)
        except ValidationError as exc:
            findings.extend(_schema_errors(f"item {index}", exc))
            continue
        items.append(item)

        if not item.topic.strip():
            findings.append(Finding("error", f"{item.id}: topic is empty"))
        if not item.evidence:
            findings.append(Finding("warning", f"{item.id}: no evidence available for trend support"))

    return items, findings


def _dominant_scope(items: list[Any]) -> str:
    if not items:
        return "other"
    counts = Counter(item.impact_scope.value for item in items)
    if len(counts) == 1:
        return next(iter(counts))
    highest = max(items, key=lambda item: item.importance_score)
    return highest.impact_scope.value


def _topic_named_in_text(topic: str, title: str, summary: str) -> bool:
    topic_text = re.sub(r"\s+", " ", topic.strip().lower())
    combined = f"{title} {summary}".lower()
    return topic_text in combined


def _validate_report(report_path: Path, items: list[Any]) -> list[Finding]:
    from src.schemas import DailyInsightReport

    findings: list[Finding] = []
    payload = _load_json(report_path)
    try:
        report = DailyInsightReport.model_validate(payload)
    except ValidationError as exc:
        return _schema_errors("report", exc)

    by_id = {item.id: item for item in items}
    by_topic: dict[str, list[Any]] = defaultdict(list)
    for item in items:
        by_topic[_normalize(item.topic)].append(item)

    topic_counts = Counter(_normalize(item.topic) for item in items)
    expected_topics = [topic for topic, _count in topic_counts.most_common(3)]

    if items and not report.trend_insights:
        findings.append(Finding("error", "report has structured items but no trend_insights"))

    covered_topics: set[str] = set()

    for index, insight in enumerate(report.trend_insights, start=1):
        if insight.trend_state.value not in {
            "new",
            "continuing",
            "heating_up",
            "cooling_down",
            "reversing",
        }:
            findings.append(
                Finding(
                    "error",
                    f"trend_insights[{index}]: invalid trend_state {insight.trend_state.value}",
                )
            )
        if not insight.evidence_item_ids:
            findings.append(Finding("error", f"trend_insights[{index}]: missing evidence_item_ids"))
            continue

        evidence_items: list[Any] = []
        for item_id in insight.evidence_item_ids:
            item = by_id.get(item_id)
            if item is None:
                findings.append(Finding("error", f"trend_insights[{index}]: unknown evidence item {item_id}"))
                continue
            evidence_items.append(item)

        if not evidence_items:
            continue

        evidence_topics = Counter(_normalize(item.topic) for item in evidence_items)
        dominant_topic, dominant_count = evidence_topics.most_common(1)[0]
        covered_topics.add(dominant_topic)

        if len(evidence_topics) > 1:
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: evidence spans multiple topics: {sorted(evidence_topics)}",
                )
            )

        if dominant_count < len(evidence_items):
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: not all evidence belongs to dominant topic {dominant_topic}",
                )
            )

        topic_items = by_topic.get(dominant_topic, [])
        expected_scope = _dominant_scope(evidence_items)
        if insight.scope.value != expected_scope:
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: scope {insight.scope.value} differs from evidence scope {expected_scope}",
                )
            )

        if not _topic_named_in_text(topic_items[0].topic, insight.title, insight.summary):
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: title/summary does not clearly name topic {topic_items[0].topic}",
                )
            )

        if len(topic_items) == 1 and topic_items[0].importance_score < 70:
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: single weak item used as trend evidence for {topic_items[0].topic}",
                )
            )
        state = insight.trend_state.value
        if state in {"continuing", "cooling_down", "reversing"} and not insight.historical_context_used:
            severity = "error" if state == "reversing" else "warning"
            findings.append(
                Finding(
                    severity,
                    f"trend_insights[{index}]: {state} requires historical context or memory signals",
                )
            )
        if (
            state == "heating_up"
            and not insight.historical_context_used
            and len(evidence_items) == 1
            and evidence_items[0].importance_score < 75
        ):
            findings.append(
                Finding(
                    "warning",
                    f"trend_insights[{index}]: heating_up is weakly supported by a single low-score item",
                )
            )

    for topic in expected_topics:
        if topic not in covered_topics:
            findings.append(Finding("warning", f"top topic {topic} is not covered by trend_insights"))

    return findings


def _validate_memory(memory_path: Path, items: list[Any]) -> list[Finding]:
    findings: list[Finding] = []
    payload = _load_json(memory_path)
    if not isinstance(payload, dict):
        return [Finding("error", "memory file must contain a JSON object")]

    topics = payload.get("topics", payload)
    if not isinstance(topics, dict):
        return [Finding("error", "memory topics must be a JSON object")]

    current_topics = {_normalize(item.topic) for item in items}
    memory_topics = {_normalize(str(topic)) for topic in topics}

    for topic in sorted(current_topics):
        if topic not in memory_topics:
            findings.append(Finding("warning", f"current topic {topic} is missing from memory"))

    for topic, entries in topics.items():
        normalized_topic = _normalize(str(topic))
        if not isinstance(entries, list):
            findings.append(Finding("error", f"memory topic {topic} must contain a list"))
            continue
        for entry_index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                findings.append(Finding("error", f"memory topic {topic}[{entry_index}] must be an object"))
                continue
            entry_topic = _normalize(str(entry.get("topic", "")))
            if entry_topic and entry_topic != normalized_topic:
                findings.append(
                    Finding(
                        "warning",
                        f"memory topic key {topic} contains entry topic {entry.get('topic')}",
                    )
                )
            for required in ("id", "title", "published_at", "summary", "importance_score"):
                if required not in entry:
                    findings.append(
                        Finding("warning", f"memory topic {topic}[{entry_index}] missing {required}")
                    )

    return findings


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        stream = sys.stderr if finding.severity == "error" else sys.stdout
        print(f"{finding.severity.upper()}: {finding.message}", file=stream)


def validate(items_path: Path, report_path: Path | None, memory_path: Path | None) -> int:
    _add_repo_to_path()
    items, findings = _validate_items(_load_json(items_path))

    if report_path is not None:
        findings.extend(_validate_report(report_path, items))
    if memory_path is not None:
        findings.extend(_validate_memory(memory_path, items))

    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    _print_findings(findings)

    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1

    topic_count = len({_normalize(item.topic) for item in items})
    print(f"OK: {len(items)} item(s), {topic_count} topic(s), {len(warnings)} warning(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate trend insight consistency for structured news, report sections, and memory.",
    )
    parser.add_argument("structured_json", type=Path, help="StructuredNewsItem JSON object or array")
    parser.add_argument("--report", type=Path, help="Optional DailyInsightReport JSON to cross-check")
    parser.add_argument("--memory", type=Path, help="Optional topic_index.json memory file to check")
    args = parser.parse_args()
    return validate(args.structured_json, args.report, args.memory)


if __name__ == "__main__":
    raise SystemExit(main())
