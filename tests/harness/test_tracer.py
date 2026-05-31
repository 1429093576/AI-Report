"""Tracer tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.harness import InMemoryTracer, JsonlTracer, PipelineContext, Tracer


class TracerTests(unittest.TestCase):
    def test_base_tracer_record_must_be_implemented(self) -> None:
        tracer = Tracer()

        with self.assertRaises(NotImplementedError):
            tracer.record({"event_type": "custom"})

    def test_in_memory_tracer_records_step_events(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 28),
            paths={"raw": "data/raw/news.json"},
        )
        tracer = InMemoryTracer()

        tracer.step_started("clean", context, metadata={"batch_size": 5})
        tracer.step_finished("clean", context, duration_ms=12)

        self.assertEqual(len(tracer.events), 2)
        self.assertEqual(tracer.events[0]["event_type"], "step_started")
        self.assertEqual(tracer.events[0]["step_name"], "clean")
        self.assertEqual(tracer.events[0]["run_id"], "run-test")
        self.assertEqual(tracer.events[0]["metadata"], {"batch_size": 5})
        self.assertIn("timestamp", tracer.events[0])
        self.assertEqual(tracer.events[1]["event_type"], "step_finished")
        self.assertEqual(tracer.events[1]["duration_ms"], 12)

    def test_step_failed_records_error_details(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()
        error = ValueError("bad input")

        tracer.step_failed("extract", context, error, duration_ms=7)

        event = tracer.events[0]
        self.assertEqual(event["event_type"], "step_failed")
        self.assertEqual(event["step_name"], "extract")
        self.assertEqual(event["duration_ms"], 7)
        self.assertEqual(event["error"]["type"], "ValueError")
        self.assertEqual(event["error"]["message"], "bad input")

    def test_jsonl_tracer_appends_events(self) -> None:
        context = PipelineContext(run_id="run-test")

        with tempfile.TemporaryDirectory() as tmp_dir:
            trace_path = Path(tmp_dir) / "nested" / "run_trace.jsonl"
            tracer = JsonlTracer(trace_path)

            tracer.step_started("collect", context)
            tracer.record({"event_type": "custom", "value": 3})

            lines = trace_path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        self.assertEqual(first["event_type"], "step_started")
        self.assertEqual(first["step_name"], "collect")
        self.assertEqual(first["run_id"], "run-test")
        self.assertEqual(second, {"event_type": "custom", "value": 3})


if __name__ == "__main__":
    unittest.main()
