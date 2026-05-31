"""Manual smoke test for the real LLM-backed pipeline path."""

from __future__ import annotations

import copy
import argparse
import sys
import tempfile
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.adapters import MockLLMAdapter, create_llm_adapter  # noqa: E402
from src.harness import PipelineContext  # noqa: E402
from src.pipeline import analyze, clean, collect, extract, relevance, validate  # noqa: E402
from src.pipeline.utils import DEFAULT_PATHS, report_timezone, write_json  # noqa: E402


def main() -> None:
    """Run relevance, extract, validate, and analyze with a real LLM."""

    args = _parse_args()
    _load_env()
    config = _load_config(PROJECT_ROOT / "config" / "pipeline.yaml")
    run_date = _resolve_run_date(config, args.run_date)
    adapter = create_llm_adapter(config)
    if isinstance(adapter, MockLLMAdapter):
        raise SystemExit(
            "LLM smoke test requires a real LLM_API_KEY. "
            "Set it in the environment or local .env/.env.example before running."
        )

    try:
        with tempfile.TemporaryDirectory(prefix="daily_ai_llm_smoke_") as tmp_dir:
            result = _run_smoke(config, adapter, Path(tmp_dir), run_date)
    except Exception as exc:
        raise SystemExit(f"LLM smoke test failed: {exc}") from exc

    _print_result(result)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real LLM smoke test.")
    parser.add_argument(
        "--run-date",
        help="Optional report date in YYYY-MM-DD. Defaults to today in report_timezone.",
    )
    return parser.parse_args()


def _load_env() -> None:
    """Load local LLM environment values without overriding real env vars."""

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)
        return

    example_path = PROJECT_ROOT / ".env.example"
    if example_path.exists():
        load_dotenv(example_path, override=False)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"paths": DEFAULT_PATHS}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a mapping")
    return payload


def _resolve_run_date(config: dict[str, Any], configured: str | None) -> date:
    if configured:
        return date.fromisoformat(configured)

    timezone_info = report_timezone(PipelineContext(config=config))
    return datetime.now(timezone.utc).astimezone(timezone_info).date()


def _run_smoke(
    source_config: dict[str, Any],
    adapter: Any,
    temp_root: Path,
    run_date: date,
) -> dict[str, Any]:
    config = copy.deepcopy(source_config)
    config.setdefault("mode", {})["llm"] = "auto"
    config.setdefault("mode", {})["source"] = "local_json"
    config.setdefault("pipeline", {})["batch_size"] = 1
    config["paths"] = _temp_path_config(temp_root)
    config["prompts"] = {
        "extract_schema": str(PROJECT_ROOT / "prompts" / "extract_schema.md"),
        "analyze_daily_report": str(PROJECT_ROOT / "prompts" / "analyze_daily_report.md"),
    }

    paths = {name: Path(path) for name, path in config["paths"].items()}
    write_json(paths["raw"], [_sample_raw_item(run_date, config)])

    context = PipelineContext(
        run_id="run-llm-smoke",
        run_date=run_date,
        config=config,
        paths=paths,
    )
    context.set("llm_adapter", adapter)

    collect.run(context)
    clean.run(context)
    relevance.run(context)
    if context.get("relevance_mode") != "llm":
        raise RuntimeError("relevance did not use the real LLM adapter")

    extract.run(context)
    if context.get("extract_mode") != "llm":
        raise RuntimeError("extract did not use the real LLM adapter")

    validated_items = validate.run(context)
    report = analyze.run(context)
    if context.get("analyze_mode") != "llm":
        raise RuntimeError("analyze did not use the real LLM adapter")

    return {
        "validated_count": len(validated_items),
        "report_title": report.title,
        "extract_calls": context.get("extract_llm_calls", []),
        "analyze_call": context.get("analyze_llm_call", {}),
    }


def _temp_path_config(temp_root: Path) -> dict[str, str]:
    return {
        name: str(temp_root / relative_path)
        for name, relative_path in DEFAULT_PATHS.items()
    }


def _sample_raw_item(run_date: date, config: dict[str, Any]) -> dict[str, Any]:
    timezone_info = report_timezone(PipelineContext(run_date=run_date, config=config))
    published_at = datetime.combine(
        run_date,
        time(hour=9),
        tzinfo=timezone_info,
    ).isoformat()
    return {
        "id": "raw-smoke-001",
        "title": "OpenAI introduces a developer agent workflow for enterprise AI teams",
        "source": "OpenAI News",
        "url": "https://example.com/openai-developer-agent-smoke",
        "published_at": published_at,
        "source_type": "news",
        "language": "en",
        "summary": (
            "OpenAI announced a developer agent workflow designed to help "
            "enterprise teams plan, modify, and review software projects."
        ),
        "content": (
            "The update focuses on AI agents for developers, with safeguards for "
            "review, source control, and enterprise deployment. OpenAI said the "
            "workflow is intended to improve developer productivity while keeping "
            "human approval in the loop."
        ),
        "metadata": {"smoke_test": True},
    }


def _print_result(result: dict[str, Any]) -> None:
    print("LLM smoke test succeeded.")
    print(f"Validated items: {result['validated_count']}")
    print(f"Report title: {result['report_title']}")

    for call in result["extract_calls"]:
        _print_call(f"extract batch {call.get('batch_index')}", call)
    _print_call("analyze", result["analyze_call"])


def _print_call(label: str, call: dict[str, Any]) -> None:
    print(
        f"{label}: model={call.get('model')} "
        f"success={call.get('success')} "
        f"tokens={call.get('total_tokens')} "
        f"prompt={call.get('prompt_tokens')} "
        f"completion={call.get('completion_tokens')} "
        f"elapsed_ms={call.get('elapsed_ms')} "
        f"error={call.get('error')}"
    )


if __name__ == "__main__":
    main()
