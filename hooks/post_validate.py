"""Post-validation hook for the pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.harness import (
    MemoryManager,
    PipelineContext,
    add_memory_report_stage,
    add_memory_report_warning,
    new_memory_report,
    write_memory_report,
)
from src.harness.memory_runtime import (
    RUN_MODE_FRESH,
    configured_memory_items_dir,
    configured_memory_path,
    memory_config_dict,
    run_mode,
)


def run(context: PipelineContext) -> PipelineContext:
    """Persist validated structured items into topic-indexed memory."""

    items = _validated_items(context)
    report_path = _memory_report_path(context)
    memory_path = _memory_path(context)
    if not items:
        context.set("memory_items_added", 0)
        return context

    if run_mode(context) != RUN_MODE_FRESH:
        context.set("memory_items_added", 0)
        _write_skipped_memory_report(
            context,
            memory_path=memory_path,
            report_path=report_path,
            input_count=len(items),
            reason="non_fresh_run",
        )
        return context

    if not _daily_report_succeeded(context):
        context.set("memory_items_added", 0)
        _write_skipped_memory_report(
            context,
            memory_path=memory_path,
            report_path=report_path,
            input_count=len(items),
            reason="daily_report_missing",
        )
        return context

    memory = MemoryManager(memory_path, items_dir=_memory_items_dir(context, memory_path))
    before = _existing_memory_item_paths(memory.items_dir)
    added = memory.append(
        items,
        clean_items=_clean_items(context),
        relevant_items=_relevant_items(context),
        run_id=context.run_id,
        run_date=context.run_date.isoformat(),
        artifact_paths=_source_artifact_paths(context),
    )
    after = _existing_memory_item_paths(memory.items_dir)
    written_paths = sorted(after - before)
    context.set("memory_items_added", added)
    context.add_artifact("memory", memory_path)
    _write_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
        added=added,
        input_count=len(items),
        written_paths=written_paths,
    )
    return context


def _validated_items(context: PipelineContext) -> list[Any]:
    candidates = context.get("validated_items", context.get("validate", []))
    if candidates is None:
        return []
    return list(candidates)


def _clean_items(context: PipelineContext) -> list[Any]:
    return _items_from_context_or_file(context, "cleaned_items", "cleaned")


def _relevant_items(context: PipelineContext) -> list[Any]:
    return _items_from_context_or_file(context, "relevant_items", "relevant")


def _items_from_context_or_file(
    context: PipelineContext,
    state_key: str,
    path_key: str,
) -> list[Any]:
    items = context.get(state_key)
    if items is not None:
        return list(items)

    path = context.artifacts.get(path_key, context.paths.get(path_key))
    if path is None or not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        return []
    return payload


def _memory_path(context: PipelineContext) -> Path:
    return configured_memory_path(context)


def _memory_items_dir(context: PipelineContext, memory_path: Path) -> Path:
    return configured_memory_items_dir(context, memory_path)


def _memory_report_path(context: PipelineContext) -> Path:
    memory_config = memory_config_dict(context)
    if memory_config.get("report_path"):
        return Path(str(memory_config["report_path"]))
    return context.paths.get("memory_report", Path("logs/memory_report.json"))


def _source_artifact_paths(context: PipelineContext) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in ("raw", "cleaned", "relevant", "structured", "validated"):
        path = context.artifacts.get(name, context.paths.get(name))
        if path is not None:
            paths[name] = path
    return paths


def _existing_memory_item_paths(items_dir: Path) -> set[str]:
    if not items_dir.exists():
        return set()
    return {
        path.as_posix()
        for path in items_dir.glob("*.json")
        if path.is_file()
    }


def _write_memory_report(
    context: PipelineContext,
    *,
    memory_path: Path,
    report_path: Path,
    added: int,
    input_count: int,
    written_paths: list[str],
) -> None:
    report = _load_or_create_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
    )
    report["memory_write"] = {
        "status": "succeeded",
        "attempted": input_count > 0,
        "input_count": input_count,
        "added_count": added,
        "skipped_count": max(input_count - added, 0),
        "memory_item_paths": written_paths,
        "skipped_reasons": [],
    }
    add_memory_report_stage(
        report,
        "post_validate_memory_write",
        details={
            "input_count": input_count,
            "added_count": added,
            "skipped_count": max(input_count - added, 0),
        },
    )
    write_memory_report(report_path, report)
    context.add_artifact("memory_report", report_path)
    context.set("memory_report", report)


def _write_skipped_memory_report(
    context: PipelineContext,
    *,
    memory_path: Path,
    report_path: Path,
    input_count: int,
    reason: str,
) -> None:
    report = _load_or_create_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
    )
    report["memory_write"] = {
        "status": "skipped",
        "attempted": False,
        "input_count": input_count,
        "added_count": 0,
        "skipped_count": input_count,
        "memory_item_paths": [],
        "skipped_reasons": [reason],
    }
    add_memory_report_warning(
        report,
        "memory_write_skipped",
        _skip_message(reason),
    )
    add_memory_report_stage(
        report,
        "post_validate_memory_write",
        status="skipped",
        details={
            "input_count": input_count,
            "added_count": 0,
            "skipped_count": input_count,
            "reason": reason,
            "run_mode": run_mode(context),
        },
    )
    write_memory_report(report_path, report)
    context.add_artifact("memory_report", report_path)
    context.set("memory_report", report)


def _daily_report_succeeded(context: PipelineContext) -> bool:
    path = context.artifacts.get("daily_report")
    return path is not None and path.exists()


def _skip_message(reason: str) -> str:
    if reason == "non_fresh_run":
        return "Memory write skipped because replay/resume runs do not update latest Memory by default."
    if reason == "daily_report_missing":
        return "Memory write skipped because generate_report has not produced a daily report artifact."
    return f"Memory write skipped: {reason}."


def _load_or_create_memory_report(
    context: PipelineContext,
    *,
    memory_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            payload.setdefault("paths", {})
            if isinstance(payload["paths"], dict):
                payload["paths"].setdefault("memory", memory_path.as_posix())
                payload["paths"].setdefault("memory_report", report_path.as_posix())
            return payload

    return new_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
        relevant_path=context.paths.get("relevant"),
    )
