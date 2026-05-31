"""PipelineRunner tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.harness import (
    Checkpointer,
    HookRegistry,
    InMemoryTracer,
    JsonlTracer,
    PipelineContext,
    PipelineRunner,
    RunStore,
)


class PipelineRunnerTests(unittest.TestCase):
    def test_run_step_stores_non_context_result_by_step_name(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()
        runner = PipelineRunner(context, tracer=tracer)

        result = runner.run_step("collect", lambda current: ["item-1"])

        self.assertEqual(result, ["item-1"])
        self.assertEqual(context.get("collect"), ["item-1"])
        self.assertEqual(
            [event["event_type"] for event in tracer.events],
            ["step_started", "step_finished"],
        )
        self.assertEqual(tracer.events[0]["step_name"], "collect")
        self.assertEqual(tracer.events[1]["step_name"], "collect")
        self.assertIn("duration_ms", tracer.events[1])
        self.assertNotIn("metadata", tracer.events[1])

    def test_run_step_keeps_context_when_step_returns_none(self) -> None:
        context = PipelineContext(run_id="run-test")
        runner = PipelineRunner(context)

        def mutate(current: PipelineContext) -> None:
            current.set("cleaned_count", 2)

        result = runner.run_step("clean", mutate)

        self.assertIsNone(result)
        self.assertIs(runner.context, context)
        self.assertEqual(context.get("cleaned_count"), 2)
        self.assertIsNone(context.get("clean"))

    def test_run_step_allows_context_replacement(self) -> None:
        original = PipelineContext(run_id="run-original")
        replacement = PipelineContext(run_id="run-replacement")
        runner = PipelineRunner(original)

        result = runner.run_step("replace", lambda current: replacement)

        self.assertIs(result, replacement)
        self.assertIs(runner.context, replacement)

    def test_run_step_rejects_empty_step_name(self) -> None:
        runner = PipelineRunner(PipelineContext(run_id="run-test"))

        with self.assertRaises(ValueError):
            runner.run_step(" ", lambda current: None)

    def test_run_step_rejects_non_callable_step(self) -> None:
        runner = PipelineRunner(PipelineContext(run_id="run-test"))

        with self.assertRaises(TypeError):
            runner.run_step("collect", "not-callable")  # type: ignore[arg-type]

    def test_run_step_records_failure_and_runs_error_hooks(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()
        hooks = HookRegistry()

        def on_error(current: PipelineContext, error: Exception) -> None:
            current.set("error", str(error))

        def fail(current: PipelineContext) -> None:
            raise RuntimeError("step exploded")

        hooks.register("on_error", on_error)
        runner = PipelineRunner(context, tracer=tracer, hooks=hooks)

        with self.assertRaises(RuntimeError):
            runner.run_step("extract", fail)

        self.assertEqual(context.get("error"), "step exploded")
        self.assertEqual(
            [event["event_type"] for event in tracer.events],
            ["step_started", "step_failed"],
        )
        self.assertEqual(tracer.events[1]["step_name"], "extract")
        self.assertEqual(tracer.events[1]["error"]["type"], "RuntimeError")
        self.assertIn("duration_ms", tracer.events[1])

    def test_run_step_records_llm_metadata_for_extract(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def extract_with_llm(current: PipelineContext) -> None:
            current.set(
                "extract_llm_calls",
                [
                    {
                        "batch_index": 1,
                        "model": "mock-model",
                        "success": True,
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                        "cost_usd": 0.001,
                        "elapsed_ms": 20,
                        "error": None,
                    },
                    {
                        "batch_index": 2,
                        "model": "mock-model",
                        "success": True,
                        "prompt_tokens": 12,
                        "completion_tokens": 6,
                        "total_tokens": 18,
                        "cost_usd": 0.002,
                        "elapsed_ms": 30,
                        "error": None,
                    },
                ],
            )

        runner = PipelineRunner(context, tracer=tracer)
        runner.run_step("extract", extract_with_llm)

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(metadata["call_count"], 2)
        self.assertTrue(metadata["success"])
        self.assertEqual(metadata["prompt_tokens"], 22)
        self.assertEqual(metadata["completion_tokens"], 11)
        self.assertEqual(metadata["total_tokens"], 33)
        self.assertEqual(metadata["cost_usd"], 0.003)
        self.assertEqual(metadata["elapsed_ms"], 50)
        self.assertEqual(metadata["errors"], [])
        self.assertEqual(metadata["calls"][0]["model"], "mock-model")

    def test_run_step_records_llm_metadata_for_relevance(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def relevance_with_llm(current: PipelineContext) -> None:
            current.set(
                "relevance_llm_call",
                {
                    "model": "mock-model",
                    "success": True,
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                    "cost_usd": 0.0005,
                    "elapsed_ms": 15,
                    "error": None,
                },
            )

        runner = PipelineRunner(context, tracer=tracer)
        runner.run_step("relevance", relevance_with_llm)

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(metadata["call_count"], 1)
        self.assertEqual(metadata["prompt_tokens"], 8)
        self.assertEqual(metadata["completion_tokens"], 4)
        self.assertEqual(metadata["total_tokens"], 12)
        self.assertEqual(metadata["cost_usd"], 0.0005)

    def test_run_step_records_llm_business_errors_and_fallbacks(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def relevance_with_fallback(current: PipelineContext) -> None:
            current.set(
                "relevance_llm_call",
                {
                    "model": "mock-model",
                    "success": True,
                    "prompt_tokens": 8,
                    "completion_tokens": 4,
                    "total_tokens": 12,
                    "cost_usd": 0.0005,
                    "elapsed_ms": 15,
                    "error": None,
                },
            )
            current.set(
                "relevance_llm_business_errors",
                [
                    {
                        "error_type": "invalid_json",
                        "message": "relevance returned invalid JSON",
                    }
                ],
            )
            current.set(
                "relevance_llm_fallbacks",
                [
                    {
                        "error_type": "invalid_json",
                        "reason": "used rule-based relevance",
                    }
                ],
            )

        runner = PipelineRunner(context, tracer=tracer)
        runner.run_step("relevance", relevance_with_fallback)

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(metadata["business_error_count"], 1)
        self.assertEqual(metadata["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metadata["fallback_count"], 1)
        self.assertEqual(metadata["fallback_error_types"], {"invalid_json": 1})

    def test_run_step_records_source_metadata_for_collect(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def collect_with_metrics(current: PipelineContext) -> None:
            current.set(
                "source_metrics",
                [
                    {
                        "source": "rss",
                        "status": "succeeded",
                        "items": 2,
                        "attempts": 2,
                        "duration_ms": 20,
                        "errors": [],
                    },
                    {
                        "source": "github",
                        "status": "partial",
                        "items": 1,
                        "attempts": 2,
                        "duration_ms": 30,
                        "errors": [
                            {
                                "source": "github",
                                "category": "rate_limit",
                                "repo": "example/repo",
                            }
                        ],
                    },
                ],
            )

        runner = PipelineRunner(context, tracer=tracer)
        runner.run_step("collect", collect_with_metrics)

        metadata = tracer.events[1]["metadata"]["sources"]
        self.assertEqual(metadata["total"], 2)
        self.assertEqual(metadata["succeeded"], 1)
        self.assertEqual(metadata["partial"], 1)
        self.assertEqual(metadata["items"], 3)
        self.assertEqual(metadata["attempts"], 4)
        self.assertEqual(metadata["errors"][0]["category"], "rate_limit")

    def test_run_step_records_quality_metadata_for_clean(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def clean_with_quality(current: PipelineContext) -> None:
            current.set(
                "clean_quality",
                {
                    "status": "warning",
                    "raw_count": 3,
                    "cleaned_count": 1,
                    "duplicate_count": 1,
                },
            )

        runner = PipelineRunner(context, tracer=tracer)
        runner.run_step("clean", clean_with_quality)

        metadata = tracer.events[1]["metadata"]["quality"]
        self.assertEqual(metadata["status"], "warning")
        self.assertEqual(metadata["duplicate_count"], 1)

    def test_run_step_records_llm_metadata_for_failed_analyze(self) -> None:
        context = PipelineContext(run_id="run-test")
        tracer = InMemoryTracer()

        def analyze_with_error(current: PipelineContext) -> None:
            current.set(
                "analyze_llm_call",
                {
                    "model": "mock-model",
                    "success": False,
                    "prompt_tokens": 10,
                    "completion_tokens": 0,
                    "total_tokens": 10,
                    "cost_usd": 0.001,
                    "elapsed_ms": 20,
                    "error": "HTTP 500: model unavailable",
                },
            )
            raise RuntimeError("analysis failed")

        runner = PipelineRunner(context, tracer=tracer)

        with self.assertRaises(RuntimeError):
            runner.run_step("analyze", analyze_with_error)

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertFalse(metadata["success"])
        self.assertEqual(metadata["errors"], ["HTTP 500: model unavailable"])
        self.assertEqual(metadata["total_tokens"], 10)
        self.assertEqual(metadata["cost_usd"], 0.001)

    def test_run_executes_pre_and_post_process_hooks(self) -> None:
        context = PipelineContext(run_id="run-test")
        hooks = HookRegistry()
        tracer = InMemoryTracer()

        hooks.register("pre_process", lambda current: current.set("pre", True))
        hooks.register("post_process", lambda current: current.set("post", True))

        runner = PipelineRunner(context, tracer=tracer, hooks=hooks)
        result = runner.run(
            [
                ("collect", lambda current: ["raw"]),
                ("clean", lambda current: ["clean"]),
            ]
        )

        self.assertIs(result, context)
        self.assertTrue(context.get("pre"))
        self.assertTrue(context.get("post"))
        self.assertEqual(context.get("collect"), ["raw"])
        self.assertEqual(context.get("clean"), ["clean"])
        self.assertEqual(
            [event["step_name"] for event in tracer.events],
            ["collect", "collect", "clean", "clean"],
        )

    def test_run_supports_per_step_after_hook(self) -> None:
        context = PipelineContext(run_id="run-test")
        hooks = HookRegistry()

        hooks.register("post_validate", lambda current: current.set("validated", True))

        runner = PipelineRunner(context, hooks=hooks)
        result = runner.run(
            [
                ("validate", lambda current: ["valid-item"], "post_validate"),
            ]
        )

        self.assertIs(result, context)
        self.assertEqual(context.get("validate"), ["valid-item"])
        self.assertTrue(context.get("validated"))

    def test_run_writes_metrics_with_llm_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            trace_path = root / "logs" / "run_trace.jsonl"
            context = PipelineContext(
                run_id="run-test",
                paths={"trace": trace_path},
            )
            runner = PipelineRunner(
                context,
                tracer=JsonlTracer(trace_path),
                run_store=RunStore(
                    root / "state",
                    latest_metrics_path=root / "logs" / "metrics.json",
                ),
                checkpointer=Checkpointer(root / "state" / "runs" / "run-test" / "checkpoints"),
            )

            def extract_with_llm(current: PipelineContext) -> None:
                current.set(
                    "extract_llm_calls",
                    [
                        {
                            "batch_index": 1,
                            "model": "mock-model",
                            "success": True,
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                            "cost_usd": 0.001,
                            "elapsed_ms": 20,
                            "error": None,
                        }
                    ],
                )

            runner.run([("extract", extract_with_llm)])

            metrics = json.loads(
                (root / "state" / "runs" / "run-test" / "metrics.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(metrics["status"], "succeeded")
            self.assertEqual(metrics["llm"]["call_count"], 1)
            self.assertEqual(metrics["llm"]["prompt_tokens"], 10)
            self.assertEqual(metrics["llm"]["completion_tokens"], 5)
            self.assertEqual(metrics["llm"]["total_tokens"], 15)
            self.assertEqual(metrics["llm"]["cost_usd"], 0.001)
            self.assertEqual(metrics["llm"]["by_step"]["extract"]["call_count"], 1)

    def test_run_writes_metrics_with_llm_business_errors_and_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            trace_path = root / "logs" / "run_trace.jsonl"
            context = PipelineContext(
                run_id="run-test",
                paths={"trace": trace_path},
            )
            runner = PipelineRunner(
                context,
                tracer=JsonlTracer(trace_path),
                run_store=RunStore(
                    root / "state",
                    latest_metrics_path=root / "logs" / "metrics.json",
                ),
            )

            def relevance_with_fallback(current: PipelineContext) -> None:
                current.set(
                    "relevance_llm_call",
                    {
                        "model": "mock-model",
                        "success": True,
                        "prompt_tokens": 8,
                        "completion_tokens": 4,
                        "total_tokens": 12,
                        "cost_usd": 0.0005,
                        "elapsed_ms": 15,
                        "error": None,
                    },
                )
                current.set(
                    "relevance_llm_business_errors",
                    [
                        {
                            "error_type": "invalid_json",
                            "message": "relevance returned invalid JSON",
                        }
                    ],
                )
                current.set(
                    "relevance_llm_fallbacks",
                    [
                        {
                            "error_type": "invalid_json",
                            "reason": "used rule-based relevance",
                        }
                    ],
                )

            runner.run([("relevance", relevance_with_fallback)])

            metrics = json.loads(
                (root / "state" / "runs" / "run-test" / "metrics.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(metrics["llm"]["business_error_count"], 1)
        self.assertEqual(metrics["llm"]["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metrics["llm"]["fallback_count"], 1)
        self.assertEqual(metrics["llm"]["fallback_error_types"], {"invalid_json": 1})
        self.assertIn("LLM business error", metrics["health"]["warnings"][0])

    def test_run_rejects_malformed_step_tuple(self) -> None:
        runner = PipelineRunner(PipelineContext(run_id="run-test"))

        with self.assertRaises(ValueError):
            runner.run([("bad", lambda current: None, None, "extra")])  # type: ignore[list-item]

    def test_run_step_snapshots_artifact_when_run_store_is_configured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            raw_path = root / "data" / "raw.json"
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
            )
            runner = PipelineRunner(
                context,
                run_store=RunStore(
                    root / "state",
                    latest_metrics_path=root / "logs" / "metrics.json",
                ),
                checkpointer=Checkpointer(root / "state" / "runs" / "run-test" / "checkpoints"),
            )

            def collect(current: PipelineContext) -> list[str]:
                raw_path.parent.mkdir(parents=True)
                raw_path.write_text('["raw"]', encoding="utf-8")
                current.add_artifact("raw", raw_path)
                return ["raw"]

            runner.run_step("collect", collect)

            snapshot = root / "state" / "runs" / "run-test" / "artifacts" / "raw.json"
            manifest = json.loads(
                (root / "state" / "runs" / "run-test" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(snapshot.read_text(encoding="utf-8"), '["raw"]')
            self.assertEqual(manifest["steps"]["collect"]["status"], "succeeded")
            self.assertIn("raw", manifest["artifacts"])

    def test_run_step_rolls_back_and_records_failed_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            cleaned_path = root / "data" / "cleaned.json"
            cleaned_path.parent.mkdir(parents=True)
            cleaned_path.write_text("old", encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"cleaned": cleaned_path},
            )
            runner = PipelineRunner(
                context,
                run_store=RunStore(
                    root / "state",
                    latest_metrics_path=root / "logs" / "metrics.json",
                ),
                checkpointer=Checkpointer(root / "state" / "runs" / "run-test" / "checkpoints"),
            )

            def fail_clean(current: PipelineContext) -> None:
                cleaned_path.write_text("new", encoding="utf-8")
                raise RuntimeError("clean failed")

            with self.assertRaises(RuntimeError):
                runner.run_step("clean", fail_clean)

            manifest = json.loads(
                (root / "state" / "runs" / "run-test" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(cleaned_path.read_text(encoding="utf-8"), "old")
            self.assertEqual(manifest["status"], "failed")
            self.assertEqual(manifest["steps"]["clean"]["status"], "failed")
            self.assertEqual(manifest["steps"]["clean"]["rollback"]["status"], "succeeded")

    def test_memory_dedupe_failure_rolls_back_relevant_but_keeps_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            relevant_path = root / "data" / "relevant.json"
            report_path = root / "logs" / "memory_report.json"
            relevant_path.parent.mkdir(parents=True)
            relevant_path.write_text('["old"]', encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "relevant": relevant_path,
                    "memory_report": report_path,
                },
            )
            runner = PipelineRunner(
                context,
                run_store=RunStore(
                    root / "state",
                    latest_metrics_path=root / "logs" / "metrics.json",
                ),
                checkpointer=Checkpointer(root / "state" / "runs" / "run-test" / "checkpoints"),
            )

            def fail_memory_dedupe(current: PipelineContext) -> None:
                relevant_path.write_text("new", encoding="utf-8")
                report_path.parent.mkdir(parents=True)
                report_path.write_text('{"filtered_count": 1}', encoding="utf-8")
                current.add_artifact("memory_report", report_path)
                raise RuntimeError("all relevant items were duplicates")

            with self.assertRaises(RuntimeError):
                runner.run_step("memory_dedupe", fail_memory_dedupe)

            self.assertEqual(relevant_path.read_text(encoding="utf-8"), '["old"]')
            self.assertEqual(report_path.read_text(encoding="utf-8"), '{"filtered_count": 1}')


if __name__ == "__main__":
    unittest.main()
