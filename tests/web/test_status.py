"""Web status projection tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.harness import PipelineContext, RunStore
from src.web.status import RunStatusService


class RunStatusServiceTests(unittest.TestCase):
    def test_missing_manifest_returns_pending_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = RunStatusService(tmp_dir)

            status = service.run_status("run-missing")

        self.assertEqual(status["status"], "pending")
        self.assertEqual(status["current_step"], None)
        self.assertEqual(status["steps"][0]["name"], "collect")
        self.assertTrue(all(step["status"] == "pending" for step in status["steps"]))

    def test_running_manifest_reports_current_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 30))
            store = RunStore(root / "state")
            store.start_run(context)
            store.step_started(context, "collect")
            service = RunStatusService(root)

            status = service.run_status("run-test")

        self.assertEqual(status["status"], "running")
        self.assertEqual(status["current_step"], "collect")
        self.assertEqual(status["steps"][0]["status"], "running")

    def test_failed_manifest_exposes_error_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 30))
            store = RunStore(
                root / "state",
                latest_metrics_path=root / "logs" / "metrics.json",
            )
            store.start_run(context)
            store.step_started(context, "extract")
            store.step_failed(context, "extract", RuntimeError("boom"))
            service = RunStatusService(root)

            status = service.run_status("run-test")

        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["current_step"], None)
        self.assertEqual(status["error"]["step_name"], "extract")
        extract_step = [step for step in status["steps"] if step["name"] == "extract"][0]
        self.assertEqual(extract_step["status"], "failed")
        self.assertEqual(extract_step["error"]["message"], "boom")

    def test_succeeded_manifest_includes_artifacts_and_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            report_path = root / "outputs" / "daily_report.md"
            report_path.parent.mkdir(parents=True)
            report_path.write_text("# report", encoding="utf-8")
            context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 30))
            context.add_artifact("daily_report", report_path)
            store = RunStore(
                root / "state",
                latest_metrics_path=root / "logs" / "metrics.json",
            )
            store.start_run(context)
            store.step_started(context, "generate_report")
            store.step_finished(context, "generate_report")
            store.finish_run(context)
            service = RunStatusService(root)

            status = service.run_status("run-test")

        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["report_path"], status["artifacts"]["daily_report"])
        self.assertTrue(str(status["metrics_path"]).endswith("state/runs/run-test/metrics.json"))
        self.assertEqual(status["progress"]["completed"], 1)
        self.assertEqual(status["progress"]["total"], 9)
        report_cards = [
            card for card in status["artifact_cards"] if card["name"] == "daily_report"
        ]
        self.assertEqual(len(report_cards), 1)
        self.assertTrue(report_cards[0]["available"])
        self.assertEqual(report_cards[0]["filename"], "daily_report.md")

    def test_status_exposes_trace_activity_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            run_dir = root / "state" / "runs" / "run-test"
            artifact_dir = run_dir / "artifacts"
            artifact_dir.mkdir(parents=True)
            trace_path = artifact_dir / "trace.jsonl"
            trace_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "event_type": "step_started",
                                "timestamp": "2026-05-30T00:00:00+00:00",
                                "run_id": "run-test",
                                "step_name": "collect",
                            }
                        ),
                        json.dumps(
                            {
                                "event_type": "step_finished",
                                "timestamp": "2026-05-30T00:00:03+00:00",
                                "run_id": "run-test",
                                "step_name": "collect",
                                "duration_ms": 3000,
                                "metadata": {
                                    "sources": {
                                        "total": 2,
                                        "succeeded": 2,
                                        "items": 12,
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            manifest = {
                "run_id": "run-test",
                "status": "running",
                "steps": {"collect": {"status": "succeeded"}},
                "artifacts": {"trace": trace_path.as_posix()},
            }
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest),
                encoding="utf-8",
            )
            service = RunStatusService(root)

            status = service.run_status("run-test")

        self.assertEqual(len(status["activity"]), 2)
        self.assertEqual(status["activity"][0]["message"], "开始 采集。")
        self.assertIn("采集源 2/2", status["activity"][1]["message"])

    def test_latest_run_id_reads_global_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "state" / "run_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({"latest_run_id": "run-latest", "runs": []}),
                encoding="utf-8",
            )
            service = RunStatusService(root)

            latest = service.latest_run_id()

        self.assertEqual(latest, "run-latest")


if __name__ == "__main__":
    unittest.main()
