"""Subprocess launcher for the local web console."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from .status import TERMINAL_STATUSES, RunStatusService


class PipelineJobManager:
    """Start one fresh pipeline run at a time."""

    def __init__(
        self,
        *,
        root: str | Path = ".",
        status_service: RunStatusService | None = None,
        popen_factory: Any = subprocess.Popen,
    ) -> None:
        self.root = Path(root)
        self.status_service = status_service or RunStatusService(self.root)
        self.popen_factory = popen_factory
        self._lock = Lock()
        self._active_run_id: str | None = None
        self._active_process: subprocess.Popen[Any] | None = None

    def start_fresh_run(self) -> dict[str, str]:
        """Start a fresh run and return its run id and status endpoint."""

        with self._lock:
            self._refresh_active_locked()
            if self._active_run_id is not None:
                raise RunAlreadyActiveError(self._active_run_id)

            run_id = f"run-web-{uuid4().hex}"
            command = [sys.executable, "-m", "src.main", "--run-id", run_id]
            process = self.popen_factory(command, cwd=str(self.root))
            self._active_run_id = run_id
            self._active_process = process
            return {
                "run_id": run_id,
                "status_url": f"/api/runs/{run_id}/status",
            }

    def active_run_id(self) -> str | None:
        with self._lock:
            self._refresh_active_locked()
            return self._active_run_id

    def _refresh_active_locked(self) -> None:
        if self._active_run_id is None:
            return

        status = self.status_service.run_status(self._active_run_id)
        if status.get("status") in TERMINAL_STATUSES:
            self._active_run_id = None
            self._active_process = None
            return

        if self._active_process is not None and self._active_process.poll() is not None:
            self._active_run_id = None
            self._active_process = None


class RunAlreadyActiveError(RuntimeError):
    """Raised when a web-started run is already active."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run already active: {run_id}")
        self.run_id = run_id
