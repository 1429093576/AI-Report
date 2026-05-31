"""RunStore tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.harness import PipelineContext, RunStore


class RunStoreTests(unittest.TestCase):
    def test_start_run_writes_manifests_and_redacted_config_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 29),
                config={
                    "report_timezone": "Asia/Shanghai",
                    "llm": {"api_key": "secret-value", "model": "test-model"},
                },
                paths={"raw": root / "data" / "raw.json"},
            )
            store = RunStore(root / "state")

            store.start_run(context)

            run_manifest = json.loads(
                store.run_manifest_file("run-test").read_text(encoding="utf-8")
            )
            global_manifest = json.loads(
                (root / "state" / "run_manifest.json").read_text(encoding="utf-8")
            )
            config_snapshot = (store.run_dir("run-test") / "config_snapshot.yaml").read_text(
                encoding="utf-8"
            )

            self.assertEqual(run_manifest["status"], "running")
            self.assertEqual(run_manifest["run_date"], "2026-05-29")
            self.assertEqual(run_manifest["report_timezone"], "Asia/Shanghai")
            self.assertEqual(global_manifest["latest_run_id"], "run-test")
            self.assertIn("[REDACTED]", config_snapshot)
            self.assertNotIn("secret-value", config_snapshot)

    def test_start_run_accepts_global_manifest_with_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "state" / "run_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({"latest_run_id": "old-run", "runs": []}),
                encoding="utf-8-sig",
            )
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 29),
            )
            store = RunStore(root / "state")

            store.start_run(context)
            global_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(global_manifest["latest_run_id"], "run-test")

    def test_run_manifest_reads_utf8_bom_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = RunStore(root / "state")
            manifest_path = store.run_manifest_file("run-test")
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(
                json.dumps({"run_id": "run-test", "status": "succeeded"}),
                encoding="utf-8-sig",
            )

            manifest = store.load_manifest("run-test")

        self.assertEqual(manifest["run_id"], "run-test")

    def test_step_finished_snapshots_artifacts_memory_and_current_run_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_path = root / "data" / "raw.json"
            audit_path = root / "logs" / "llm_audit_report.json"
            trace_path = root / "logs" / "run_trace.jsonl"
            memory_path = root / "memory" / "topic_index.json"
            memory_item_path = root / "memory" / "items" / "item-1.json"
            raw_path.parent.mkdir(parents=True)
            trace_path.parent.mkdir(parents=True)
            memory_item_path.parent.mkdir(parents=True)
            raw_path.write_text('[{"id": "1"}]', encoding="utf-8")
            audit_path.write_text('{"run_id": "run-test"}', encoding="utf-8")
            trace_path.write_text(
                '\n'.join(
                    [
                        '{"run_id": "other", "event_type": "step_started"}',
                        '{"run_id": "run-test", "event_type": "step_started"}',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            memory_path.write_text('{"AI": []}', encoding="utf-8")
            memory_item_path.write_text('{"memory_item_id": "item-1"}', encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 29),
                config={"memory": {"path": str(memory_path)}},
                paths={"trace": trace_path},
            )
            context.add_artifact("raw", raw_path)
            context.add_artifact("llm_audit_report", audit_path)
            store = RunStore(root / "state")

            store.start_run(context)
            store.step_started(context, "collect")
            store.step_finished(context, "collect")

            artifact_dir = store.run_dir("run-test") / "artifacts"
            self.assertEqual(
                (artifact_dir / "raw.json").read_text(encoding="utf-8"),
                '[{"id": "1"}]',
            )
            self.assertEqual(
                (artifact_dir / "llm_audit_report.json").read_text(encoding="utf-8"),
                '{"run_id": "run-test"}',
            )
            trace_snapshot = (artifact_dir / "trace.jsonl").read_text(encoding="utf-8")
            self.assertIn('"run_id": "run-test"', trace_snapshot)
            self.assertNotIn('"run_id": "other"', trace_snapshot)
            self.assertEqual(
                (artifact_dir / "memory.json").read_text(encoding="utf-8"),
                '{"AI": []}',
            )
            self.assertEqual(
                (artifact_dir / "memory_items" / "item-1.json").read_text(encoding="utf-8"),
                '{"memory_item_id": "item-1"}',
            )
            run_manifest = json.loads(
                store.run_manifest_file("run-test").read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["steps"]["collect"]["status"], "succeeded")
            self.assertIn("raw", run_manifest["artifacts"])
            self.assertIn("llm_audit_report", run_manifest["artifacts"])
            self.assertIn("trace", run_manifest["artifacts"])
            self.assertIn("memory", run_manifest["artifacts"])
            self.assertIn("memory_items", run_manifest["artifacts"])

    def test_step_failed_updates_run_and_global_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 29))
            store = RunStore(
                root / "state",
                latest_metrics_path=root / "logs" / "metrics.json",
            )
            error = RuntimeError("boom")
            rollback = {"status": "succeeded", "entries": []}

            store.start_run(context)
            store.step_started(context, "extract")
            store.step_failed(context, "extract", error, rollback=rollback)

            run_manifest = json.loads(
                store.run_manifest_file("run-test").read_text(encoding="utf-8")
            )
            global_manifest = json.loads(
                (root / "state" / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["status"], "failed")
            self.assertEqual(run_manifest["steps"]["extract"]["status"], "failed")
            self.assertEqual(run_manifest["error"]["step_name"], "extract")
            self.assertEqual(run_manifest["rollback"], rollback)
            self.assertTrue(run_manifest["metrics_path"].endswith("state/runs/run-test/metrics.json"))
            self.assertEqual(global_manifest["runs"][0]["status"], "failed")
            self.assertTrue(global_manifest["runs"][0]["metrics_path"].endswith("state/runs/run-test/metrics.json"))

            metrics = json.loads(
                store.run_metrics_file("run-test").read_text(encoding="utf-8")
            )
            latest_metrics = json.loads(
                (root / "logs" / "metrics.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metrics["status"], "failed")
            self.assertEqual(metrics["health"]["status"], "failed")
            self.assertEqual(metrics["health"]["failed_step"], "extract")
            self.assertEqual(latest_metrics["run_id"], "run-test")

    def test_finish_run_writes_per_run_and_latest_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_path = root / "data" / "raw.json"
            trace_path = root / "logs" / "run_trace.jsonl"
            raw_path.parent.mkdir(parents=True)
            trace_path.parent.mkdir(parents=True)
            raw_path.write_text('[{"id": "1"}, {"id": "2"}]', encoding="utf-8")
            trace_path.write_text(
                json.dumps(
                    {
                        "event_type": "step_finished",
                        "run_id": "run-test",
                        "run_date": "2026-05-29",
                        "step_name": "collect",
                        "duration_ms": 12,
                        "metadata": {
                            "sources": {
                                "total": 1,
                                "succeeded": 1,
                                "partial": 0,
                                "failed": 0,
                                "rate_limited": 0,
                                "empty": 0,
                                "items": 2,
                                "attempts": 1,
                                "duration_ms": 10,
                                "errors": [],
                                "sources": [
                                    {
                                        "source": "fixture",
                                        "status": "succeeded",
                                        "items": 2,
                                    }
                                ],
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 29),
                paths={"trace": trace_path},
            )
            context.add_artifact("raw", raw_path)
            store = RunStore(
                root / "state",
                latest_metrics_path=root / "logs" / "metrics.json",
            )

            store.start_run(context)
            store.step_started(context, "collect")
            store.step_finished(context, "collect")
            store.finish_run(context)

            metrics = json.loads(
                store.run_metrics_file("run-test").read_text(encoding="utf-8")
            )
            latest_metrics = json.loads(
                (root / "logs" / "metrics.json").read_text(encoding="utf-8")
            )
            run_manifest = json.loads(
                store.run_manifest_file("run-test").read_text(encoding="utf-8")
            )
            global_manifest = json.loads(
                (root / "state" / "run_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(metrics["run_id"], "run-test")
            self.assertEqual(metrics["status"], "succeeded")
            self.assertEqual(metrics["health"]["status"], "healthy")
            self.assertEqual(metrics["counts"]["raw"], 2)
            self.assertEqual(metrics["steps"]["collect"]["duration_ms"], 12)
            self.assertEqual(metrics["sources"]["items"], 2)
            self.assertEqual(metrics["llm"]["total_tokens"], 0)
            self.assertEqual(latest_metrics["run_id"], "run-test")
            self.assertTrue(run_manifest["metrics_path"].endswith("state/runs/run-test/metrics.json"))
            self.assertTrue(global_manifest["runs"][0]["metrics_path"].endswith("state/runs/run-test/metrics.json"))

    def test_restore_snapshot_copies_historical_artifact_to_latest_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_path = root / "data" / "raw.json"
            latest_path = root / "latest" / "raw.json"
            raw_path.parent.mkdir(parents=True)
            raw_path.write_text('[{"id": "raw-1"}]', encoding="utf-8")
            context = PipelineContext(
                run_id="run-parent",
                run_date=date(2026, 5, 29),
                paths={"raw": raw_path},
            )
            context.add_artifact("raw", raw_path)
            store = RunStore(root / "state")
            store.start_run(context)
            store.step_started(context, "collect")
            store.step_finished(context, "collect")

            restored = store.restore_snapshot("run-parent", "raw", latest_path)

            self.assertEqual(
                latest_path.read_text(encoding="utf-8"),
                '[{"id": "raw-1"}]',
            )
            self.assertEqual(restored["artifact"], "raw")
            self.assertEqual(restored["source_run_id"], "run-parent")
            self.assertTrue(restored["source_snapshot"].endswith("artifacts/raw.json"))

    def test_snapshot_artifacts_uses_effective_memory_snapshot_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            latest_memory_path = root / "latest" / "memory" / "topic_index.json"
            snapshot_memory_path = root / "state" / "runs" / "parent" / "artifacts" / "memory.json"
            snapshot_item_path = root / "state" / "runs" / "parent" / "artifacts" / "memory_items" / "history-1.json"
            latest_memory_path.parent.mkdir(parents=True)
            snapshot_item_path.parent.mkdir(parents=True)
            latest_memory_path.write_text('{"topics": {"latest": []}}', encoding="utf-8")
            snapshot_memory_path.write_text('{"topics": {"snapshot": []}}', encoding="utf-8")
            snapshot_item_path.write_text('{"memory_item_id": "history-1"}', encoding="utf-8")
            context = PipelineContext(
                run_id="run-child",
                run_date=date(2026, 5, 29),
                config={"memory": {"path": str(latest_memory_path)}},
            )
            context.set(
                "memory_replay_snapshot",
                {
                    "status": "available",
                    "source_run_id": "parent",
                    "memory_path": snapshot_memory_path.as_posix(),
                    "items_dir": snapshot_item_path.parent.as_posix(),
                },
            )
            store = RunStore(root / "state")

            store.start_run(context, mode="resume", parent_run_id="parent")
            store.step_started(context, "analyze")
            store.step_finished(context, "analyze")

            artifact_dir = store.run_dir("run-child") / "artifacts"
            self.assertEqual(
                (artifact_dir / "memory.json").read_text(encoding="utf-8"),
                '{"topics": {"snapshot": []}}',
            )
            self.assertEqual(
                (artifact_dir / "memory_items" / "history-1.json").read_text(encoding="utf-8"),
                '{"memory_item_id": "history-1"}',
            )


if __name__ == "__main__":
    unittest.main()
