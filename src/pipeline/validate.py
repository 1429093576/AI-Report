"""Structured output validation pipeline step."""

from __future__ import annotations

from src.harness import PipelineContext, audit_structured_evidence, validate_output
from src.schemas import CleanNewsItem, StructuredNewsItem
from src.schemas.validation import ValidationIssue

from .utils import (
    is_on_report_date,
    model_list_payload,
    path_for,
    report_timezone,
    report_timezone_name,
    require_json_list,
    write_llm_audit_report,
    write_json,
)


def run(context: PipelineContext) -> list[StructuredNewsItem]:
    """Validate structured news and write validation artifacts."""

    payload = context.get("structured_items")
    if payload is None:
        payload = require_json_list(path_for(context, "structured"))

    valid_items, result = validate_output(
        payload,
        StructuredNewsItem,
        run_id=context.run_id,
    )
    _assert_rationales(valid_items, result.issues)
    _assert_report_date_items(context, valid_items)
    _assert_supported_evidence(context, valid_items)

    validated_path = path_for(context, "validated")
    report_path = path_for(context, "validation_report")
    write_json(validated_path, model_list_payload(valid_items))
    write_json(report_path, result.model_dump(mode="json"))

    context.add_artifact("validated", validated_path)
    context.add_artifact("validation_report", report_path)
    context.set("validated_items", valid_items)
    context.set("validation_result", result)
    context.set("validated_count", len(valid_items))

    if not result.is_valid:
        messages = "; ".join(
            f"{issue.item_id or '<unknown>'}.{issue.field}: {issue.message}"
            for issue in result.issues
        )
        raise ValueError(f"structured validation failed: {messages}")

    return valid_items


def _assert_rationales(
    items: list[StructuredNewsItem],
    issues: list[ValidationIssue],
) -> None:
    for item in items:
        for field in (
            "importance_rationale",
            "risk_rationale",
            "opportunity_rationale",
        ):
            value = str(getattr(item, field)).strip()
            if _is_generic_rationale(value):
                issues.append(
                    ValidationIssue(
                        severity="error",
                        item_id=item.id,
                        field=field,
                        code="generic_rationale",
                        message=(
                            "rationale must explain the judgment with concrete, "
                            "reader-facing evidence"
                        ),
                    )
                )


def _is_generic_rationale(value: str) -> bool:
    normalized = value.replace(" ", "")
    generic_values = {
        "根据新闻判断",
        "根据材料判断",
        "根据内容判断",
        "依据新闻判断",
        "依据材料判断",
        "信息不足",
        "暂无",
        "无",
    }
    if normalized in generic_values:
        return True
    return len(normalized) < 8


def _assert_supported_evidence(
    context: PipelineContext,
    items: list[StructuredNewsItem],
) -> None:
    sources = _source_items(context)
    if not sources:
        return

    audit = audit_structured_evidence(items, sources, run_id=context.run_id)
    write_llm_audit_report(context, audit)
    blocked = audit.get("blocked_records", [])
    if blocked:
        blocked_ids = ", ".join(str(record.get("item_id")) for record in blocked)
        raise ValueError(
            "structured validation failed: missing supported evidence for "
            f"{blocked_ids}"
        )


def _source_items(context: PipelineContext) -> list[CleanNewsItem]:
    items = context.get("relevant_items")
    if items is None:
        if "relevant" not in context.paths and "relevant" not in context.artifacts:
            return []
        relevant_path = path_for(context, "relevant")
        if relevant_path.exists():
            items = require_json_list(relevant_path)
    if items is None:
        return []

    return [
        item if isinstance(item, CleanNewsItem) else CleanNewsItem.model_validate(item)
        for item in items
    ]


def _assert_report_date_items(
    context: PipelineContext,
    items: list[StructuredNewsItem],
) -> None:
    timezone_info = report_timezone(context)
    current_timezone_name = report_timezone_name(context)
    invalid_items = [
        item.id
        for item in items
        if not is_on_report_date(item.published_at, context.run_date, timezone_info)
    ]
    if invalid_items:
        item_list = ", ".join(invalid_items)
        raise ValueError(
            "structured validation failed: published_at falls outside report date "
            f"{context.run_date.isoformat()} in {current_timezone_name}: {item_list}"
        )
