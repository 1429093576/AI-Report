#!/usr/bin/env python3
"""Validate risk/opportunity consistency for Daily AI Insight Engine outputs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError


RISK_TERMS = {
    "risk",
    "safety",
    "security",
    "vulnerability",
    "vulnerabilities",
    "breach",
    "misuse",
    "abuse",
    "lawsuit",
    "complaint",
    "regulation",
    "regulatory",
    "governance",
    "compliance",
    "privacy",
    "cyber",
    "scrutiny",
    "weakness",
    "weaknesses",
}

OPPORTUNITY_TERMS = {
    "agent",
    "agents",
    "developer",
    "developers",
    "enterprise",
    "customer",
    "customers",
    "product",
    "platform",
    "workflow",
    "workflows",
    "model",
    "models",
    "release",
    "launch",
    "partnership",
    "partner",
    "open source",
    "gpu",
    "cloud",
    "productivity",
    "deployment",
    "adoption",
}


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


def _combined_text(item: Any) -> str:
    parts = [
        item.title,
        item.summary,
        " ".join(item.key_points),
        " ".join(item.evidence),
        item.event_type.value,
        item.impact_scope.value,
    ]
    return " ".join(parts).lower()


def _has_term(text: str, terms: set[str]) -> bool:
    return any(term in text for term in terms)


def _schema_errors(index: int, exc: ValidationError) -> list[Finding]:
    findings: list[Finding] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        message = error.get("msg", "validation error")
        findings.append(Finding("error", f"item {index} schema {location}: {message}"))
    return findings


def _validate_items(payload: Any) -> tuple[list[Any], list[Finding]]:
    from src.schemas import StructuredNewsItem

    items: list[Any] = []
    findings: list[Finding] = []

    for index, raw_item in enumerate(_as_list(payload, "structured news"), start=1):
        try:
            item = StructuredNewsItem.model_validate(raw_item)
        except ValidationError as exc:
            findings.extend(_schema_errors(index, exc))
            continue

        items.append(item)
        text = _combined_text(item)
        evidence_text = " ".join(item.evidence).strip().lower()

        if item.risk_level.value in {"medium", "high"} and not item.evidence:
            findings.append(Finding("error", f"{item.id}: {item.risk_level.value} risk has no evidence"))
        if item.opportunity_level.value in {"medium", "high"} and not item.evidence:
            findings.append(
                Finding("error", f"{item.id}: {item.opportunity_level.value} opportunity has no evidence")
            )

        if item.risk_level.value == "high" and not _has_term(text, RISK_TERMS):
            findings.append(Finding("warning", f"{item.id}: high risk has no obvious risk signal"))
        if item.risk_level.value == "medium" and not _has_term(text, RISK_TERMS):
            findings.append(Finding("warning", f"{item.id}: medium risk has no obvious risk signal"))
        if item.opportunity_level.value == "high" and not _has_term(text, OPPORTUNITY_TERMS):
            findings.append(Finding("warning", f"{item.id}: high opportunity has no obvious opportunity signal"))

        if item.event_type.value == "security" and item.risk_level.value == "low":
            findings.append(Finding("warning", f"{item.id}: security event is marked low risk"))
        if item.event_type.value == "policy" and item.risk_level.value == "low":
            findings.append(Finding("warning", f"{item.id}: policy event is marked low risk"))
        if item.risk_level.value == "high" and item.sentiment.value == "positive":
            findings.append(Finding("warning", f"{item.id}: high risk paired with positive sentiment"))
        if (
            item.risk_level.value == "high"
            and item.opportunity_level.value == "high"
            and item.sentiment.value != "mixed"
        ):
            findings.append(Finding("warning", f"{item.id}: high risk and high opportunity should usually be mixed"))

        if item.risk_level.value in {"medium", "high"} and not _has_term(evidence_text, RISK_TERMS):
            findings.append(Finding("warning", f"{item.id}: risk evidence has no obvious risk term"))

    return items, findings


def _validate_report(report_path: Path, items: list[Any]) -> list[Finding]:
    from src.schemas import DailyInsightReport

    findings: list[Finding] = []
    payload = _load_json(report_path)
    try:
        report = DailyInsightReport.model_validate(payload)
    except ValidationError as exc:
        return _schema_errors(0, exc)

    by_id = {item.id: item for item in items}

    for index, insight in enumerate(report.risk_insights, start=1):
        if not insight.evidence_item_ids:
            findings.append(Finding("error", f"risk_insights[{index}]: missing evidence_item_ids"))
            continue
        for item_id in insight.evidence_item_ids:
            item = by_id.get(item_id)
            if item is None:
                findings.append(Finding("error", f"risk_insights[{index}]: unknown evidence item {item_id}"))
                continue
            if insight.level.value in {"medium", "high"} and item.risk_level.value not in {"medium", "high"}:
                findings.append(
                    Finding(
                        "error",
                        f"risk_insights[{index}]: {insight.level.value} insight references {item_id} with {item.risk_level.value} risk",
                    )
                )

    for index, insight in enumerate(report.opportunity_insights, start=1):
        if not insight.evidence_item_ids:
            findings.append(Finding("error", f"opportunity_insights[{index}]: missing evidence_item_ids"))
            continue
        for item_id in insight.evidence_item_ids:
            item = by_id.get(item_id)
            if item is None:
                findings.append(Finding("error", f"opportunity_insights[{index}]: unknown evidence item {item_id}"))
                continue
            if insight.level.value in {"medium", "high"} and item.opportunity_level.value not in {"medium", "high"}:
                findings.append(
                    Finding(
                        "error",
                        f"opportunity_insights[{index}]: {insight.level.value} insight references {item_id} with {item.opportunity_level.value} opportunity",
                    )
                )

    return findings


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        stream = sys.stderr if finding.severity == "error" else sys.stdout
        print(f"{finding.severity.upper()}: {finding.message}", file=stream)


def validate(items_path: Path, report_path: Path | None) -> int:
    _add_repo_to_path()
    items, findings = _validate_items(_load_json(items_path))

    if report_path is not None:
        findings.extend(_validate_report(report_path, items))

    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    _print_findings(findings)

    if errors:
        print(f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)", file=sys.stderr)
        return 1

    print(f"OK: {len(items)} item(s) checked, {len(warnings)} warning(s)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate risk/opportunity consistency for structured news and report sections.",
    )
    parser.add_argument("structured_json", type=Path, help="StructuredNewsItem JSON object or array")
    parser.add_argument("--report", type=Path, help="Optional DailyInsightReport JSON to cross-check")
    args = parser.parse_args()
    return validate(args.structured_json, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
