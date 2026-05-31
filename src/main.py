"""Command line entry point for the daily AI insight pipeline."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from hooks import on_error, post_relevance, post_validate, pre_process
from src.harness.memory_runtime import configure_memory_replay_snapshot
from src.harness import (
    Checkpointer,
    HookRegistry,
    JsonlTracer,
    PipelineContext,
    PipelineRunner,
    RunStore,
)
from src.pipeline import analyze, clean, collect, extract, generate_report, relevance, validate, visualize
from src.pipeline.utils import DEFAULT_PATHS, report_timezone

ReplayFrom = str
StepPlan = list[tuple[str, Any] | tuple[str, Any, str | None]]

REPLAY_CHOICES = ("raw", "relevant", "validated")
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def main(argv: list[str] | None = None) -> None:
    """Run the end-to-end daily insight pipeline."""

    load_dotenv()
    args = _parse_args(argv)
    config = _load_config(Path("config/pipeline.yaml"))
    if args.run_date:
        config["run_date"] = args.run_date
    paths = _configured_paths(config)
    run_store = RunStore()
    run_date = _resolve_run_date(config)
    if args.replay_run_id:
        run_date = _resolve_replay_run_date(run_store, args.replay_run_id, config)

    context_kwargs: dict[str, Any] = {
        "run_date": run_date,
        "config": config,
        "paths": paths,
    }
    if args.run_id:
        context_kwargs["run_id"] = args.run_id
    context = PipelineContext(**context_kwargs)
    hooks = HookRegistry()
    hooks.register("pre_analyze", pre_process.run)
    hooks.register("post_validate", post_validate.run)
    hooks.register("on_error", on_error.run)

    plan = _step_plan(args.replay_from)
    run_mode = _run_mode(args.replay_from)
    replay_inputs = _prepare_replay_inputs(
        run_store,
        context,
        parent_run_id=args.replay_run_id,
        replay_from=args.replay_from,
    )
    if replay_inputs:
        context.set("replay_inputs", replay_inputs)

    runner = PipelineRunner(
        context=context,
        tracer=JsonlTracer(context.paths.get("trace", Path(DEFAULT_PATHS["trace"]))),
        hooks=hooks,
        run_store=run_store,
        checkpointer=Checkpointer(Path("state") / "runs" / context.run_id / "checkpoints"),
        run_mode=run_mode,
        parent_run_id=args.replay_run_id,
        resume_from=args.replay_from,
    )
    runner.run(plan)
    report_path = runner.context.artifacts.get("daily_report", context.paths["daily_report"])
    print(f"Daily insight pipeline complete: {report_path}")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily AI insight pipeline.")
    parser.add_argument(
        "--replay-run-id",
        help="Historical run_id to replay or resume from.",
    )
    parser.add_argument(
        "--from",
        dest="replay_from",
        choices=REPLAY_CHOICES,
        help="Replay/resume boundary: raw, relevant, or validated.",
    )
    parser.add_argument(
        "--run-date",
        help="Report date to run, in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--run-id",
        help="Optional explicit run_id for external orchestration.",
    )
    args = parser.parse_args(argv)
    if bool(args.replay_run_id) != bool(args.replay_from):
        parser.error("--replay-run-id and --from must be provided together")
    if args.run_id:
        run_id = args.run_id.strip()
        if not RUN_ID_PATTERN.fullmatch(run_id):
            parser.error("--run-id may only contain letters, numbers, underscores, and hyphens")
        args.run_id = run_id
    return args


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"paths": DEFAULT_PATHS}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("config/pipeline.yaml must contain a mapping")
    return payload


def _configured_paths(config: dict[str, Any]) -> dict[str, Path]:
    configured = dict(DEFAULT_PATHS)
    paths = config.get("paths", {})
    if isinstance(paths, dict):
        configured.update({name: str(path) for name, path in paths.items()})
    return {name: Path(path) for name, path in configured.items()}


def _resolve_run_date(config: dict[str, Any]) -> date:
    configured = config.get("run_date")
    if configured not in (None, ""):
        return date.fromisoformat(str(configured))

    timezone_info = report_timezone(PipelineContext(config=config))
    return datetime.now(timezone.utc).astimezone(timezone_info).date()


def _resolve_replay_run_date(
    run_store: RunStore,
    parent_run_id: str,
    config: dict[str, Any],
) -> date:
    configured = config.get("run_date")
    if configured not in (None, ""):
        return date.fromisoformat(str(configured))

    manifest = run_store.load_manifest(parent_run_id)
    return date.fromisoformat(str(manifest["run_date"]))


def _run_mode(replay_from: ReplayFrom | None) -> str:
    if replay_from == "raw":
        return "replay"
    if replay_from in {"relevant", "validated"}:
        return "resume"
    return "fresh"


def _prepare_replay_inputs(
    run_store: RunStore,
    context: PipelineContext,
    *,
    parent_run_id: str | None,
    replay_from: ReplayFrom | None,
) -> dict[str, Any]:
    if parent_run_id is None or replay_from is None:
        return {}

    replay_inputs: dict[str, Any] = {}
    target_path = context.paths[replay_from]
    restored = run_store.restore_snapshot(parent_run_id, replay_from, target_path)
    context.add_artifact(replay_from, target_path)
    if replay_from == "validated":
        context.set("validated_items", json.loads(target_path.read_text(encoding="utf-8")))
    replay_inputs[replay_from] = restored
    replay_inputs["memory"] = _configure_replay_memory_snapshot(
        run_store,
        context,
        parent_run_id,
    )
    return replay_inputs


def _configure_replay_memory_snapshot(
    run_store: RunStore,
    context: PipelineContext,
    parent_run_id: str,
) -> dict[str, str | None]:
    try:
        memory_path = run_store.snapshot_path(parent_run_id, "memory")
    except FileNotFoundError:
        return configure_memory_replay_snapshot(
            context,
            source_run_id=parent_run_id,
            memory_path=None,
            items_dir=None,
        )

    try:
        items_dir = run_store.snapshot_path(parent_run_id, "memory_items")
    except FileNotFoundError:
        items_dir = memory_path.parent / "memory_items"
    return configure_memory_replay_snapshot(
        context,
        source_run_id=parent_run_id,
        memory_path=memory_path,
        items_dir=items_dir if items_dir.exists() else None,
    )


def _step_plan(replay_from: ReplayFrom | None) -> StepPlan:
    if replay_from == "raw":
        return [
            ("clean", clean.run),
            ("relevance", relevance.run),
            ("memory_dedupe", post_relevance.run),
            ("extract", extract.run),
            ("validate", validate.run, "pre_analyze"),
            ("visualize", visualize.run),
            ("analyze", analyze.run),
            ("generate_report", generate_report.run, "post_validate"),
        ]
    if replay_from == "relevant":
        return [
            ("extract", extract.run),
            ("validate", validate.run, "pre_analyze"),
            ("visualize", visualize.run),
            ("analyze", analyze.run),
            ("generate_report", generate_report.run, "post_validate"),
        ]
    if replay_from == "validated":
        return [
            ("visualize", visualize.run, "pre_analyze"),
            ("analyze", analyze.run),
            ("generate_report", generate_report.run, "post_validate"),
        ]
    return [
        ("collect", collect.run),
        ("clean", clean.run),
        ("relevance", relevance.run),
        ("memory_dedupe", post_relevance.run),
        ("extract", extract.run),
        ("validate", validate.run, "pre_analyze"),
        ("visualize", visualize.run),
        ("analyze", analyze.run),
        ("generate_report", generate_report.run, "post_validate"),
    ]


if __name__ == "__main__":
    main()
