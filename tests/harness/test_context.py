"""PipelineContext tests."""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from src.harness import PipelineContext


class PipelineContextTests(unittest.TestCase):
    def test_context_defaults_create_run_metadata(self) -> None:
        context = PipelineContext()

        self.assertTrue(context.run_id.startswith("run-"))
        self.assertIsInstance(context.run_date, date)
        self.assertEqual(context.config, {})
        self.assertEqual(context.paths, {})
        self.assertEqual(context.artifacts, {})
        self.assertEqual(context.historical_context, "")

    def test_context_rejects_empty_run_id(self) -> None:
        with self.assertRaises(ValueError):
            PipelineContext(run_id="  ")

    def test_state_get_and_set(self) -> None:
        context = PipelineContext(run_id="run-test")

        self.assertEqual(context.get("missing", "fallback"), "fallback")

        context.set("cleaned_count", 3)

        self.assertEqual(context.get("cleaned_count"), 3)

    def test_paths_and_artifacts_are_normalized(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            paths={"raw": "data/raw/news.json"},
            artifacts={"report": Path("outputs/daily_report.md")},
        )

        context.add_path("cleaned", "data/processed/news_cleaned.json")
        context.add_artifact("chart", "outputs/charts/topic.png")

        self.assertEqual(context.paths["raw"], Path("data/raw/news.json"))
        self.assertEqual(
            context.paths["cleaned"], Path("data/processed/news_cleaned.json")
        )
        self.assertEqual(context.artifacts["chart"], Path("outputs/charts/topic.png"))

    def test_to_event_payload_is_json_friendly(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 28),
            paths={"raw": "data/raw/news.json"},
            historical_context=" previous topic context ",
        )
        context.set("items", [1, 2, 3])
        context.add_artifact("report", "outputs/daily_report.md")

        payload = context.to_event_payload()

        self.assertEqual(payload["run_id"], "run-test")
        self.assertEqual(payload["run_date"], "2026-05-28")
        self.assertEqual(payload["paths"], {"raw": "data/raw/news.json"})
        self.assertEqual(
            payload["artifacts"], {"report": "outputs/daily_report.md"}
        )
        self.assertEqual(payload["state_keys"], ["items"])
        self.assertTrue(payload["has_historical_context"])


if __name__ == "__main__":
    unittest.main()
