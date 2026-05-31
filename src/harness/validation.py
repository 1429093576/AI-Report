"""Harness-level validation helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import ValidationError

from src.schemas import (
    SchemaBase,
    ValidationIssue,
    ValidationResult,
)


ModelT = TypeVar("ModelT", bound=SchemaBase)


def validate_output(
    payload: dict[str, Any] | SchemaBase | Iterable[dict[str, Any] | SchemaBase],
    model_type: type[ModelT],
    run_id: str,
) -> tuple[list[ModelT], ValidationResult]:
    """Validate pipeline output against a schema model.

    Invalid items are converted into ``ValidationIssue`` entries instead of
    raising immediately, letting the runner decide whether to stop downstream
    execution based on the returned ``ValidationResult``.
    """

    if not issubclass(model_type, SchemaBase):
        raise TypeError("model_type must be a SchemaBase subclass")

    items = _coerce_items(payload)
    valid_items: list[ModelT] = []
    issues: list[ValidationIssue] = []

    for index, item in enumerate(items):
        item_id = _extract_item_id(item)
        try:
            valid_items.append(model_type.model_validate(item))
        except ValidationError as error:
            issues.extend(_validation_error_issues(error, index, item_id))

    result = ValidationResult(
        run_id=run_id,
        checked_at=datetime.now(timezone.utc),
        total_items=len(items),
        valid_items=len(valid_items),
        issues=issues,
    )
    return valid_items, result


def _coerce_items(
    payload: dict[str, Any] | SchemaBase | Iterable[dict[str, Any] | SchemaBase],
) -> list[dict[str, Any] | SchemaBase]:
    if isinstance(payload, dict | SchemaBase):
        return [payload]
    if isinstance(payload, str | bytes):
        raise TypeError("payload must be a dict, schema model, or iterable of them")

    try:
        return list(payload)
    except TypeError as error:
        raise TypeError(
            "payload must be a dict, schema model, or iterable of them"
        ) from error


def _extract_item_id(item: dict[str, Any] | SchemaBase) -> str:
    if isinstance(item, SchemaBase):
        value = getattr(item, "id", "")
    elif isinstance(item, dict):
        value = item.get("id", "")
    else:
        value = ""
    return str(value).strip() if value is not None else ""


def _validation_error_issues(
    error: ValidationError,
    index: int,
    item_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail.get("loc", ()))
        message = str(detail.get("msg", "Validation failed"))
        code = str(detail.get("type", "validation_error"))
        issues.append(
            ValidationIssue(
                severity="error",
                message=message,
                item_id=item_id,
                field=location,
                code=code,
                details={"index": index},
            )
        )
    return issues
