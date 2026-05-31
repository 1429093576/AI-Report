"""File checkpoint and rollback support for pipeline steps."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import PipelineContext


STEP_TARGETS: dict[str, tuple[str, ...]] = {
    "collect": ("raw",),
    "clean": ("cleaned",),
    "relevance": ("relevant", "relevance_report"),
    "memory_dedupe": ("relevant",),
    "extract": ("structured",),
    "validate": ("validated", "validation_report"),
    "visualize": ("charts_dir",),
    "analyze": ("report_sections",),
    "generate_report": ("daily_report", "memory"),
}

CHECKPOINT_NAMES = {
    "raw": "raw.json",
    "cleaned": "cleaned.json",
    "relevant": "relevant.json",
    "structured": "structured.json",
    "validated": "validated.json",
    "relevance_report": "relevance_report.json",
    "memory_report": "memory_report.json",
    "validation_report": "validation_report.json",
    "llm_audit_report": "llm_audit_report.json",
    "report_sections": "report_sections.json",
    "daily_report": "daily_report.md",
    "memory": "memory_topic_index.json",
    "charts_dir": "charts",
}


@dataclass(frozen=True)
class CheckpointEntry:
    """One file or directory protected before a step runs."""

    key: str
    latest_path: Path
    checkpoint_path: Path | None
    existed: bool
    was_directory: bool

    def to_manifest(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "latest_path": self.latest_path.as_posix(),
            "checkpoint_path": (
                self.checkpoint_path.as_posix() if self.checkpoint_path else None
            ),
            "existed": self.existed,
            "was_directory": self.was_directory,
        }


@dataclass(frozen=True)
class Checkpoint:
    """Checkpoint metadata for one pipeline step."""

    step_name: str
    checkpoint_dir: Path
    entries: tuple[CheckpointEntry, ...]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "checkpoint_dir": self.checkpoint_dir.as_posix(),
            "entries": [entry.to_manifest() for entry in self.entries],
        }


class Checkpointer:
    """Create and restore pre-step file checkpoints."""

    def __init__(
        self,
        root: str | Path = "state/checkpoints",
        step_targets: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.step_targets = dict(step_targets or STEP_TARGETS)

    def create(self, step_name: str, context: PipelineContext) -> Checkpoint:
        """Snapshot files that the step may overwrite."""

        checkpoint_dir = self.root / step_name
        if checkpoint_dir.exists():
            shutil.rmtree(checkpoint_dir)
        checkpoint_dir.mkdir(parents=True, exist_ok=True)

        entries: list[CheckpointEntry] = []
        for key in self.step_targets.get(step_name, ()):
            latest_path = _resolve_context_path(context, key)
            if latest_path is None:
                continue

            existed = latest_path.exists()
            was_directory = latest_path.is_dir() if existed else key.endswith("_dir")
            checkpoint_path = (
                checkpoint_dir / _checkpoint_name(key, latest_path) if existed else None
            )
            if existed and checkpoint_path is not None:
                _copy_path(latest_path, checkpoint_path)

            entries.append(
                CheckpointEntry(
                    key=key,
                    latest_path=latest_path,
                    checkpoint_path=checkpoint_path,
                    existed=existed,
                    was_directory=was_directory,
                )
            )

        checkpoint = Checkpoint(
            step_name=step_name,
            checkpoint_dir=checkpoint_dir,
            entries=tuple(entries),
        )
        _write_json(checkpoint_dir / "manifest.json", checkpoint.to_manifest())
        return checkpoint

    def rollback(self, checkpoint: Checkpoint) -> dict[str, Any]:
        """Restore files from a checkpoint and remove new files created by a step."""

        results: list[dict[str, Any]] = []
        success = True

        for entry in checkpoint.entries:
            try:
                if entry.existed:
                    if entry.checkpoint_path is None or not entry.checkpoint_path.exists():
                        raise FileNotFoundError(
                            f"missing checkpoint for {entry.latest_path.as_posix()}"
                        )
                    _remove_path(entry.latest_path)
                    entry.latest_path.parent.mkdir(parents=True, exist_ok=True)
                    _copy_path(entry.checkpoint_path, entry.latest_path)
                    action = "restored"
                else:
                    if entry.latest_path.exists():
                        _remove_path(entry.latest_path)
                        action = "removed"
                    else:
                        action = "not_present"

                results.append(
                    {
                        "key": entry.key,
                        "latest_path": entry.latest_path.as_posix(),
                        "action": action,
                        "status": "succeeded",
                    }
                )
            except Exception as error:  # pragma: no cover - rare filesystem failure
                success = False
                results.append(
                    {
                        "key": entry.key,
                        "latest_path": entry.latest_path.as_posix(),
                        "action": "rollback",
                        "status": "failed",
                        "error": {
                            "type": error.__class__.__name__,
                            "message": str(error),
                        },
                    }
                )

        payload = {
            "step_name": checkpoint.step_name,
            "checkpoint_dir": checkpoint.checkpoint_dir.as_posix(),
            "status": "succeeded" if success else "failed",
            "finished_at": _utc_now_iso(),
            "entries": results,
        }
        _write_json(checkpoint.checkpoint_dir / "rollback.json", payload)
        return payload


def _resolve_context_path(context: PipelineContext, key: str) -> Path | None:
    if key == "memory":
        memory_config = context.config.get("memory", {})
        if isinstance(memory_config, dict) and memory_config.get("path"):
            return Path(str(memory_config["path"]))

    path = context.paths.get(key)
    if path is not None:
        return path

    artifact = context.artifacts.get(key)
    if artifact is not None:
        return artifact

    return None


def _checkpoint_name(key: str, path: Path) -> str:
    configured = CHECKPOINT_NAMES.get(key)
    if configured:
        return configured
    suffix = path.suffix or ".artifact"
    return f"{key}{suffix}"


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        if destination.exists():
            _remove_path(destination)
        shutil.copytree(source, destination)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
