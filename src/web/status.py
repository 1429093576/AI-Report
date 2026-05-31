"""Status projection for the local web console."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


STEP_ORDER = [
    "collect",
    "clean",
    "relevance",
    "memory_dedupe",
    "extract",
    "validate",
    "visualize",
    "analyze",
    "generate_report",
]

STEP_MESSAGES = {
    "collect": "采集已启用新闻源并保存 raw。",
    "clean": "清洗、去重、按报告日期过滤。",
    "relevance": "执行 AI 科技相关性准入判断。",
    "memory_dedupe": "用历史记忆过滤强重复内容。",
    "extract": "抽取结构化新闻字段。",
    "validate": "校验结构化结果和证据。",
    "visualize": "生成图表。",
    "analyze": "生成日报分析章节。",
    "generate_report": "组装 Markdown 日报并写入记忆。",
}

STEP_LABELS = {
    "collect": "采集",
    "clean": "清洗",
    "relevance": "准入",
    "memory_dedupe": "记忆去重",
    "extract": "抽取",
    "validate": "校验",
    "visualize": "图表",
    "analyze": "分析",
    "generate_report": "报告",
}

ARTIFACT_DEFINITIONS = [
    ("daily_report", "Markdown 日报", "MD", "generate_report"),
    ("topic_distribution_chart", "主题分布图", "PNG", "visualize"),
    ("importance_ranking_chart", "关注度排行图", "PNG", "visualize"),
    ("metrics", "运行指标", "JSON", "generate_report"),
    ("validated", "验证数据", "JSON", "validate"),
    ("report_sections", "分析章节", "JSON", "analyze"),
    ("structured", "结构化数据", "JSON", "extract"),
    ("relevant", "准入新闻", "JSON", "relevance"),
    ("cleaned", "清洗数据", "JSON", "clean"),
    ("raw", "原始采集", "JSON", "collect"),
    ("relevance_report", "准入报告", "JSON", "relevance"),
    ("validation_report", "校验报告", "JSON", "validate"),
    ("llm_audit_report", "LLM 审计", "JSON", "validate"),
    ("memory_report", "记忆报告", "JSON", "memory_dedupe"),
    ("trace", "运行轨迹", "LOG", "generate_report"),
]

TERMINAL_STATUSES = {"succeeded", "failed"}


class RunStatusService:
    """Read run manifests and expose UI-friendly status payloads."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root)

    def latest_run_id(self) -> str | None:
        manifest = self._read_json(self.root / "state" / "run_manifest.json", default={})
        if isinstance(manifest, dict) and manifest.get("latest_run_id"):
            return str(manifest["latest_run_id"])
        return None

    def run_status(self, run_id: str) -> dict[str, Any]:
        manifest_path = self.root / "state" / "runs" / run_id / "manifest.json"
        manifest = self._read_json(manifest_path, default=None)
        if not isinstance(manifest, dict):
            return self._pending_status(run_id)

        steps = self._steps(manifest)
        current_step = self._current_step(steps, str(manifest.get("status") or "pending"))
        error = self._error_payload(manifest)
        artifacts = self._artifacts(manifest)
        metrics_path = self._path_or_none(manifest.get("metrics_path"))
        metrics = self._metrics(metrics_path)
        counts = self._counts(metrics)
        health = self._health(metrics)
        progress = self._progress(steps)
        activity = self._activity(manifest, run_id)
        artifact_cards = self._artifact_cards(artifacts, metrics_path)

        return {
            "run_id": str(manifest.get("run_id") or run_id),
            "run_date": manifest.get("run_date"),
            "report_timezone": manifest.get("report_timezone"),
            "mode": manifest.get("mode"),
            "status": str(manifest.get("status") or "pending"),
            "started_at": manifest.get("started_at"),
            "finished_at": manifest.get("finished_at"),
            "current_step": current_step,
            "steps": steps,
            "error": error,
            "artifacts": artifacts,
            "artifact_cards": artifact_cards,
            "activity": activity,
            "counts": counts,
            "health": health,
            "progress": progress,
            "report_path": artifacts.get("daily_report"),
            "metrics_path": metrics_path,
        }

    def _pending_status(self, run_id: str) -> dict[str, Any]:
        steps = [
            self._step_payload(name, "pending", started_at=None, finished_at=None, error=None)
            for name in STEP_ORDER
        ]
        return {
            "run_id": run_id,
            "run_date": None,
            "mode": "fresh",
            "status": "pending",
            "started_at": None,
            "finished_at": None,
            "current_step": None,
            "steps": steps,
            "error": None,
            "artifacts": {},
            "artifact_cards": self._artifact_cards({}, None),
            "activity": [],
            "counts": {},
            "health": {},
            "progress": self._progress(steps),
            "report_path": None,
            "metrics_path": None,
        }

    def _steps(self, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        manifest_steps = manifest.get("steps")
        manifest_steps = manifest_steps if isinstance(manifest_steps, dict) else {}
        payloads: list[dict[str, Any]] = []
        for name in STEP_ORDER:
            step = manifest_steps.get(name)
            step = step if isinstance(step, dict) else {}
            status = str(step.get("status") or "pending")
            payloads.append(
                self._step_payload(
                    name,
                    status,
                    started_at=step.get("started_at"),
                    finished_at=step.get("finished_at"),
                    error=step.get("error"),
                )
            )
        return payloads

    def _step_payload(
        self,
        name: str,
        status: str,
        *,
        started_at: Any,
        finished_at: Any,
        error: Any,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "label": STEP_LABELS[name],
            "status": status,
            "message": STEP_MESSAGES[name],
            "started_at": started_at,
            "finished_at": finished_at,
            "error": error if isinstance(error, dict) else None,
        }

    @staticmethod
    def _current_step(steps: list[dict[str, Any]], status: str) -> str | None:
        for step in steps:
            if step["status"] == "running":
                return str(step["name"])
        if status in TERMINAL_STATUSES:
            return None
        for step in steps:
            if step["status"] == "pending":
                return str(step["name"])
        return None

    @staticmethod
    def _error_payload(manifest: dict[str, Any]) -> dict[str, Any] | None:
        error = manifest.get("error")
        return error if isinstance(error, dict) else None

    def _artifacts(self, manifest: dict[str, Any]) -> dict[str, str]:
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            return {}
        normalized: dict[str, str] = {}
        for name, path in artifacts.items():
            if isinstance(name, str) and path:
                normalized[name] = self._path_or_none(path) or str(path)
        return normalized

    def _artifact_cards(
        self,
        artifacts: dict[str, str],
        metrics_path: str | None,
    ) -> list[dict[str, Any]]:
        cards: list[dict[str, Any]] = []
        for name, label, kind, step_name in ARTIFACT_DEFINITIONS:
            path = metrics_path if name == "metrics" else artifacts.get(name)
            available = bool(path)
            cards.append(
                {
                    "name": name,
                    "label": label,
                    "kind": kind,
                    "step_name": step_name,
                    "step_label": STEP_LABELS.get(step_name, step_name),
                    "filename": Path(str(path)).name if path else None,
                    "path": path,
                    "url": f"/files?path={path}" if path else None,
                    "available": available,
                }
            )
        return cards

    def _activity(self, manifest: dict[str, Any], run_id: str) -> list[dict[str, str]]:
        trace_path = self._trace_path(manifest)
        if trace_path is None:
            return self._activity_from_steps(manifest)

        events: list[dict[str, str]] = []
        for payload in self._read_jsonl(trace_path):
            if payload.get("run_id") != run_id:
                continue
            event_type = str(payload.get("event_type") or "")
            if event_type not in {"step_started", "step_finished", "step_failed"}:
                continue
            step_name = str(payload.get("step_name") or "")
            label = STEP_LABELS.get(step_name, step_name or "pipeline")
            timestamp = str(payload.get("timestamp") or "")
            level = "info"
            if event_type == "step_started":
                message = f"开始 {label}。"
            elif event_type == "step_failed":
                level = "error"
                error = payload.get("error")
                error_text = ""
                if isinstance(error, dict):
                    error_text = str(error.get("message") or "")
                message = f"{label} 失败。{error_text}".strip()
            else:
                duration = _format_duration(payload.get("duration_ms"))
                message = f"完成 {label}{duration}。"
                detail = _metadata_summary(step_name, payload.get("metadata"))
                if detail:
                    message = f"{message} {detail}"
            events.append(
                {
                    "timestamp": timestamp,
                    "step_name": step_name,
                    "step_label": label,
                    "event_type": event_type,
                    "level": level,
                    "message": message[:280],
                }
            )
        return events[-24:]

    def _activity_from_steps(self, manifest: dict[str, Any]) -> list[dict[str, str]]:
        manifest_steps = manifest.get("steps")
        manifest_steps = manifest_steps if isinstance(manifest_steps, dict) else {}
        events: list[dict[str, str]] = []
        for name in STEP_ORDER:
            step = manifest_steps.get(name)
            if not isinstance(step, dict):
                continue
            status = str(step.get("status") or "")
            if status == "pending":
                continue
            label = STEP_LABELS[name]
            timestamp = str(step.get("finished_at") or step.get("started_at") or "")
            if status == "running":
                message = f"开始 {label}。"
                event_type = "step_started"
                level = "info"
            elif status == "failed":
                error = step.get("error")
                error_text = ""
                if isinstance(error, dict):
                    error_text = str(error.get("message") or "")
                message = f"{label} 失败。{error_text}".strip()
                event_type = "step_failed"
                level = "error"
            else:
                message = f"完成 {label}。"
                event_type = "step_finished"
                level = "info"
            events.append(
                {
                    "timestamp": timestamp,
                    "step_name": name,
                    "step_label": label,
                    "event_type": event_type,
                    "level": level,
                    "message": message[:280],
                }
            )
        return events[-24:]

    def _trace_path(self, manifest: dict[str, Any]) -> Path | None:
        candidates: list[Any] = []
        artifacts = manifest.get("artifacts")
        latest_paths = manifest.get("latest_paths")
        if isinstance(artifacts, dict):
            candidates.append(artifacts.get("trace"))
        if isinstance(latest_paths, dict):
            candidates.append(latest_paths.get("trace"))
        for candidate in candidates:
            path = self._resolve_path(candidate)
            if path is not None and path.exists() and path.is_file():
                return path
        return None

    def _metrics(self, metrics_path: str | None) -> dict[str, Any]:
        path = self._resolve_path(metrics_path)
        if path is None:
            return {}
        payload = self._read_json(path, default={})
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _counts(metrics: dict[str, Any]) -> dict[str, Any]:
        counts = metrics.get("counts")
        return counts if isinstance(counts, dict) else {}

    @staticmethod
    def _health(metrics: dict[str, Any]) -> dict[str, Any]:
        health = metrics.get("health")
        return health if isinstance(health, dict) else {}

    @staticmethod
    def _progress(steps: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(steps)
        completed = sum(1 for step in steps if step.get("status") == "succeeded")
        failed = sum(1 for step in steps if step.get("status") == "failed")
        running = sum(1 for step in steps if step.get("status") == "running")
        percent = round((completed / total) * 100) if total else 0
        return {
            "completed": completed,
            "total": total,
            "failed": failed,
            "running": running,
            "percent": percent,
        }

    def _resolve_path(self, value: Any) -> Path | None:
        path_text = self._path_or_none(value)
        if path_text is None:
            return None
        path = Path(path_text)
        return path if path.is_absolute() else self.root / path

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            return events
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        return events

    @staticmethod
    def _path_or_none(value: Any) -> str | None:
        if value in (None, ""):
            return None
        return str(value)

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return default


def _format_duration(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    if value < 1000:
        return f"，耗时 {int(value)} ms"
    seconds = value / 1000
    if seconds < 60:
        return f"，耗时 {seconds:.1f} 秒"
    minutes = int(seconds // 60)
    rest = int(seconds % 60)
    return f"，耗时 {minutes} 分 {rest} 秒"


def _metadata_summary(step_name: str, metadata: Any) -> str:
    if not isinstance(metadata, dict):
        return ""
    if step_name == "collect":
        sources = metadata.get("sources")
        if isinstance(sources, dict):
            total = sources.get("total")
            succeeded = sources.get("succeeded")
            items = sources.get("items")
            return f"采集源 {succeeded}/{total}，原始条目 {items}。"
    if step_name == "clean":
        quality = metadata.get("quality")
        if isinstance(quality, dict):
            raw = quality.get("raw_count")
            cleaned = quality.get("cleaned_count")
            filtered = quality.get("filtered_non_report_date_count")
            duplicate = quality.get("duplicate_count")
            return f"raw {raw} -> cleaned {cleaned}，过滤非报告日 {filtered}，去重 {duplicate}。"
    llm = metadata.get("llm")
    if isinstance(llm, dict):
        calls = llm.get("call_count")
        tokens = llm.get("total_tokens")
        cost = llm.get("cost_usd")
        if calls is not None:
            parts = [f"LLM 调用 {calls} 次"]
            if tokens is not None:
                parts.append(f"tokens {tokens}")
            if cost is not None:
                parts.append(f"约 ${cost}")
            return "，".join(parts) + "。"
    return ""
