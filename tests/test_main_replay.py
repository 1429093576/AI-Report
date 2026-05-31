"""Replay/resume entrypoint tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from src import main as main_module
from src.harness import PipelineContext, RunStore


class MainReplayTests(unittest.TestCase):
    def test_step_plan_for_raw_replay_skips_collect(self) -> None:
        steps = main_module._step_plan("raw")

        self.assertEqual(
            [step[0] for step in steps],
            [
                "clean",
                "relevance",
                "memory_dedupe",
                "extract",
                "validate",
                "visualize",
                "analyze",
                "generate_report",
            ],
        )

    def test_step_plan_for_relevant_resume_starts_at_extract(self) -> None:
        steps = main_module._step_plan("relevant")

        self.assertEqual(
            [step[0] for step in steps],
            ["extract", "validate", "visualize", "analyze", "generate_report"],
        )

    def test_step_plan_for_raw_replay_runs_memory_dedupe_after_relevance(self) -> None:
        steps = main_module._step_plan("raw")

        self.assertEqual(
            [step[0] for step in steps[:4]],
            ["clean", "relevance", "memory_dedupe", "extract"],
        )

    def test_step_plan_for_fresh_run_runs_memory_dedupe_after_relevance(self) -> None:
        steps = main_module._step_plan(None)

        self.assertEqual(
            [step[0] for step in steps[:5]],
            ["collect", "clean", "relevance", "memory_dedupe", "extract"],
        )

    def test_step_plan_for_validated_resume_loads_memory_before_analyze(self) -> None:
        steps = main_module._step_plan("validated")

        self.assertEqual(
            [step[0] for step in steps],
            ["visualize", "analyze", "generate_report"],
        )
        self.assertEqual(steps[0][2], "pre_analyze")

    def test_replay_run_date_defaults_to_parent_run_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = RunStore(root / "state")
            context = PipelineContext(
                run_id="run-parent",
                run_date=date(2026, 5, 20),
            )
            store.start_run(context)

            resolved = main_module._resolve_replay_run_date(
                store,
                "run-parent",
                {},
            )

            self.assertEqual(resolved, date(2026, 5, 20))

    def test_parse_args_accepts_run_date(self) -> None:
        args = main_module._parse_args(["--run-date", "2026-05-29"])

        self.assertEqual(args.run_date, "2026-05-29")

    def test_parse_args_accepts_run_id(self) -> None:
        args = main_module._parse_args(["--run-id", "run-web-test_1"])

        self.assertEqual(args.run_id, "run-web-test_1")

    def test_parse_args_rejects_invalid_run_id(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            main_module._parse_args(["--run-id", "../bad"])

    def test_main_uses_explicit_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config" / "pipeline.yaml"
            trace_path = root / "logs" / "trace.jsonl"
            report_path = root / "outputs" / "daily_report.md"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "run_date: 2026-05-20",
                        "report_timezone: Asia/Shanghai",
                        "paths:",
                        f"  trace: {trace_path.as_posix()}",
                        f"  daily_report: {report_path.as_posix()}",
                    ]
                ),
                encoding="utf-8",
            )
            store = RunStore(root / "state")

            def mark(name: str):
                def run(context: PipelineContext) -> None:
                    if name == "generate_report":
                        report_path.parent.mkdir(parents=True)
                        report_path.write_text("# report", encoding="utf-8")
                        context.add_artifact("daily_report", report_path)

                return run

            with patch.object(main_module, "RunStore", return_value=store), patch.object(
                main_module.collect, "run", mark("collect")
            ), patch.object(
                main_module.clean, "run", mark("clean")
            ), patch.object(
                main_module.relevance, "run", mark("relevance")
            ), patch.object(
                main_module.post_relevance, "run", mark("memory_dedupe")
            ), patch.object(
                main_module.extract, "run", mark("extract")
            ), patch.object(
                main_module.validate, "run", mark("validate")
            ), patch.object(
                main_module.visualize, "run", mark("visualize")
            ), patch.object(
                main_module.analyze, "run", mark("analyze")
            ), patch.object(
                main_module.generate_report, "run", mark("generate_report")
            ), patch("builtins.print"):
                current_dir = Path.cwd()
                try:
                    import os

                    os.chdir(root)
                    main_module.main(["--run-id", "run-web-test"])
                finally:
                    os.chdir(current_dir)

            manifest = store.load_manifest("run-web-test")

        self.assertEqual(manifest["run_id"], "run-web-test")
        self.assertEqual(manifest["status"], "succeeded")

    def test_prepare_replay_inputs_restores_validated_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = RunStore(root / "state")
            parent_validated = root / "parent" / "validated.json"
            parent_memory = root / "parent" / "memory" / "topic_index.json"
            parent_memory_item = root / "parent" / "memory" / "items" / "history-1.json"
            latest_validated = root / "latest" / "validated.json"
            parent_validated.parent.mkdir(parents=True)
            parent_memory_item.parent.mkdir(parents=True)
            parent_validated.write_text(
                json.dumps([{"id": "validated-1"}]),
                encoding="utf-8",
            )
            parent_memory.write_text(
                json.dumps({"topics": {"foundation models": [{"id": "history-1"}]}}),
                encoding="utf-8",
            )
            parent_memory_item.write_text(
                json.dumps({"memory_item_id": "history-1"}),
                encoding="utf-8",
            )
            parent = PipelineContext(
                run_id="run-parent",
                run_date=date(2026, 5, 20),
                config={"memory": {"path": str(parent_memory)}},
                paths={"validated": parent_validated},
            )
            parent.add_artifact("validated", parent_validated)
            store.start_run(parent)
            store.step_started(parent, "validate")
            store.step_finished(parent, "validate")
            child = PipelineContext(
                run_id="run-child",
                paths={"validated": latest_validated},
            )

            restored = main_module._prepare_replay_inputs(
                store,
                child,
                parent_run_id="run-parent",
                replay_from="validated",
            )

            self.assertEqual(restored["validated"]["artifact"], "validated")
            self.assertEqual(restored["memory"]["status"], "available")
            self.assertTrue(restored["memory"]["memory_path"].endswith("artifacts/memory.json"))
            self.assertTrue(restored["memory"]["items_dir"].endswith("artifacts/memory_items"))
            self.assertEqual(
                json.loads(latest_validated.read_text(encoding="utf-8")),
                [{"id": "validated-1"}],
            )
            self.assertEqual(child.get("validated_items"), [{"id": "validated-1"}])

    def test_prepare_replay_inputs_records_missing_memory_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            store = RunStore(root / "state")
            parent_raw = root / "parent" / "raw.json"
            latest_raw = root / "latest" / "raw.json"
            parent_raw.parent.mkdir(parents=True)
            parent_raw.write_text('[{"id": "raw-1"}]', encoding="utf-8")
            parent = PipelineContext(
                run_id="run-parent",
                run_date=date(2026, 5, 20),
                paths={"raw": parent_raw},
            )
            parent.add_artifact("raw", parent_raw)
            store.start_run(parent)
            store.step_started(parent, "collect")
            store.step_finished(parent, "collect")
            child = PipelineContext(run_id="run-child", paths={"raw": latest_raw})

            restored = main_module._prepare_replay_inputs(
                store,
                child,
                parent_run_id="run-parent",
                replay_from="raw",
            )

            self.assertEqual(restored["memory"]["status"], "missing")
            self.assertIsNone(restored["memory"]["memory_path"])

    def test_main_raw_replay_does_not_run_collect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config" / "pipeline.yaml"
            raw_path = root / "data" / "raw.json"
            cleaned_path = root / "data" / "cleaned.json"
            relevant_path = root / "data" / "relevant.json"
            structured_path = root / "data" / "structured.json"
            validated_path = root / "data" / "validated.json"
            trace_path = root / "logs" / "trace.jsonl"
            report_path = root / "outputs" / "daily_report.md"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                "\n".join(
                    [
                        "run_date: 2026-05-20",
                        "report_timezone: Asia/Shanghai",
                        "paths:",
                        f"  raw: {raw_path.as_posix()}",
                        f"  cleaned: {cleaned_path.as_posix()}",
                        f"  relevant: {relevant_path.as_posix()}",
                        f"  structured: {structured_path.as_posix()}",
                        f"  validated: {validated_path.as_posix()}",
                        f"  trace: {trace_path.as_posix()}",
                        f"  daily_report: {report_path.as_posix()}",
                    ]
                ),
                encoding="utf-8",
            )
            store = RunStore(root / "state")
            parent_raw = root / "parent" / "raw.json"
            parent_raw.parent.mkdir(parents=True)
            parent_raw.write_text('[{"id": "raw-1"}]', encoding="utf-8")
            parent = PipelineContext(
                run_id="run-parent",
                run_date=date(2026, 5, 20),
                paths={"raw": parent_raw},
            )
            parent.add_artifact("raw", parent_raw)
            store.start_run(parent)
            store.step_started(parent, "collect")
            store.step_finished(parent, "collect")
            executed: list[str] = []

            def mark(name: str):
                def run(context: PipelineContext) -> None:
                    executed.append(name)
                    if name == "relevance":
                        context.set("relevant_items", [])

                return run

            def fail_collect(context: PipelineContext) -> None:
                raise AssertionError("collect should not run during raw replay")

            with patch.object(main_module, "RunStore", return_value=store), patch.object(
                main_module.collect, "run", fail_collect
            ), patch.object(
                main_module.clean, "run", mark("clean")
            ), patch.object(
                main_module.relevance, "run", mark("relevance")
            ), patch.object(
                main_module.post_relevance, "run", mark("memory_dedupe")
            ), patch.object(
                main_module.extract, "run", mark("extract")
            ), patch.object(
                main_module.validate, "run", mark("validate")
            ), patch.object(
                main_module.visualize, "run", mark("visualize")
            ), patch.object(
                main_module.analyze, "run", mark("analyze")
            ), patch.object(
                main_module.generate_report, "run", mark("generate_report")
            ), patch("builtins.print"):
                current_dir = Path.cwd()
                try:
                    import os

                    os.chdir(root)
                    main_module.main(["--replay-run-id", "run-parent", "--from", "raw"])
                finally:
                    os.chdir(current_dir)

            self.assertEqual(
                executed,
                [
                    "clean",
                    "relevance",
                    "memory_dedupe",
                    "extract",
                    "validate",
                    "visualize",
                    "analyze",
                    "generate_report",
                ],
            )
            self.assertEqual(raw_path.read_text(encoding="utf-8"), '[{"id": "raw-1"}]')


if __name__ == "__main__":
    unittest.main()
