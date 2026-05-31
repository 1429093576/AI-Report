"""Runtime context shared across Harness-managed pipeline steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class PipelineContext:
    """Mutable runtime state passed through pipeline steps and hooks.

    The context is intentionally small and framework-neutral. Business modules
    can attach intermediate values through ``state`` and generated file paths
    through ``artifacts`` without depending on each other's internals.
    """

    run_id: str = field(default_factory=lambda: f"run-{uuid4().hex}")
    run_date: date = field(default_factory=date.today)
    config: dict[str, Any] = field(default_factory=dict)
    paths: dict[str, Path] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Path] = field(default_factory=dict)
    historical_context: str = ""

    def __post_init__(self) -> None:
        self.run_id = self.run_id.strip()
        if not self.run_id:
            raise ValueError("run_id must not be empty")

        self.paths = {
            name: self._normalize_path(path) for name, path in self.paths.items()
        }
        self.artifacts = {
            name: self._normalize_path(path) for name, path in self.artifacts.items()
        }
        self.historical_context = self.historical_context.strip()

    def get(self, key: str, default: Any = None) -> Any:
        """Return a runtime state value."""

        return self.state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a runtime state value."""

        self.state[key] = value

    def add_path(self, name: str, path: str | Path) -> None:
        """Register a named input, output, or working path."""

        self.paths[name] = self._normalize_path(path)

    def add_artifact(self, name: str, path: str | Path) -> None:
        """Register a generated artifact path."""

        self.artifacts[name] = self._normalize_path(path)

    def to_event_payload(self) -> dict[str, Any]:
        """Return JSON-friendly metadata for trace events."""

        return {
            "run_id": self.run_id,
            "run_date": self.run_date.isoformat(),
            "paths": {name: path.as_posix() for name, path in self.paths.items()},
            "artifacts": {
                name: path.as_posix() for name, path in self.artifacts.items()
            },
            "state_keys": sorted(self.state.keys()),
            "has_historical_context": bool(self.historical_context),
        }

    @staticmethod
    def _normalize_path(path: str | Path) -> Path:
        return Path(path)
