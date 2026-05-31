#!/usr/bin/env python3
"""Validate StructuredNewsItem JSON files against the project schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError


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


def _items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return [item.get("output", item) if isinstance(item, dict) else item for item in payload]
    if isinstance(payload, dict):
        if isinstance(payload.get("output"), dict):
            return [payload["output"]]
        return [payload]
    raise SystemExit("Input JSON must be an object or an array of objects")


def _format_error(index: int, exc: ValidationError) -> str:
    lines = [f"item {index}: validation failed"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
        message = error.get("msg", "validation error")
        lines.append(f"  - {location}: {message}")
    return "\n".join(lines)


def validate(path: Path) -> int:
    _add_repo_to_path()
    from src.schemas import StructuredNewsItem

    payload = _load_json(path)
    errors: list[str] = []

    for index, item in enumerate(_items(payload), start=1):
        try:
            structured = StructuredNewsItem.model_validate(item)
        except ValidationError as exc:
            errors.append(_format_error(index, exc))
            continue
        for field in (
            "importance_rationale",
            "risk_rationale",
            "opportunity_rationale",
        ):
            value = str(getattr(structured, field)).strip()
            if _is_generic_rationale(value):
                errors.append(
                    f"item {index}: {field} must explain the judgment with concrete evidence"
                )

    if errors:
        print("\n\n".join(errors), file=sys.stderr)
        print(f"FAILED: {len(errors)} item(s) invalid in {path}", file=sys.stderr)
        return 1

    print(f"OK: {len(_items(payload))} item(s) valid in {path}")
    return 0


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
    return normalized in generic_values or len(normalized) < 8


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate StructuredNewsItem JSON against src.schemas.StructuredNewsItem.",
    )
    parser.add_argument("json_path", type=Path, help="JSON object or array to validate")
    args = parser.parse_args()
    return validate(args.json_path)


if __name__ == "__main__":
    raise SystemExit(main())
