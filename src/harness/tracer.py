"""Tracing utilities for pipeline and LLM calls."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import PipelineContext


class Tracer:
    """Base tracer interface with common pipeline event helpers."""

    def record(self, event: dict[str, Any]) -> None:
        """Record a trace event."""

        raise NotImplementedError

    def step_started(
        self,
        step_name: str,
        context: PipelineContext,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record that a pipeline step started."""

        self.record(
            self._event(
                event_type="step_started",
                context=context,
                step_name=step_name,
                metadata=metadata,
            )
        )

    def step_finished(
        self,
        step_name: str,
        context: PipelineContext,
        duration_ms: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record that a pipeline step completed."""

        self.record(
            self._event(
                event_type="step_finished",
                context=context,
                step_name=step_name,
                duration_ms=duration_ms,
                metadata=metadata,
            )
        )

    def step_failed(
        self,
        step_name: str,
        context: PipelineContext,
        error: Exception,
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record that a pipeline step failed."""

        event = self._event(
            event_type="step_failed",
            context=context,
            step_name=step_name,
            duration_ms=duration_ms,
            metadata=metadata,
        )
        event["error"] = {
            "type": error.__class__.__name__,
            "message": str(error),
        }
        self.record(event)

    def _event(
        self,
        event_type: str,
        context: PipelineContext,
        step_name: str | None = None,
        duration_ms: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = {
            "event_type": event_type,
            "timestamp": self._utc_now_iso(),
            **context.to_event_payload(),
        }

        if step_name is not None:
            event["step_name"] = step_name
        if duration_ms is not None:
            event["duration_ms"] = duration_ms
        if metadata:
            event["metadata"] = metadata

        return event

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()


class JsonlTracer(Tracer):
    """Append trace events to a JSON Lines file."""

    def __init__(self, path: str | Path = "logs/run_trace.jsonl") -> None:
        self.path = Path(path)

    def record(self, event: dict[str, Any]) -> None:
        """Append a JSON-serializable event as one line."""

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, default=str))
            handle.write("\n")


class InMemoryTracer(Tracer):
    """Store trace events in memory for tests and dry runs."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, event: dict[str, Any]) -> None:
        """Store a trace event."""

        self.events.append(event)
