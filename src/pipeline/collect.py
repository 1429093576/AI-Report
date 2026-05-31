"""Data collection pipeline step."""

from __future__ import annotations

from src.adapters import SourceAdapter, create_source_adapter
from src.harness import PipelineContext, validate_output
from src.schemas import RawNewsItem

from .utils import model_list_payload, path_for, write_json


def run(context: PipelineContext) -> list[RawNewsItem]:
    """Collect raw AI news items from the configured source adapter."""

    raw_path = path_for(context, "raw")
    source_adapter = _source_adapter(context)
    payload = source_adapter.collect()
    errors = getattr(source_adapter, "errors", None)
    if isinstance(errors, list):
        context.set("source_errors", errors)
    metrics = getattr(source_adapter, "source_metrics", None)
    if isinstance(metrics, list):
        context.set("source_metrics", metrics)
    items, result = validate_output(payload, RawNewsItem, context.run_id)

    if not result.is_valid:
        messages = "; ".join(
            f"{issue.item_id or '<unknown>'}.{issue.field}: {issue.message}"
            for issue in result.issues
        )
        raise ValueError(f"raw data validation failed: {messages}")

    write_json(raw_path, model_list_payload(items))
    context.add_artifact("raw", raw_path)
    context.set("raw_items", items)
    context.set("raw_count", len(items))
    return items


def _source_adapter(context: PipelineContext) -> SourceAdapter:
    adapter = context.get("source_adapter")
    if adapter is not None:
        if not isinstance(adapter, SourceAdapter):
            raise TypeError("source_adapter must implement SourceAdapter")
        return adapter

    mode = context.config.get("mode", {})
    source_mode = mode.get("source", "local_json") if isinstance(mode, dict) else "local_json"
    return create_source_adapter(context.config)
