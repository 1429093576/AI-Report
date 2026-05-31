"""Per-run manifest and artifact snapshot storage."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .context import PipelineContext
from .memory_runtime import effective_memory_items_dir, memory_replay_snapshot
from .metrics import build_run_metrics


ARTIFACT_SNAPSHOT_NAMES = {
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
    "topic_distribution_chart": "charts/topic_distribution.png",
    "importance_ranking_chart": "charts/importance_ranking.png",
    "trace": "trace.jsonl",
    "memory": "memory.json",
}


class RunStore:
    """Write run-level manifests and immutable per-run artifact snapshots."""

    def __init__(
        self,
        state_dir: str | Path = "state",
        run_manifest_path: str | Path | None = None,
        latest_metrics_path: str | Path = "logs/metrics.json",
    ) -> None:
        self.state_dir = Path(state_dir)
        self.runs_dir = self.state_dir / "runs"
        self.run_manifest_path = (
            Path(run_manifest_path)
            if run_manifest_path is not None
            else self.state_dir / "run_manifest.json"
        )
        self.latest_metrics_path = Path(latest_metrics_path)

    def start_run(
        self,
        context: PipelineContext,
        *,
        mode: str = "fresh",
        parent_run_id: str | None = None,
        resume_from: str | None = None,
    ) -> None:
        """Initialize manifests and write a config snapshot."""

        run_dir = self.run_dir(context.run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "artifacts").mkdir(exist_ok=True)
        (run_dir / "checkpoints").mkdir(exist_ok=True)

        config_snapshot = run_dir / "config_snapshot.yaml"
        config_snapshot.write_text(
            yaml.safe_dump(_redact_config(context.config), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

        now = _utc_now_iso()
        manifest = {
            "run_id": context.run_id,
            "run_date": context.run_date.isoformat(),
            "report_timezone": _report_timezone(context),
            "mode": mode,
            "parent_run_id": parent_run_id,
            "resume_from": resume_from,
            "status": "running",
            "started_at": now,
            "finished_at": None,
            "config_snapshot": _relative(config_snapshot),
            "latest_paths": {
                name: path.as_posix() for name, path in sorted(context.paths.items())
            },
            "steps": {},
            "artifacts": {},
            "replay_inputs": context.get("replay_inputs", {}),
            "error": None,
            "rollback": None,
        }
        self._write_run_manifest(context.run_id, manifest)
        self._upsert_global_run(
            {
                "run_id": context.run_id,
                "run_date": context.run_date.isoformat(),
                "status": "running",
                "mode": mode,
                "parent_run_id": parent_run_id,
                "resume_from": resume_from,
                "started_at": now,
                "finished_at": None,
                "manifest_path": _relative(self.run_manifest_file(context.run_id)),
            }
        )

    def step_started(
        self,
        context: PipelineContext,
        step_name: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        """Record that a step started."""

        manifest = self._read_run_manifest(context.run_id)
        step = self._step(manifest, step_name)
        step["status"] = "running"
        step["started_at"] = _utc_now_iso()
        step["finished_at"] = None
        step["checkpoint"] = checkpoint
        step["rollback"] = None
        step["error"] = None
        self._write_run_manifest(context.run_id, manifest)

    def step_finished(self, context: PipelineContext, step_name: str) -> None:
        """Snapshot current artifacts and mark a step as succeeded."""

        manifest = self._read_run_manifest(context.run_id)
        snapshot_artifacts = self.snapshot_artifacts(context)
        step = self._step(manifest, step_name)
        step["status"] = "succeeded"
        step["finished_at"] = _utc_now_iso()
        step["latest_artifacts"] = {
            name: path.as_posix() for name, path in sorted(context.artifacts.items())
        }
        step["snapshot_artifacts"] = snapshot_artifacts
        manifest["artifacts"] = snapshot_artifacts
        self._write_run_manifest(context.run_id, manifest)

    def step_failed(
        self,
        context: PipelineContext,
        step_name: str,
        error: Exception,
        rollback: dict[str, Any] | None = None,
    ) -> None:
        """Record step failure details and any rollback result."""

        manifest = self._read_run_manifest(context.run_id)
        error_payload = {
            "type": error.__class__.__name__,
            "message": str(error),
        }
        step = self._step(manifest, step_name)
        step["status"] = "failed"
        step["finished_at"] = _utc_now_iso()
        step["error"] = error_payload
        step["rollback"] = rollback
        step["snapshot_artifacts"] = self.snapshot_artifacts(context)
        manifest["artifacts"] = step["snapshot_artifacts"]
        manifest["status"] = "failed"
        manifest["finished_at"] = step["finished_at"]
        manifest["error"] = {"step_name": step_name, **error_payload}
        manifest["rollback"] = rollback
        metrics_path = self.write_metrics(context, manifest)
        manifest["metrics_path"] = _relative(metrics_path)
        self._write_run_manifest(context.run_id, manifest)
        self._update_global_status(
            context.run_id,
            status="failed",
            finished_at=manifest["finished_at"],
            metrics_path=_relative(metrics_path),
        )

    def finish_run(self, context: PipelineContext, status: str = "succeeded") -> None:
        """Finalize a run manifest and global index entry."""

        manifest = self._read_run_manifest(context.run_id)
        manifest["status"] = status
        manifest["finished_at"] = _utc_now_iso()
        manifest["artifacts"] = self.snapshot_artifacts(context)
        metrics_path = self.write_metrics(context, manifest)
        manifest["metrics_path"] = _relative(metrics_path)
        self._write_run_manifest(context.run_id, manifest)
        self._update_global_status(
            context.run_id,
            status=status,
            finished_at=manifest["finished_at"],
            metrics_path=_relative(metrics_path),
        )

    def write_metrics(
        self,
        context: PipelineContext,
        manifest: dict[str, Any] | None = None,
    ) -> Path:
        """Write per-run metrics and update the latest metrics shortcut."""

        current_manifest = manifest if manifest is not None else self._read_run_manifest(context.run_id)
        metrics_path = self.run_metrics_file(context.run_id)
        metrics = build_run_metrics(
            context,
            current_manifest,
            metrics_path=metrics_path,
            latest_metrics_path=self.latest_metrics_path,
        )
        _write_json(metrics_path, metrics)
        _write_json(self.latest_metrics_path, metrics)
        return metrics_path

    def snapshot_artifacts(self, context: PipelineContext) -> dict[str, str]:
        """Copy existing latest artifacts into the per-run artifact directory."""

        snapshots: dict[str, str] = {}
        for name, path in sorted(context.artifacts.items()):
            if not path.exists():
                continue
            destination = self.artifact_path(context.run_id, name, path)
            _copy_path(path, destination)
            snapshots[name] = _relative(destination)

        trace_path = context.paths.get("trace")
        if trace_path is not None and trace_path.exists():
            destination = self.artifact_path(context.run_id, "trace", trace_path)
            _copy_trace_for_run(trace_path, destination, context.run_id)
            snapshots["trace"] = _relative(destination)

        memory_path = _memory_path(context)
        if memory_path is not None and memory_path.exists():
            destination = self.artifact_path(context.run_id, "memory", memory_path)
            _copy_path(memory_path, destination)
            snapshots["memory"] = _relative(destination)
            memory_items_dir = _memory_items_dir(context, memory_path)
            if memory_items_dir.exists():
                items_destination = self.artifact_path(
                    context.run_id,
                    "memory_items",
                    memory_items_dir,
                )
                _copy_path(memory_items_dir, items_destination)
                snapshots["memory_items"] = _relative(items_destination)

        return snapshots

    def load_manifest(self, run_id: str) -> dict[str, Any]:
        """Return a historical run manifest."""

        return self._read_run_manifest(run_id)

    def snapshot_path(self, run_id: str, artifact_name: str) -> Path:
        """Return the archived snapshot path for an artifact."""

        manifest = self._read_run_manifest(run_id)
        artifacts = manifest.get("artifacts", {})
        if isinstance(artifacts, dict) and artifacts.get(artifact_name):
            path = Path(str(artifacts[artifact_name]))
            return path if path.is_absolute() else Path.cwd() / path

        fallback = self.artifact_path(run_id, artifact_name, Path(artifact_name))
        if fallback.exists():
            return fallback
        raise FileNotFoundError(
            f"snapshot {artifact_name!r} not found for run {run_id!r}"
        )

    def restore_snapshot(
        self,
        run_id: str,
        artifact_name: str,
        latest_path: str | Path,
    ) -> dict[str, str]:
        """Restore an archived artifact snapshot into a latest path."""

        source = self.snapshot_path(run_id, artifact_name)
        destination = Path(latest_path)
        _copy_path(source, destination)
        return {
            "artifact": artifact_name,
            "source_run_id": run_id,
            "source_snapshot": _relative(source),
            "restored_to": destination.as_posix(),
        }

    def record_replay_inputs(
        self,
        context: PipelineContext,
        replay_inputs: dict[str, Any],
    ) -> None:
        """Persist restored replay/resume input metadata into the run manifest."""

        manifest = self._read_run_manifest(context.run_id)
        manifest["replay_inputs"] = replay_inputs
        self._write_run_manifest(context.run_id, manifest)

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def run_manifest_file(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    def run_metrics_file(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "metrics.json"

    def checkpoint_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "checkpoints"

    def artifact_path(self, run_id: str, name: str, source_path: Path) -> Path:
        configured = ARTIFACT_SNAPSHOT_NAMES.get(name)
        if configured:
            return self.run_dir(run_id) / "artifacts" / configured
        if source_path.is_dir():
            return self.run_dir(run_id) / "artifacts" / name
        return self.run_dir(run_id) / "artifacts" / f"{name}{source_path.suffix}"

    def _read_run_manifest(self, run_id: str) -> dict[str, Any]:
        path = self.run_manifest_file(run_id)
        if not path.exists():
            raise FileNotFoundError(f"run manifest not found: {path.as_posix()}")
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise ValueError(f"run manifest must contain a JSON object: {path.as_posix()}")
        return payload

    def _write_run_manifest(self, run_id: str, payload: dict[str, Any]) -> None:
        _write_json(self.run_manifest_file(run_id), payload)

    def _read_global_manifest(self) -> dict[str, Any]:
        if not self.run_manifest_path.exists():
            return {"latest_run_id": None, "runs": []}
        payload = _read_json(self.run_manifest_path, default={})
        if not isinstance(payload, dict):
            return {"latest_run_id": None, "runs": []}
        runs = payload.get("runs")
        if not isinstance(runs, list):
            payload["runs"] = []
        payload.setdefault("latest_run_id", None)
        return payload

    def _write_global_manifest(self, payload: dict[str, Any]) -> None:
        _write_json(self.run_manifest_path, payload)

    def _upsert_global_run(self, entry: dict[str, Any]) -> None:
        manifest = self._read_global_manifest()
        runs = [run for run in manifest["runs"] if run.get("run_id") != entry["run_id"]]
        runs.append(entry)
        manifest["runs"] = runs
        manifest["latest_run_id"] = entry["run_id"]
        self._write_global_manifest(manifest)

    def _update_global_status(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str | None,
        metrics_path: str | None = None,
    ) -> None:
        manifest = self._read_global_manifest()
        for run in manifest["runs"]:
            if run.get("run_id") == run_id:
                run["status"] = status
                run["finished_at"] = finished_at
                if metrics_path is not None:
                    run["metrics_path"] = metrics_path
                break
        else:
            entry = {
                "run_id": run_id,
                "status": status,
                "finished_at": finished_at,
                "manifest_path": _relative(self.run_manifest_file(run_id)),
            }
            if metrics_path is not None:
                entry["metrics_path"] = metrics_path
            manifest["runs"].append(entry)
        manifest["latest_run_id"] = run_id
        self._write_global_manifest(manifest)

    @staticmethod
    def _step(manifest: dict[str, Any], step_name: str) -> dict[str, Any]:
        steps = manifest.setdefault("steps", {})
        if not isinstance(steps, dict):
            manifest["steps"] = {}
            steps = manifest["steps"]
        step = steps.setdefault(step_name, {})
        if not isinstance(step, dict):
            steps[step_name] = {}
            step = steps[step_name]
        return step


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
        return

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _copy_trace_for_run(source: Path, destination: Path, run_id: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("run_id") == run_id:
            lines.append(json.dumps(payload, ensure_ascii=False, default=str))
    destination.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json(path: Path, *, default: Any | None = None) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if not text and default is not None:
        return default
    return json.loads(text)


def _relative(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def _report_timezone(context: PipelineContext) -> str:
    value = context.config.get("report_timezone")
    if value:
        return str(value)
    pipeline_config = context.config.get("pipeline")
    if isinstance(pipeline_config, dict) and pipeline_config.get("report_timezone"):
        return str(pipeline_config["report_timezone"])
    return "UTC"


def _memory_path(context: PipelineContext) -> Path | None:
    snapshot = memory_replay_snapshot(context)
    if snapshot.get("status") == "available" and snapshot.get("memory_path"):
        return Path(str(snapshot["memory_path"]))

    memory_config = context.config.get("memory", {})
    if isinstance(memory_config, dict) and memory_config.get("path"):
        return Path(str(memory_config["path"]))
    return context.paths.get("memory")


def _memory_items_dir(context: PipelineContext, memory_path: Path) -> Path:
    return effective_memory_items_dir(context, memory_path)


def _redact_config(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("api_key", "apikey", "token", "secret", "password")):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact_config(item)
        return redacted
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
