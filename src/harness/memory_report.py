"""Memory audit report helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import PipelineContext


MEMORY_REPORT_SCHEMA_VERSION = 1


def new_memory_report(
    context: PipelineContext,
    *,
    memory_path: str | Path,
    report_path: str | Path,
    relevant_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build an empty Memory report with stable extension points."""

    paths: dict[str, str] = {
        "memory": Path(memory_path).as_posix(),
        "memory_report": Path(report_path).as_posix(),
    }
    if relevant_path is not None:
        paths["relevant"] = Path(relevant_path).as_posix()

    return {
        "schema_version": MEMORY_REPORT_SCHEMA_VERSION,
        "run_id": context.run_id,
        "run_date": context.run_date.isoformat(),
        "generated_at": _utc_now_iso(),
        "paths": paths,
        "stages": [],
        "strong_dedupe": _empty_strong_dedupe(),
        "soft_similarity": _empty_soft_similarity(),
        "context_retrieval": _empty_context_retrieval(),
        "fulltext_selection": _empty_fulltext_selection(),
        "historical_evidence_selection": _empty_historical_evidence_selection(),
        "memory_write": _empty_memory_write(),
        "warnings": [],
        "errors": [],
    }


def add_stage(
    report: dict[str, Any],
    stage_name: str,
    *,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append a stage record to a Memory report."""

    stages = report.setdefault("stages", [])
    if not isinstance(stages, list):
        report["stages"] = []
        stages = report["stages"]
    stage = {
        "name": stage_name,
        "status": status,
        "finished_at": _utc_now_iso(),
    }
    if details:
        stage["details"] = details
    stages.append(stage)
    return report


def add_warning(report: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    """Append a warning to a Memory report."""

    warnings = report.setdefault("warnings", [])
    if not isinstance(warnings, list):
        report["warnings"] = []
        warnings = report["warnings"]
    warnings.append({"code": code, "message": message})
    return report


def add_error(report: dict[str, Any], code: str, message: str) -> dict[str, Any]:
    """Append an error to a Memory report."""

    errors = report.setdefault("errors", [])
    if not isinstance(errors, list):
        report["errors"] = []
        errors = report["errors"]
    errors.append({"code": code, "message": message})
    return report


def write_memory_report(path: str | Path, report: dict[str, Any]) -> None:
    """Write a Memory report to disk."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _empty_strong_dedupe() -> dict[str, Any]:
    return {
        "status": "not_run",
        "input_count": 0,
        "kept_count": 0,
        "filtered_count": 0,
        "kept_item_ids": [],
        "filtered_items": [],
    }


def _empty_soft_similarity() -> dict[str, Any]:
    return {
        "status": "not_run",
        "candidate_count": 0,
        "match_count": 0,
        "matches": [],
    }


def _empty_context_retrieval() -> dict[str, Any]:
    return {
        "status": "not_run",
        "topics": [],
        "retrieved_count": 0,
        "budget": {},
        "truncated": False,
    }


def _empty_fulltext_selection() -> dict[str, Any]:
    return {
        "status": "not_run",
        "mode": None,
        "selected_count": 0,
        "selected_item_ids": [],
        "budget": {},
        "truncated": False,
    }


def _empty_historical_evidence_selection() -> dict[str, Any]:
    return {
        "status": "not_run",
        "requested_item_ids": [],
        "read_item_ids": [],
        "adopted_count": 0,
        "adopted_items": [],
        "unadopted_items": [],
        "invalid_item_ids": [],
        "invalid_adopted_items": [],
        "fallback_mode": None,
    }


def _empty_memory_write() -> dict[str, Any]:
    return {
        "status": "not_run",
        "attempted": False,
        "input_count": 0,
        "added_count": 0,
        "skipped_count": 0,
        "memory_item_paths": [],
        "skipped_reasons": [],
    }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
