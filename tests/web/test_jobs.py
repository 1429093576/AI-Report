"""Web job manager tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.web.jobs import PipelineJobManager, RunAlreadyActiveError
from src.web.status import RunStatusService


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FixedStatusService(RunStatusService):
    def __init__(self, status: str) -> None:
        self.status = status

    def run_status(self, run_id: str) -> dict[str, object]:
        return {"run_id": run_id, "status": self.status}


class PipelineJobManagerTests(unittest.TestCase):
    def test_start_fresh_run_launches_subprocess_with_run_id(self) -> None:
        calls: list[tuple[list[str], str]] = []

        def fake_popen(command: list[str], cwd: str) -> FakeProcess:
            calls.append((command, cwd))
            return FakeProcess()

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = PipelineJobManager(
                root=tmp_dir,
                status_service=FixedStatusService("pending"),
                popen_factory=fake_popen,
            )

            result = manager.start_fresh_run()

        self.assertTrue(result["run_id"].startswith("run-web-"))
        self.assertEqual(result["status_url"], f"/api/runs/{result['run_id']}/status")
        self.assertEqual(calls[0][0][1:4], ["-m", "src.main", "--run-id"])
        self.assertEqual(calls[0][0][4], result["run_id"])

    def test_start_fresh_run_rejects_second_active_run(self) -> None:
        def fake_popen(command: list[str], cwd: str) -> FakeProcess:
            return FakeProcess()

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = PipelineJobManager(
                root=Path(tmp_dir),
                status_service=FixedStatusService("running"),
                popen_factory=fake_popen,
            )
            first = manager.start_fresh_run()

            with self.assertRaises(RunAlreadyActiveError) as raised:
                manager.start_fresh_run()

        self.assertEqual(raised.exception.run_id, first["run_id"])

    def test_finished_process_allows_new_run(self) -> None:
        processes = [FakeProcess(returncode=0), FakeProcess(returncode=None)]

        def fake_popen(command: list[str], cwd: str) -> FakeProcess:
            return processes.pop(0)

        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = PipelineJobManager(
                root=tmp_dir,
                status_service=FixedStatusService("pending"),
                popen_factory=fake_popen,
            )
            first = manager.start_fresh_run()
            second = manager.start_fresh_run()

        self.assertNotEqual(first["run_id"], second["run_id"])


if __name__ == "__main__":
    unittest.main()
