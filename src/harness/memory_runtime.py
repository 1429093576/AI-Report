"""Runtime Memory path and lifecycle helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import PipelineContext


MEMORY_REPLAY_SNAPSHOT_KEY = "memory_replay_snapshot"
RUN_MODE_FRESH = "fresh"
RUN_MODE_REPLAY = "replay"
RUN_MODE_RESUME = "resume"


def run_mode(context: PipelineContext) -> str:
    """Return the current run mode, defaulting to a normal fresh run."""

    return str(context.get("run_mode", RUN_MODE_FRESH) or RUN_MODE_FRESH)


def configure_memory_replay_snapshot(
    context: PipelineContext,
    *,
    source_run_id: str,
    memory_path: str | Path | None,
    items_dir: str | Path | None = None,
) -> dict[str, str | None]:
    """Record the parent-run Memory snapshot that replay/resume should read."""

    if memory_path is None:
        payload: dict[str, str | None] = {
            "status": "missing",
            "source_run_id": source_run_id,
            "memory_path": None,
            "items_dir": None,
        }
    else:
        payload = {
            "status": "available",
            "source_run_id": source_run_id,
            "memory_path": Path(memory_path).as_posix(),
            "items_dir": Path(items_dir).as_posix() if items_dir is not None else None,
        }
    context.set(MEMORY_REPLAY_SNAPSHOT_KEY, payload)
    return payload


def effective_memory_read_path(
    context: PipelineContext,
    default_path: str | Path = "memory/topic_index.json",
) -> Path:
    """Return the Memory index path used for reads in this run."""

    snapshot = _available_snapshot(context)
    if snapshot is not None and snapshot.get("memory_path"):
        return Path(str(snapshot["memory_path"]))
    return configured_memory_path(context, default_path=default_path)


def effective_memory_items_dir(
    context: PipelineContext,
    memory_path: str | Path,
) -> Path:
    """Return the Memory items directory paired with the effective read path."""

    snapshot = _available_snapshot(context)
    if snapshot is not None:
        if snapshot.get("items_dir"):
            return Path(str(snapshot["items_dir"]))
        return Path(str(snapshot["memory_path"])).parent / "memory_items"
    return configured_memory_items_dir(context, Path(memory_path))


def configured_memory_path(
    context: PipelineContext,
    default_path: str | Path = "memory/topic_index.json",
) -> Path:
    """Return the latest Memory index path configured as the write target."""

    memory_config = memory_config_dict(context)
    if memory_config.get("path"):
        return Path(str(memory_config["path"]))
    return context.paths.get("memory", Path(default_path))


def configured_memory_items_dir(
    context: PipelineContext,
    memory_path: str | Path,
) -> Path:
    """Return the latest Memory items directory configured as the write target."""

    memory_config = memory_config_dict(context)
    if memory_config.get("items_dir"):
        return Path(str(memory_config["items_dir"]))
    return Path(memory_path).parent / "items"


def memory_config_dict(context: PipelineContext) -> dict[str, Any]:
    """Return Memory config as a mapping."""

    config = context.config.get("memory", {})
    return config if isinstance(config, dict) else {}


def memory_replay_snapshot(context: PipelineContext) -> dict[str, Any]:
    """Return replay/resume Memory snapshot metadata, if any."""

    snapshot = context.get(MEMORY_REPLAY_SNAPSHOT_KEY, {})
    return snapshot if isinstance(snapshot, dict) else {}


def _available_snapshot(context: PipelineContext) -> dict[str, Any] | None:
    snapshot = memory_replay_snapshot(context)
    if snapshot.get("status") == "available" and snapshot.get("memory_path"):
        return snapshot
    return None
