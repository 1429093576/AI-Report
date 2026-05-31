"""Checkpointer tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.harness import Checkpointer, PipelineContext


class CheckpointerTests(unittest.TestCase):
    def test_rollback_restores_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cleaned_path = root / "data" / "cleaned.json"
            cleaned_path.parent.mkdir(parents=True)
            cleaned_path.write_text("old", encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"cleaned": cleaned_path},
            )

            checkpointer = Checkpointer(root / "checkpoints")
            checkpoint = checkpointer.create("clean", context)
            cleaned_path.write_text("new", encoding="utf-8")

            rollback = checkpointer.rollback(checkpoint)

            self.assertEqual(cleaned_path.read_text(encoding="utf-8"), "old")
            self.assertEqual(rollback["status"], "succeeded")
            self.assertEqual(rollback["entries"][0]["action"], "restored")
            checkpoint_manifest = json.loads(
                (checkpoint.checkpoint_dir / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(checkpoint_manifest["entries"][0]["existed"])

    def test_rollback_removes_new_files_created_by_failed_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            relevant_path = root / "data" / "relevant.json"
            report_path = root / "logs" / "relevance_report.json"
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "relevant": relevant_path,
                    "relevance_report": report_path,
                },
            )

            checkpointer = Checkpointer(root / "checkpoints")
            checkpoint = checkpointer.create("relevance", context)
            relevant_path.parent.mkdir(parents=True)
            report_path.parent.mkdir(parents=True)
            relevant_path.write_text("[]", encoding="utf-8")
            report_path.write_text("[]", encoding="utf-8")

            rollback = checkpointer.rollback(checkpoint)

            self.assertFalse(relevant_path.exists())
            self.assertFalse(report_path.exists())
            self.assertEqual(rollback["status"], "succeeded")
            self.assertEqual(
                [entry["action"] for entry in rollback["entries"]],
                ["removed", "removed"],
            )

    def test_rollback_restores_existing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            charts_dir = root / "outputs" / "charts"
            charts_dir.mkdir(parents=True)
            (charts_dir / "old.png").write_text("old", encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"charts_dir": charts_dir},
            )

            checkpointer = Checkpointer(root / "checkpoints")
            checkpoint = checkpointer.create("visualize", context)
            (charts_dir / "old.png").unlink()
            (charts_dir / "new.png").write_text("new", encoding="utf-8")

            rollback = checkpointer.rollback(checkpoint)

            self.assertTrue((charts_dir / "old.png").exists())
            self.assertFalse((charts_dir / "new.png").exists())
            self.assertEqual(rollback["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
