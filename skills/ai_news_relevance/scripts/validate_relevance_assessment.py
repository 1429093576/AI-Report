"""Validate AI news relevance assessment JSON against project schemas."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import ValidationError

from src.schemas import RelevanceAssessment


def validate(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else [payload]

    errors = 0
    for index, item in enumerate(items):
        try:
            RelevanceAssessment.model_validate(item)
        except ValidationError as exc:
            errors += 1
            print(f"[{index}] invalid relevance assessment")
            print(exc)

    if errors:
        print(f"{errors} invalid relevance assessment(s)")
        return 1

    print(f"validated {len(items)} relevance assessment(s)")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python skills/ai_news_relevance/scripts/validate_relevance_assessment.py <path>")
        return 2

    return validate(Path(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
