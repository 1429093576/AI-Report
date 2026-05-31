"""Post-relevance hook for Memory-backed strong duplicate filtering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.harness import (
    MemoryManager,
    PipelineContext,
    add_memory_report_error,
    add_memory_report_stage,
    add_memory_report_warning,
    new_memory_report,
    write_memory_report,
)
from src.harness.memory_runtime import effective_memory_read_path
from src.schemas import CleanNewsItem


def run(context: PipelineContext) -> PipelineContext:
    """Filter AI-relevant items that already exist in local Memory."""

    items = _relevant_items(context)
    memory_path = _memory_path(context)
    report_path = _memory_report_path(context)
    relevant_path = _relevant_path(context)
    memory_exists = memory_path.exists()
    matches = MemoryManager(memory_path).strong_duplicate_matches(items)
    kept_items = [item for item in items if item.id not in matches]
    report = _report(
        context,
        items,
        kept_items,
        matches,
        memory_path=memory_path,
        report_path=report_path,
        relevant_path=relevant_path,
        memory_exists=memory_exists,
    )
    write_memory_report(report_path, report)
    context.add_artifact("memory_report", report_path)
    context.set("memory_report", report)
    context.set("memory_strong_duplicate_count", len(matches))

    if items and not kept_items:
        add_memory_report_error(
            report,
            "all_relevant_items_filtered",
            "All AI-relevant report-date items matched historical Memory strong keys.",
        )
        add_memory_report_stage(
            report,
            "post_relevance_strong_dedupe",
            status="failed",
            details={"reason": "all_relevant_items_filtered"},
        )
        write_memory_report(report_path, report)
        raise ValueError(
            "no non-duplicate AI-relevant report-date news items remain after "
            "memory strong duplicate filtering"
        )

    _write_json(relevant_path, [_item_payload(item) for item in kept_items])
    context.set("relevant_items", kept_items)
    context.set("relevance", kept_items)
    context.set("relevant_count", len(kept_items))
    return context


def _relevant_items(context: PipelineContext) -> list[CleanNewsItem]:
    items = context.get("relevant_items")
    if items is None:
        path = _relevant_path(context)
        if not path.exists():
            raise ValueError("memory strong dedupe requires relevance output")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError(f"{path.as_posix()} must contain a JSON array")
        items = payload
    return [
        item if isinstance(item, CleanNewsItem) else CleanNewsItem.model_validate(item)
        for item in list(items or [])
    ]


def _report(
    context: PipelineContext,
    items: list[CleanNewsItem],
    kept_items: list[CleanNewsItem],
    matches: dict[str, dict[str, Any]],
    *,
    memory_path: Path,
    report_path: Path,
    relevant_path: Path,
    memory_exists: bool,
) -> dict[str, Any]:
    report = new_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
        relevant_path=relevant_path,
    )
    if not memory_exists:
        add_memory_report_warning(
            report,
            "memory_not_found",
            "Memory file was not found; strong duplicate filtering used an empty index.",
        )
    report["strong_dedupe"] = {
        "status": "succeeded",
        "input_count": len(items),
        "kept_count": len(kept_items),
        "filtered_count": len(matches),
        "kept_item_ids": [item.id for item in kept_items],
        "filtered_items": [
            _filtered_item_report(item, matches[item.id])
            for item in items
            if item.id in matches
        ],
    }
    add_memory_report_stage(
        report,
        "post_relevance_strong_dedupe",
        details={
            "input_count": len(items),
            "kept_count": len(kept_items),
            "filtered_count": len(matches),
        },
    )
    return report


def _filtered_item_report(
    item: CleanNewsItem,
    match: dict[str, Any],
) -> dict[str, Any]:
    return {
        "item_id": item.id,
        "title": item.title,
        "url": item.url,
        "content_hash": item.content_hash,
        "matched_keys": match.get("matched_keys", []),
        "memory_entries": match.get("memory_entries", []),
    }


def _item_payload(item: CleanNewsItem) -> dict[str, Any]:
    return item.model_dump(mode="json")


def _memory_path(context: PipelineContext) -> Path:
    return effective_memory_read_path(context)


def _memory_report_path(context: PipelineContext) -> Path:
    memory_config = _memory_config(context)
    if memory_config.get("report_path"):
        return Path(str(memory_config["report_path"]))
    return context.paths.get("memory_report", Path("logs/memory_report.json"))


def _relevant_path(context: PipelineContext) -> Path:
    return context.paths.get("relevant", Path("data/processed/ai_news_relevant.json"))


def _memory_config(context: PipelineContext) -> dict[str, Any]:
    config = context.config.get("memory", {})
    return config if isinstance(config, dict) else {}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
