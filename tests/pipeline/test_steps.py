"""Pipeline step tests for the offline MVP."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.adapters import (
    CompositeSourceAdapter,
    LocalJsonSourceAdapter,
    MockLLMAdapter,
    SourceAdapter,
    create_source_adapter,
)
from src.harness import InMemoryTracer, PipelineContext, PipelineRunner
from src.main import _resolve_run_date
from src.pipeline import analyze, clean, collect, extract, generate_report, relevance, validate, visualize
from src.schemas import CleanNewsItem, RawNewsItem, StructuredNewsItem


class InMemorySourceAdapter(SourceAdapter):
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.called = False
        self.name = "in_memory"

    def collect(self) -> list[dict[str, object]]:
        self.called = True
        return self.items


class FailingSourceAdapter(SourceAdapter):
    name = "failing"

    def collect(self) -> list[dict[str, object]]:
        raise RuntimeError("source unavailable")


class FakeResponse:
    def __init__(self, status_code: int, payload: str) -> None:
        self.status_code = status_code
        self.text = payload
        self.content = payload.encode("utf-8")
        self.headers: dict[str, str] = {}
        self.url = "https://example.com/response"

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses

    def get(self, *args: object, **kwargs: object) -> FakeResponse:
        return self.responses.pop(0)


class PipelineStepTests(unittest.TestCase):
    def test_collect_uses_configured_source_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            adapter = InMemorySourceAdapter([self._raw_payload("raw-1")])
            context = PipelineContext(run_id="run-test")
            context.paths["raw"] = Path(tmp_dir) / "raw.json"
            context.set("source_adapter", adapter)

            items = collect.run(context)
            stored = json.loads(context.paths["raw"].read_text(encoding="utf-8"))

        self.assertTrue(adapter.called)
        self.assertEqual(len(items), 1)
        self.assertEqual(stored[0]["id"], "raw-1")

    def test_collect_multi_source_uses_configured_source_factory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            source_path = Path(tmp_dir) / "source.json"
            source_path.write_text(json.dumps([self._raw_payload("raw-1")]), encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
                config={
                    "mode": {"source": "multi_source"},
                    "paths": {"raw": str(raw_path)},
                    "sources": [
                        {
                            "name": "fixture",
                            "type": "local_json",
                            "path": str(source_path),
                            "enabled": True,
                        }
                    ],
                },
            )

            items = collect.run(context)
            stored = json.loads(raw_path.read_text(encoding="utf-8"))

        self.assertEqual(len(items), 1)
        self.assertEqual(stored[0]["id"], "raw-1")

    def test_collect_online_mode_excludes_local_fixture_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            source_path = Path(tmp_dir) / "source.json"
            source_path.write_text(json.dumps([self._raw_payload("raw-fixture")]), encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
                config={
                    "mode": {"source": "online"},
                    "paths": {"raw": str(raw_path)},
                    "sources": [
                        {
                            "name": "fixture",
                            "family": "local_fixture",
                            "type": "local_json",
                            "path": str(source_path),
                            "enabled": True,
                        },
                        {
                            "name": "feed",
                            "type": "rss",
                            "url": "https://example.com/feed.xml",
                            "source": "Example Feed",
                            "source_type": "news",
                            "enabled": True,
                        },
                    ],
                },
            )
            context.set(
                "source_adapter",
                create_source_adapter(
                    context.config,
                    session=FakeSession(
                        [FakeResponse(200, _rss_feed("OpenAI launches developer agents", "https://example.com/a"))]
                    ),
                ),
            )

            items = collect.run(context)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source, "Example Feed")

    def test_collect_local_fixture_mode_excludes_online_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            source_path = Path(tmp_dir) / "source.json"
            source_path.write_text(json.dumps([self._raw_payload("raw-fixture")]), encoding="utf-8")
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
                config={
                    "mode": {"source": "local_fixture"},
                    "paths": {"raw": str(raw_path)},
                    "sources": [
                        {
                            "name": "fixture",
                            "family": "local_fixture",
                            "type": "local_json",
                            "path": str(source_path),
                            "enabled": True,
                        },
                        {
                            "name": "feed",
                            "type": "rss",
                            "url": "https://example.com/feed.xml",
                            "source": "Example Feed",
                            "source_type": "news",
                            "enabled": True,
                        },
                    ],
                },
            )

            items = collect.run(context)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "raw-fixture")

    def test_collect_multi_source_supports_qbitai_rss_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
                config={
                    "mode": {"source": "multi_source"},
                    "paths": {"raw": str(raw_path)},
                    "sources": [
                        {
                            "name": "qbitai",
                            "family": "tech_media",
                            "type": "rss",
                            "url": "https://www.qbitai.com/feed",
                            "source": "量子位",
                            "source_type": "news",
                            "language": "zh",
                            "enabled": True,
                        }
                    ],
                },
            )
            context.set(
                "source_adapter",
                create_source_adapter(
                    context.config,
                    session=FakeSession(
                        [
                            FakeResponse(
                                200,
                                _rss_feed(
                                    "量子位：智能体应用开始进入企业核心流程",
                                    "https://example.com/qbitai-agent",
                                ),
                            )
                        ]
                    ),
                ),
            )

            items = collect.run(context)
            stored = json.loads(raw_path.read_text(encoding="utf-8"))

        self.assertEqual(len(items), 1)
        self.assertEqual(stored[0]["source"], "量子位")
        self.assertEqual(stored[0]["language"], "zh")

    def test_collect_multi_source_fails_without_enabled_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            context = PipelineContext(
                run_id="run-test",
                paths={"raw": raw_path},
                config={
                    "mode": {"source": "multi_source"},
                    "sources": [
                        {
                            "name": "disabled",
                            "type": "local_json",
                            "path": str(raw_path),
                            "enabled": False,
                        }
                    ],
                },
            )

            with self.assertRaisesRegex(ValueError, "no enabled sources configured"):
                collect.run(context)

    def test_collect_records_composite_source_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test", paths={"raw": Path(tmp_dir) / "raw.json"})
            context.set(
                "source_adapter",
                CompositeSourceAdapter(
                    [
                        FailingSourceAdapter(),
                        InMemorySourceAdapter([self._raw_payload("raw-1")]),
                    ]
                ),
            )

            items = collect.run(context)

        self.assertEqual(len(items), 1)
        self.assertEqual(context.get("source_errors")[0]["source"], "failing")
        self.assertEqual(context.get("source_metrics")[0]["status"], "failed")
        self.assertEqual(context.get("source_metrics")[1]["status"], "succeeded")

    def test_clean_filters_duplicates_and_creates_stable_hash(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(**self._raw_payload("raw-1")),
                RawNewsItem(**self._raw_payload("raw-2", url="https://example.com/a")),
                RawNewsItem(**self._raw_payload("raw-3", summary="", content="")),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            first = clean.run(context)
            first_hash = first[0].content_hash
            second = clean.run(context)

        self.assertEqual(len(first), 1)
        self.assertEqual(first_hash, second[0].content_hash)
        self.assertEqual(context.get("clean_quality")["duplicate_count"], 1)

    def test_clean_content_hash_uses_title_only(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        title="OpenAI launches developer agents",
                        url="https://example.com/a",
                        summary="First summary.",
                        content="A long article body with one wording.",
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        title="OpenAI launches developer agents",
                        url="https://example.com/b",
                        summary="Different summary.",
                        content="A long article body with another wording.",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(context.get("clean_quality")["duplicate_count"], 1)
        self.assertIn("content_hash", context.get("clean_quality")["dedupe_groups"][0]["reasons"])

    def test_clean_canonical_url_removes_tracking_query_for_dedupe(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        title="OpenAI launches developer agents",
                        url="https://Example.com/a?utm_source=newsletter&id=42#section",
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        title="Different title",
                        url="https://example.com/a?id=42",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].metadata["canonical_url"], "https://example.com/a?id=42")
        self.assertIn("canonical_url", context.get("clean_quality")["dedupe_groups"][0]["reasons"])

    def test_clean_keeps_higher_quality_duplicate_item(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        weak = self._raw_payload(
            "raw-1",
            title="OpenAI launches developer agents",
            url="https://news.google.com/rss/articles/abc",
            summary="Short summary.",
            content="Short summary.",
        )
        weak["metadata"] = {
            "source_family": "aggregator",
            "content_source": "rss_feed",
            "full_content_error": "extracted_text_too_short",
        }
        strong = self._raw_payload(
            "raw-2",
            title="OpenAI launches developer agents",
            url="https://openai.com/news/developer-agents",
            summary="Detailed summary.",
            content="Detailed article body " * 50,
        )
        strong["metadata"] = {
            "source_family": "official_channel",
            "content_source": "article_html",
        }
        context.set("raw_items", [RawNewsItem(**weak), RawNewsItem(**strong)])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].id, "raw-2")
        self.assertEqual(context.get("clean_quality")["dedupe_groups"][0]["kept_id"], "raw-2")

    def test_clean_fails_when_no_report_date_items_remain(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        published_at="2026-05-19T10:00:00+00:00",
                    )
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            with self.assertRaisesRegex(ValueError, "no report-date news items remain after cleaning"):
                clean.run(context)

    def test_clean_keeps_only_items_on_report_date(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(**self._raw_payload("raw-1", published_at="2026-05-20T10:00:00+00:00")),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        url="https://example.com/b",
                        published_at="2026-05-19T23:59:59+00:00",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].id, "raw-1")
        self.assertEqual(context.get("clean_filtered_non_report_date_count"), 1)

    def test_clean_uses_report_timezone_for_report_date_filtering(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 20),
            config={"report_timezone": "Asia/Shanghai"},
        )
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        published_at="2026-05-19T16:30:00+00:00",
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        url="https://example.com/b",
                        published_at="2026-05-20T16:30:00+00:00",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)

        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].id, "raw-1")
        self.assertEqual(context.get("clean_report_timezone"), "Asia/Shanghai")

    def test_extract_maps_keywords_and_keeps_evidence(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("relevant_items", cleaned)

            structured = extract.run(context)

        self.assertEqual(structured[0].topic, "AI Agents")
        self.assertEqual(structured[0].event_type.value, "model_release")
        self.assertGreaterEqual(structured[0].importance_score, 0)
        self.assertLessEqual(structured[0].importance_score, 100)
        self.assertTrue(structured[0].importance_rationale)
        self.assertTrue(structured[0].risk_rationale)
        self.assertTrue(structured[0].opportunity_rationale)
        self.assertTrue(structured[0].evidence)
        self.assertEqual(structured[0].evidence_sources[0].source_item_id, "raw-1")
        self.assertEqual(structured[0].evidence_sources[0].evidence_field, "content")

    def test_extract_maps_research_to_canonical_topic(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "relevant_items",
            [
                CleanNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        title="Stanford publishes benchmark paper for multimodal models",
                        summary="A research paper introduces a new benchmark.",
                        content="The study reports benchmark results for multimodal AI models.",
                    ),
                    content_hash="hash-1",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["structured"] = Path(tmp_dir) / "structured.json"

            structured = extract.run(context)

        self.assertEqual(structured[0].topic, "AI Research")

    def test_extract_maps_open_source_tools_to_canonical_topic(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "relevant_items",
            [
                CleanNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        title="vLLM releases faster open-source inference framework",
                        summary="The GitHub release improves model serving.",
                        content="The open-source framework update helps developers deploy LLMs.",
                    ),
                    content_hash="hash-1",
                )
            ],
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["structured"] = Path(tmp_dir) / "structured.json"

            structured = extract.run(context)

        self.assertEqual(structured[0].topic, "Developer Tools and Open Source")

    def test_extract_prefers_relevant_artifact_over_cleaned_context(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", [])
            context.set("relevant_items", None)
            context.paths["relevant"].write_text(
                json.dumps([cleaned[0].model_dump(mode="json")]),
                encoding="utf-8",
            )

            structured = extract.run(context)

        self.assertEqual(len(structured), 1)
        self.assertEqual(structured[0].title, cleaned[0].title)

    def test_extract_requires_relevance_output_when_relevant_artifact_missing(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("relevant_items", None)
            context.set("cleaned_items", cleaned)

            with self.assertRaisesRegex(
                ValueError,
                "extract requires AI-relevant input from relevance",
            ):
                extract.run(context)

    def test_extract_can_use_injected_llm_adapter(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])
        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            expected = self._structured_payload()
            expected["summary"] = "LLM extracted summary."
            adapter = MockLLMAdapter([json.dumps([expected])])
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", adapter)

            structured = extract.run(context)
            stored = json.loads(context.paths["structured"].read_text(encoding="utf-8"))

        self.assertEqual(context.get("extract_mode"), "llm")
        self.assertEqual(structured[0].summary, "LLM extracted summary.")
        self.assertEqual(stored[0]["summary"], "LLM extracted summary.")
        self.assertEqual(len(adapter.prompts), 1)
        self.assertIn("LLM extracted summary", json.dumps(stored))

    def test_extract_llm_fallback_is_available_to_runner_trace(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        tracer = InMemoryTracer()
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json"]))
            runner = PipelineRunner(context, tracer=tracer)

            structured = runner.run_step("extract", extract.run)

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(len(structured), 1)
        self.assertEqual(context.get("extract_mode"), "llm_fallback")
        self.assertEqual(metadata["call_count"], 4)
        self.assertTrue(metadata["success"])
        self.assertGreater(metadata["total_tokens"], 0)
        self.assertEqual(metadata["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metadata["fallback_item_count"], 1)

    def test_extract_falls_back_to_rule_item_after_single_failures(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        tracer = InMemoryTracer()
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json", "still not json", "nope", "bad"]))
            runner = PipelineRunner(context, tracer=tracer)

            structured = runner.run_step("extract", extract.run)
            stored = json.loads(context.paths["structured"].read_text(encoding="utf-8"))

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(context.get("extract_mode"), "llm_fallback")
        self.assertEqual(len(structured), 1)
        self.assertEqual(structured[0].id, "structured-1")
        self.assertEqual(stored[0]["id"], "structured-1")
        self.assertEqual(metadata["call_count"], 4)
        self.assertEqual(metadata["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metadata["fallback_count"], 1)
        self.assertEqual(metadata["fallback_item_count"], 1)
        self.assertEqual(
            context.get("extract_llm_fallbacks")[-1]["item_id"],
            "raw-1",
        )

    def test_extract_required_llm_raises_instead_of_fallback(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 20),
            config={"mode": {"llm": "llm"}},
        )
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json", "still not json"]))

            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                extract.run(context)

        self.assertIsNone(context.get("extract_llm_fallbacks"))

    def test_extract_retries_single_item_successfully(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 20),
            config={"pipeline": {"llm_max_concurrency": 1}},
        )
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)
            expected = self._structured_payload()
            expected["summary"] = "Recovered on retry."
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json", json.dumps([expected])]))

            structured = extract.run(context)

        self.assertEqual(context.get("extract_mode"), "llm")
        self.assertEqual(structured[0].summary, "Recovered on retry.")
        self.assertIsNone(context.get("extract_llm_fallbacks"))
        self.assertEqual(len(context.get("extract_llm_calls")), 2)

    def test_extract_defaults_to_single_item_parallel_calls(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(**self._raw_payload("raw-1")),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        title="Anthropic launches agent tooling",
                        url="https://example.com/b",
                        summary="Anthropic announced agent tooling for developers.",
                        content="The release improves agent workflows for enterprise teams.",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            cleaned = clean.run(context)

            def response(prompt: str, schema: object) -> str:
                payload = self._structured_payload()
                if "raw-2" in prompt:
                    payload["id"] = "structured-2"
                    payload["title"] = "Anthropic launches agent tooling"
                    payload["source"] = "OpenAI News"
                    payload["url"] = "https://example.com/b"
                    payload["summary"] = "Anthropic announced agent tooling for developers."
                    payload["content_hash"] = "hash-2"
                    payload["evidence_sources"][0]["source_item_id"] = "raw-2"
                    payload["evidence_sources"][0]["evidence_quote"] = (
                        "The release improves agent workflows for enterprise teams."
                    )
                return json.dumps([payload])

            adapter = MockLLMAdapter(response)
            context.set("relevant_items", cleaned)
            context.set("llm_adapter", adapter)

            structured = extract.run(context)

        self.assertEqual([item.id for item in structured], ["structured-1", "structured-2"])
        self.assertEqual(len(adapter.prompts), 2)
        self.assertEqual({call["scope"] for call in context.get("extract_llm_calls")}, {"single"})

    def test_relevance_accepts_ai_items_and_writes_artifacts(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            relevant_items = relevance.run(context)
            stored = json.loads(context.paths["relevant"].read_text(encoding="utf-8"))
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        self.assertEqual(len(relevant_items), 1)
        self.assertEqual(len(stored), 1)
        self.assertEqual(report[0]["decision_source"], "rule_based")
        self.assertEqual(context.get("relevant_count"), 1)

    def test_relevance_rejects_non_ai_items(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-1",
                        title="Valve raises Steam Deck prices by more than $200",
                        summary="The handheld gaming device now costs much more.",
                        content="The article is about gaming hardware pricing and market pressure.",
                    )
                )
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            with self.assertRaisesRegex(
                ValueError,
                "no AI-relevant report-date news items remain after relevance filtering",
            ):
                relevance.run(context)

    def test_relevance_rule_based_accepts_ai_infrastructure_policy_and_research(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-infra",
                        title="vLLM releases faster inference scheduler for LLM serving",
                        url="https://example.com/infra",
                        summary="The release improves throughput for large language model inference.",
                        content=(
                            "The update targets model serving, batching, KV cache use, "
                            "and GPU utilization for production LLM deployments."
                        ),
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-policy",
                        title="Regulators require labels for AI-generated election videos",
                        url="https://example.com/policy",
                        summary="The rule applies to synthetic media made by generative AI systems.",
                        content=(
                            "The policy targets deepfakes, generated-content labels, "
                            "and disclosure obligations for AI video tools."
                        ),
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-research",
                        title="Researchers introduce benchmark for multimodal reasoning models",
                        url="https://example.com/research",
                        summary="The paper evaluates VLM reasoning across charts and documents.",
                        content=(
                            "The benchmark measures model accuracy, hallucination risk, "
                            "and robustness for multimodal AI systems."
                        ),
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            relevant_items = relevance.run(context)
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        self.assertEqual([item.id for item in relevant_items], ["raw-infra", "raw-policy", "raw-research"])
        self.assertTrue(all(item["relevance_score"] >= 70 for item in report))

    def test_relevance_rule_based_rejects_ai_adjacent_borderline_items(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **self._raw_payload(
                        "raw-cloud",
                        title="Cloud provider opens new enterprise data center region",
                        url="https://example.com/cloud",
                        summary="The company expanded cloud capacity for enterprise customers.",
                        content=(
                            "The announcement mentions AI demand in one paragraph, "
                            "but the main event is storage, databases, and application hosting."
                        ),
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-privacy",
                        title="New privacy bill sets rules for location data brokers",
                        url="https://example.com/privacy",
                        summary="The legislation restricts resale of consumer location data.",
                        content=(
                            "Technology companies say the law may affect analytics products; "
                            "AI is listed as one possible downstream use."
                        ),
                    )
                ),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-phone",
                        title="Phone maker unveils flagship handset with AI camera filters",
                        url="https://example.com/phone",
                        summary="The phone adds automatic image enhancement and a new display.",
                        content=(
                            "The launch is mainly about hardware design, cameras, battery life, "
                            "and pricing. AI filters are one small feature in the camera app."
                        ),
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            with self.assertRaisesRegex(
                ValueError,
                "no AI-relevant report-date news items remain after relevance filtering",
            ):
                relevance.run(context)
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        self.assertTrue(all(not item["is_ai_related"] for item in report))
        self.assertTrue(all(item["relevance_score"] < 70 for item in report))

    def test_relevance_rule_based_accepts_chinese_ai_boundaries(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-infra",
                            title="昇腾推理框架升级，提升大模型推理部署效率",
                            url="https://example.com/cn-infra",
                            summary="新版 CANN 优化模型服务、显存使用和训练集群调度。",
                            content="该更新面向生产级 AI 工作负载，支持大模型推理和模型部署。",
                        ),
                        "language": "zh",
                    }
                ),
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-policy",
                            title="生成式人工智能服务备案规则更新",
                            url="https://example.com/cn-policy",
                            summary="新规要求大模型完成模型安全评测和内容标识。",
                            content="监管要求说明训练数据来源、算法备案和生成内容水印策略。",
                        ),
                        "language": "zh",
                    }
                ),
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-research",
                            title="清华团队发布多模态推理基准测试论文",
                            url="https://example.com/cn-research",
                            summary="该数据集用于评测大模型在图表和文档理解中的推理能力。",
                            content="论文提出新的模型评测方法，覆盖多模态、推理模型和幻觉风险。",
                        ),
                        "language": "zh",
                    }
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            relevant_items = relevance.run(context)
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        self.assertEqual(
            [item.id for item in relevant_items],
            ["raw-cn-infra", "raw-cn-policy", "raw-cn-research"],
        )
        self.assertTrue(all(item["relevance_score"] >= 70 for item in report))

    def test_relevance_rule_based_rejects_chinese_ai_adjacent_items(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-phone",
                            title="手机厂商发布旗舰手机，新增 AI 拍照滤镜",
                            url="https://example.com/cn-phone",
                            summary="发布会主要介绍屏幕、相机、电池和价格。",
                            content="AI 功能只是相机应用里的小功能，并非核心产品能力。",
                        ),
                        "language": "zh",
                    }
                ),
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-cloud",
                            title="云服务商开设新数据中心",
                            url="https://example.com/cn-cloud",
                            summary="公告提到 AI 需求增长，但主要服务数据库、存储和企业应用托管。",
                            content="该数据中心面向通用云服务，没有明确的大模型训练或推理任务。",
                        ),
                        "language": "zh",
                    }
                ),
                RawNewsItem(
                    **{
                        **self._raw_payload(
                            "raw-cn-privacy",
                            title="新隐私法规限制位置数据买卖",
                            url="https://example.com/cn-privacy",
                            summary="法规主要针对数据经纪商和消费者隐私保护。",
                            content="报道称人工智能可能是下游用途之一，但不是监管对象。",
                        ),
                        "language": "zh",
                    }
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)

            with self.assertRaisesRegex(
                ValueError,
                "no AI-relevant report-date news items remain after relevance filtering",
            ):
                relevance.run(context)
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        self.assertTrue(all(not item["is_ai_related"] for item in report))
        self.assertTrue(all(item["relevance_score"] < 70 for item in report))

    def test_relevance_llm_rejects_items_below_threshold(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        response = [
            {
                "item_id": "raw-1",
                "title": "OpenAI launches developer agents",
                "url": "https://example.com/a",
                "published_at": "2026-05-20T10:00:00+00:00",
                "content_hash": "hash-1",
                "is_ai_related": True,
                "relevance_score": 60,
                "relevance_reason": "AI is mentioned but the signal is too weak.",
                "relevance_evidence": ["OpenAI launches developer agents"],
                "decision_source": "llm",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter([json.dumps(response)]))

            with self.assertRaisesRegex(
                ValueError,
                "no AI-relevant report-date news items remain after relevance filtering",
            ):
                relevance.run(context)

    def test_relevance_falls_back_to_rules_on_invalid_llm_json(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        tracer = InMemoryTracer()
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json"]))
            runner = PipelineRunner(context, tracer=tracer)

            relevant_items = runner.run_step("relevance", relevance.run)
            report = json.loads(context.paths["relevance_report"].read_text(encoding="utf-8"))

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(context.get("relevance_mode"), "llm_fallback")
        self.assertEqual(len(relevant_items), 1)
        self.assertEqual(report[0]["decision_source"], "rule_based")
        self.assertEqual(metadata["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metadata["fallback_count"], 1)

    def test_relevance_required_llm_raises_instead_of_fallback(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            run_date=date(2026, 5, 20),
            config={"mode": {"llm": "llm"}},
        )
        context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)
            context.set("cleaned_items", cleaned)
            context.set("llm_adapter", MockLLMAdapter(["not json", "still not json"]))

            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                relevance.run(context)

        self.assertIsNone(context.get("relevance_llm_fallbacks"))

    def test_relevance_defaults_to_single_item_parallel_calls(self) -> None:
        context = PipelineContext(run_id="run-test", run_date=date(2026, 5, 20))
        context.set(
            "raw_items",
            [
                RawNewsItem(**self._raw_payload("raw-1")),
                RawNewsItem(
                    **self._raw_payload(
                        "raw-2",
                        title="Anthropic launches agent tooling",
                        url="https://example.com/b",
                        summary="Anthropic announced agent tooling for developers.",
                        content="The release improves agent workflows for enterprise teams.",
                    )
                ),
            ],
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["relevance_report"] = Path(tmp_dir) / "relevance_report.json"
            cleaned = clean.run(context)

            def response(prompt: str, schema: object) -> str:
                item_id = "raw-2" if "raw-2" in prompt else "raw-1"
                title = (
                    "Anthropic launches agent tooling"
                    if item_id == "raw-2"
                    else "OpenAI launches developer agents"
                )
                return json.dumps(
                    {
                        "item_id": item_id,
                        "title": title,
                        "url": "https://example.com/b" if item_id == "raw-2" else "https://example.com/a",
                        "published_at": "2026-05-20T10:00:00+00:00",
                        "content_hash": "hash-2" if item_id == "raw-2" else "hash-1",
                        "is_ai_related": True,
                        "relevance_score": 90,
                        "relevance_reason": "The item is centrally about AI agent tooling.",
                        "relevance_evidence": [title],
                        "decision_source": "llm",
                    }
                )

            adapter = MockLLMAdapter(response)
            context.set("cleaned_items", cleaned)
            context.set("llm_adapter", adapter)

            relevant_items = relevance.run(context)

        self.assertEqual([item.id for item in relevant_items], ["raw-1", "raw-2"])
        self.assertEqual(len(adapter.prompts), 2)
        self.assertEqual({call["scope"] for call in context.get("relevance_llm_calls")}, {"single"})

    def test_validate_writes_valid_items_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                config={"report_timezone": "Asia/Shanghai"},
            )
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            context.paths["validated"] = Path(tmp_dir) / "validated.json"
            context.paths["validation_report"] = Path(tmp_dir) / "validation.json"
            item = self._structured_payload()
            context.set("structured_items", [item])

            valid_items = validate.run(context)
            report = json.loads(context.paths["validation_report"].read_text(encoding="utf-8"))

        self.assertEqual(len(valid_items), 1)
        self.assertEqual(report["valid_items"], 1)
        self.assertTrue(report["is_valid"])

    def test_validate_rejects_missing_supported_evidence_and_writes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                config={"report_timezone": "Asia/Shanghai"},
            )
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            context.paths["validated"] = Path(tmp_dir) / "validated.json"
            context.paths["validation_report"] = Path(tmp_dir) / "validation.json"
            context.paths["relevant"] = Path(tmp_dir) / "relevant.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            context.set("raw_items", [RawNewsItem(**self._raw_payload("raw-1"))])
            context.paths["cleaned"] = Path(tmp_dir) / "cleaned.json"
            cleaned = clean.run(context)
            context.set("relevant_items", cleaned)
            item = self._structured_payload()
            item["evidence_sources"] = [
                {
                    "source_item_id": "raw-1",
                    "evidence_field": "content",
                    "evidence_quote": "This quote is not in the source.",
                    "claim": "OpenAI announced a model release for developer agents.",
                }
            ]
            context.set("structured_items", [item])

            with self.assertRaisesRegex(ValueError, "missing supported evidence"):
                validate.run(context)

            audit = json.loads(context.paths["llm_audit_report"].read_text(encoding="utf-8"))

        self.assertEqual(audit["run_id"], "run-test")
        self.assertEqual(audit["blocked_count"], 1)
        self.assertEqual(
            audit["sections"]["structured_evidence"]["records"][0]["evidence"][0]["status"],
            "missing_quote",
        )

    def test_validate_rejects_items_outside_report_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                config={"report_timezone": "Asia/Shanghai"},
            )
            context.paths["structured"] = Path(tmp_dir) / "structured.json"
            context.paths["validated"] = Path(tmp_dir) / "validated.json"
            context.paths["validation_report"] = Path(tmp_dir) / "validation.json"
            item = self._structured_payload()
            item["published_at"] = "2026-05-20T16:30:00+00:00"
            context.set("structured_items", [item])

            with self.assertRaisesRegex(ValueError, "falls outside report date"):
                validate.run(context)

    def test_analyze_generates_core_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.set("validated_items", [self._structured_payload()])
            context.historical_context = "Historical context for topic: ai agents"

            report = analyze.run(context)

        self.assertTrue(report.top_events)
        self.assertTrue(report.trend_insights)
        self.assertTrue(report.risk_insights)
        self.assertTrue(report.opportunity_insights)
        self.assertTrue(report.trend_insights[0].historical_context_used)
        self.assertEqual(report.trend_insights[0].trend_state.value, "new")

    def test_analyze_marks_continuing_trend_from_memory_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["memory_report"] = Path(tmp_dir) / "memory_report.json"
            item = self._structured_payload()
            context.set("validated_items", [item])
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "memory_item_id": "history-1",
                                    "title": "Previous OpenAI agent update",
                                    "published_at": "2026-05-19T10:00:00+00:00",
                                    "summary": "OpenAI previously expanded agent workflows.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 82,
                                    "risk_level": "low",
                                    "opportunity_level": "high",
                                }
                            ],
                        }
                    ],
                    "item_relationships": [
                        {
                            "item_id": "structured-1",
                            "topic": "AI Agents",
                            "relationship": "continuing",
                            "confidence": 0.76,
                            "matched_memory_item_ids": ["history-1"],
                        }
                    ],
                },
            )
            context.set(
                "memory_report",
                {
                    "strong_dedupe": {
                        "input_count": 3,
                        "filtered_count": 1,
                    },
                    "context_retrieval": {
                        "metadata_included_count": 1,
                    },
                    "fulltext_selection": {
                        "mode": "heuristic",
                        "requested_item_ids": ["history-1"],
                        "read_item_ids": ["history-1"],
                        "invalid_item_ids": [],
                    },
                },
            )

            report = analyze.run(context)
            memory_report = json.loads(
                context.paths["memory_report"].read_text(encoding="utf-8")
            )

        self.assertEqual(report.trend_insights[0].trend_state.value, "continuing")
        self.assertEqual(report.memory_usage.relevant_candidate_count, 3)
        self.assertEqual(report.memory_usage.strong_duplicate_filtered_count, 1)
        self.assertEqual(report.memory_usage.retrieved_metadata_count, 1)
        self.assertEqual(report.memory_usage.adopted_historical_evidence_count, 1)
        self.assertEqual(report.historical_comparisons[0].memory_item_id, "history-1")
        self.assertEqual(report.historical_comparisons[0].relation_type, "continuing")
        self.assertTrue(report.deep_dives[0].historical_context_note)
        self.assertTrue(report.trend_insights[0].historical_evidence)
        self.assertEqual(
            memory_report["historical_evidence_selection"]["adopted_count"],
            1,
        )

    def test_analyze_marks_heating_up_when_current_topic_intensifies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            first = self._structured_payload()
            second = {
                **self._structured_payload(),
                "id": "structured-2",
                "url": "https://example.com/b",
                "content_hash": "hash-2",
                "title": "OpenAI launches second developer agent update",
            }
            context.set("validated_items", [first, second])
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "importance_score": 70,
                                    "risk_level": "low",
                                    "opportunity_level": "medium",
                                }
                            ],
                        }
                    ],
                    "item_relationships": [],
                },
            )

            report = analyze.run(context)

        self.assertEqual(report.trend_insights[0].trend_state.value, "heating_up")

    def test_analyze_marks_cooling_down_when_history_is_stronger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            item = {**self._structured_payload(), "importance_score": 70}
            context.set("validated_items", [item])
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {"id": "history-1", "importance_score": 92},
                                {"id": "history-2", "importance_score": 88},
                            ],
                        }
                    ],
                    "item_relationships": [],
                },
            )

            report = analyze.run(context)

        self.assertEqual(report.trend_insights[0].trend_state.value, "cooling_down")

    def test_analyze_marks_reversing_when_risk_direction_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            item = {
                **self._structured_payload(),
                "risk_level": "high",
                "opportunity_level": "low",
            }
            context.set("validated_items", [item])
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "importance_score": 82,
                                    "risk_level": "low",
                                    "opportunity_level": "high",
                                }
                            ],
                        }
                    ],
                    "item_relationships": [],
                },
            )

            report = analyze.run(context)

        self.assertEqual(report.trend_insights[0].trend_state.value, "reversing")

    def test_analyze_can_use_injected_llm_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            context.set(
                "relevant_items",
                [
                    CleanNewsItem(
                        **self._raw_payload(
                            "raw-1",
                            content=(
                                "FULL ORIGINAL CONTEXT: OpenAI launches developer agents "
                                "with API workflow details, enterprise deployment notes, "
                                "and integration context that should reach the analyzer."
                            ),
                        ),
                        content_hash="hash-1",
                    )
                ],
            )
            expected = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            expected["executive_summary"] = "LLM generated executive summary."
            adapter = MockLLMAdapter([json.dumps(expected)])
            context.set("validated_items", [item])
            context.set("llm_adapter", adapter)

            report = analyze.run(context)
            stored = json.loads(context.paths["report_sections"].read_text(encoding="utf-8"))

        self.assertEqual(context.get("analyze_mode"), "llm")
        self.assertEqual(report.executive_summary, "LLM generated executive summary.")
        self.assertTrue(report.top_events[0].evidence_sources)
        self.assertEqual(stored["executive_summary"], "LLM generated executive summary.")
        self.assertTrue(stored["top_events"][0]["evidence_sources"])
        self.assertEqual(len(adapter.prompts), 1)
        self.assertIn("validated_items", adapter.prompts[0])
        self.assertIn("source_documents", adapter.prompts[0])
        self.assertIn("FULL ORIGINAL CONTEXT", adapter.prompts[0])
        self.assertIn("full clean/relevant content included without truncation", adapter.prompts[0])
        self.assertIn("trend_signals", adapter.prompts[0])
        self.assertIn("rule_suggested_state", adapter.prompts[0])
        self.assertTrue(context.get("analyze_trend_signals"))

    def test_analyze_llm_can_override_rule_suggested_trend_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            item = StructuredNewsItem.model_validate(
                {
                    **self._structured_payload(),
                    "risk_level": "medium",
                    "opportunity_level": "medium",
                }
            )
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "importance_score": 82,
                                    "risk_level": "low",
                                    "opportunity_level": "high",
                                }
                            ],
                        }
                    ],
                    "item_relationships": [
                        {
                            "item_id": "structured-1",
                            "topic": "AI Agents",
                            "relationship": "continuing",
                            "confidence": 0.76,
                            "matched_memory_item_ids": ["history-1"],
                        }
                    ],
                },
            )
            report_payload = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            self.assertEqual(
                context.get("analyze_trend_signals")[0]["rule_suggested_state"],
                "continuing",
            )
            report_payload["trend_insights"][0]["trend_state"] = "reversing"
            report_payload["trend_insights"][0]["summary"] = (
                "LLM judged this as reversing because today's risk and opportunity "
                "direction changed against historical context."
            )
            adapter = MockLLMAdapter([json.dumps(report_payload)])
            context.set("validated_items", [item])
            context.set("llm_adapter", adapter)

            report = analyze.run(context)

        self.assertEqual(context.get("analyze_mode"), "llm")
        self.assertEqual(report.trend_insights[0].trend_state.value, "reversing")
        self.assertIn("trend_signals", adapter.prompts[0])
        self.assertIn('"rule_suggested_state": "continuing"', adapter.prompts[0])

    def test_analyze_filters_llm_historical_evidence_outside_memory_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["memory_report"] = Path(tmp_dir) / "memory_report.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "memory_item_id": "history-1",
                                    "title": "Previous OpenAI agent update",
                                    "published_at": "2026-05-19T10:00:00+00:00",
                                    "summary": "OpenAI previously expanded agent workflows.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 82,
                                }
                            ],
                        }
                    ],
                    "item_relationships": [
                        {
                            "item_id": "structured-1",
                            "topic": "AI Agents",
                            "relationship": "continuing",
                            "confidence": 0.76,
                            "matched_memory_item_ids": ["history-1"],
                        }
                    ],
                },
            )
            context.set(
                "memory_report",
                {
                    "strong_dedupe": {"input_count": 1, "filtered_count": 0},
                    "context_retrieval": {"metadata_included_count": 1},
                    "fulltext_selection": {
                        "mode": "llm",
                        "requested_item_ids": ["history-1"],
                        "read_item_ids": ["history-1"],
                        "invalid_item_ids": [],
                    },
                },
            )
            report_payload = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            report_payload["historical_comparisons"] = [
                {
                    **report_payload["historical_comparisons"][0],
                    "memory_item_id": "invented-history",
                    "historical_event_title": "Invented historical item",
                }
            ]
            adapter = MockLLMAdapter([json.dumps(report_payload)])
            context.set("validated_items", [item])
            context.set("llm_adapter", adapter)

            report = analyze.run(context)
            memory_report = json.loads(
                context.paths["memory_report"].read_text(encoding="utf-8")
            )

        self.assertEqual(report.historical_comparisons, [])
        self.assertEqual(report.memory_usage.adopted_historical_evidence_count, 0)
        self.assertEqual(
            memory_report["historical_evidence_selection"]["invalid_adopted_items"][0]["memory_item_id"],
            "invented-history",
        )

    def test_analyze_excludes_llm_items_without_supported_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            report_payload = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            report_payload["risk_insights"].append(
                {
                    "title": "Unsupported risk",
                    "level": "medium",
                    "summary": "This risk cites no validated item evidence.",
                    "evidence_item_ids": ["missing-item"],
                    "evidence_sources": [],
                }
            )
            adapter = MockLLMAdapter([json.dumps(report_payload)])
            context.set("validated_items", [item])
            context.set("llm_adapter", adapter)

            report = analyze.run(context)
            audit = json.loads(context.paths["llm_audit_report"].read_text(encoding="utf-8"))

        self.assertEqual(len(report.risk_insights), 1)
        self.assertEqual(audit["sections"]["analysis_evidence"]["blocked_count"], 1)
        self.assertEqual(
            audit["sections"]["analysis_evidence"]["blocked_records"][0]["section"],
            "risk_insights",
        )

    def test_analyze_removes_risk_insight_with_low_risk_evidence_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            low_risk_item = StructuredNewsItem.model_validate(self._structured_payload())
            report_payload = analyze._rule_based_report(
                context,
                [low_risk_item],
            ).model_dump(mode="json")
            report_payload["risk_insights"] = [
                {
                    "title": "Overstated risk",
                    "level": "medium",
                    "summary": "This risk should not cite a low-risk item.",
                    "evidence_item_ids": [low_risk_item.id],
                    "evidence_sources": [
                        source.model_dump(mode="json")
                        for source in low_risk_item.evidence_sources
                    ],
                }
            ]
            report_payload["opportunity_insights"] = []
            context.set("validated_items", [low_risk_item])
            context.set("llm_adapter", MockLLMAdapter([json.dumps(report_payload)]))

            report = analyze.run(context)
            stored = json.loads(context.paths["report_sections"].read_text(encoding="utf-8"))
            audit = json.loads(context.paths["llm_audit_report"].read_text(encoding="utf-8"))

        self.assertEqual(report.risk_insights, [])
        self.assertEqual(stored["risk_insights"], [])
        repair = context.get("risk_opportunity_repair")
        self.assertEqual(repair["removed_count"], 1)
        self.assertEqual(
            repair["removed_records"][0]["removed_item_ids"],
            [low_risk_item.id],
        )
        self.assertIn("risk_opportunity_repair", audit["sections"])

    def test_analyze_falls_back_to_rule_report_on_invalid_llm_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            tracer = InMemoryTracer()
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            context.set("validated_items", [item])
            context.set("llm_adapter", MockLLMAdapter(["not json"]))
            runner = PipelineRunner(context, tracer=tracer)

            report = runner.run_step("analyze", analyze.run)
            stored = json.loads(context.paths["report_sections"].read_text(encoding="utf-8"))

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(context.get("analyze_mode"), "llm_fallback")
        self.assertEqual(report.top_events[0].item_id, "structured-1")
        self.assertEqual(stored["top_events"][0]["item_id"], "structured-1")
        self.assertEqual(metadata["call_count"], 4)
        self.assertEqual(metadata["business_error_types"], {"invalid_json": 1})
        self.assertEqual(metadata["fallback_count"], 1)

    def test_analyze_required_llm_raises_instead_of_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(
                run_id="run-test",
                config={"mode": {"llm": "llm"}},
            )
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            context.set("validated_items", [item])
            context.set("llm_adapter", MockLLMAdapter(["not json", "still not json"]))

            with self.assertRaisesRegex(ValueError, "invalid JSON"):
                analyze.run(context)

        self.assertIsNone(context.get("analyze_llm_fallbacks"))

    def test_analyze_falls_back_when_llm_report_fails_evidence_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            tracer = InMemoryTracer()
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["llm_audit_report"] = Path(tmp_dir) / "llm_audit_report.json"
            item = StructuredNewsItem.model_validate(self._structured_payload())
            report_payload = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            report_payload["top_events"][0]["evidence_sources"] = [
                {
                    "source_item_id": "raw-1",
                    "evidence_field": "content",
                    "evidence_quote": "Unsupported analysis quote.",
                    "claim": "Unsupported claim.",
                }
            ]
            context.set("validated_items", [item])
            context.set("llm_adapter", MockLLMAdapter([json.dumps(report_payload)]))
            runner = PipelineRunner(context, tracer=tracer)

            report = runner.run_step("analyze", analyze.run)
            audit = json.loads(context.paths["llm_audit_report"].read_text(encoding="utf-8"))

        metadata = tracer.events[1]["metadata"]["llm"]
        self.assertEqual(context.get("analyze_mode"), "llm_fallback")
        self.assertTrue(report.top_events[0].evidence_sources)
        self.assertEqual(metadata["business_error_types"], {"audit_failure": 1})
        self.assertEqual(metadata["fallback_error_types"], {"audit_failure": 1})
        self.assertEqual(audit["sections"]["analysis_evidence"]["blocked_count"], 0)

    def test_visualize_and_report_write_non_empty_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            context = PipelineContext(run_id="run-test")
            context.paths["charts_dir"] = Path(tmp_dir) / "charts"
            context.paths["report_sections"] = Path(tmp_dir) / "sections.json"
            context.paths["daily_report"] = Path(tmp_dir) / "daily_report.md"
            context.paths["memory_report"] = Path(tmp_dir) / "memory_report.json"
            structured = self._structured_payload()
            context.set("validated_items", [structured])
            context.set(
                "relevant_items",
                [
                    CleanNewsItem(
                        **self._raw_payload(
                            "raw-1",
                            content=(
                                "FULL ORIGINAL CONTEXT: OpenAI launches developer agents "
                                "with API workflow details, enterprise deployment notes, "
                                "and integration context that should reach the analyzer."
                            ),
                        ),
                        content_hash="hash-1",
                    )
                ],
            )
            context.historical_context = "Historical context for topic: ai agents"
            context.set(
                "memory_context",
                {
                    "topics": [
                        {
                            "topic": "AI Agents",
                            "entries": [
                                {
                                    "id": "history-1",
                                    "memory_item_id": "history-1",
                                    "title": "Previous OpenAI agent update",
                                    "published_at": "2026-05-19T10:00:00+00:00",
                                    "summary": "OpenAI previously expanded agent workflows.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 82,
                                    "risk_level": "low",
                                    "opportunity_level": "high",
                                }
                            ],
                        }
                    ],
                    "item_relationships": [
                        {
                            "item_id": "structured-1",
                            "topic": "AI Agents",
                            "relationship": "continuing",
                            "confidence": 0.8,
                            "matched_memory_item_ids": ["history-1"],
                        }
                    ],
                    "metadata_included_count": 1,
                    "fulltext_items": [
                        {
                            "memory_item_id": "history-1",
                            "title": "Previous OpenAI agent update",
                            "published_at": "2026-05-19T10:00:00+00:00",
                            "entities": ["OpenAI"],
                            "event_type": "model_release",
                        }
                    ],
                },
            )
            context.set(
                "memory_report",
                {
                    "strong_dedupe": {
                        "input_count": 2,
                        "filtered_count": 1,
                    },
                    "context_retrieval": {
                        "metadata_included_count": 1,
                    },
                    "fulltext_selection": {
                        "mode": "heuristic",
                        "requested_item_ids": ["history-1"],
                        "read_item_ids": ["history-1"],
                        "invalid_item_ids": [],
                    },
                },
            )

            refs = visualize.run(context)
            report = analyze.run(context)
            context.set("report_sections", report)
            markdown = generate_report.run(context)
            chart_sizes = [Path(ref).stat().st_size for ref in refs]

        self.assertEqual(len(refs), 2)
        self.assertTrue(all(size > 0 for size in chart_sizes))
        self.assertIn("重点事件", markdown)
        self.assertIn("趋势：", markdown)
        self.assertIn("[1]", markdown)
        self.assertIn("之所以值得展开", markdown)
        self.assertIn("Analyzer 已重新读取", markdown)
        self.assertIn("API", markdown)
        self.assertNotIn("- 背景：", markdown)
        self.assertNotIn("- 当前进展：", markdown)
        self.assertNotIn("- 影响分析：", markdown)
        self.assertIn("## 参考依据", markdown)
        self.assertNotIn("证据说明：", markdown)
        self.assertNotIn("证据原文：", markdown)
        self.assertNotIn("## 来源备注", markdown)
        self.assertIn("后续验证：", markdown)
        self.assertNotIn("后续关注：", markdown)
        self.assertIn("## 历史对照", markdown)
        self.assertIn("Memory 使用概览", markdown)
        self.assertIn("关联历史事件", markdown)
        self.assertIn("Previous OpenAI agent update", markdown)
        self.assertIn("历史依据：", markdown)
        self.assertIn("- 历史脉络：", markdown)
        self.assertNotIn("memory_item_id", markdown)
        self.assertNotIn("run_id", markdown)
        self.assertIn("结构化新闻判断说明表", markdown)
        self.assertIn(
            "| 序号 | 标题 | 来源 | 主题 | 事件 | 关注度判断 | 风险提示 | 机会提示 | URL |",
            markdown,
        )
        self.assertIn("88：开发者智能体模型发布影响开发者工作流", markdown)
        self.assertIn("低：材料未显示安全、合规或交付方面的突出风险", markdown)
        self.assertIn("高：开发者工作流改进强化企业采用机会", markdown)
        self.assertNotIn("| ID | 标题 |", markdown)
        self.assertNotIn("| structured-1 |", markdown)
        self.assertIn("https://example.com/a", markdown)

    def test_report_follow_up_questions_render_as_validation_actions(self) -> None:
        rendered = generate_report._render_follow_up_actions(
            [
                "第三方独立基准是否验证了璇玑A3 的功耗与算力声明？",
                "该芯片的量产交付节奏与上游供应链是否已达商业化规模？",
                "联想对机密数据与 Agent 访问控制的治理方案是什么？",
                "厂方在安全、车规认证方面的进展如何？",
                "赠送 Token 模式对客户 TCO 的影响如何？",
            ]
        )

        self.assertIn("跟踪第三方独立基准对璇玑A3 的功耗与算力声明的验证结果", rendered)
        self.assertIn("核验该芯片的量产交付节奏与上游供应链达到商业化规模的进展", rendered)
        self.assertIn("关注联想对机密数据与 Agent 访问控制的治理方案的具体披露", rendered)
        self.assertIn("跟踪厂方在安全、车规认证方面的进展", rendered)
        self.assertIn("评估赠送 Token 模式对客户 TCO 的影响", rendered)
        self.assertNotIn("？", rendered)
        self.assertNotIn("是否", rendered)

    def _raw_payload(
        self,
        item_id: str,
        *,
        title: str = "OpenAI launches developer agents",
        url: str = "https://example.com/a",
        summary: str = "OpenAI announced a model release for developer agents.",
        content: str = "The release improves agent workflows for developers and enterprise teams.",
        published_at: str = "2026-05-20T10:00:00+00:00",
    ) -> dict[str, object]:
        return {
            "id": item_id,
            "title": title,
            "source": "OpenAI News",
            "url": url,
            "published_at": published_at,
            "source_type": "blog",
            "language": "en",
            "summary": summary,
            "content": content,
        }

    def _structured_payload(self) -> dict[str, object]:
        return {
            "id": "structured-1",
            "title": "OpenAI launches developer agents",
            "source": "OpenAI News",
            "url": "https://example.com/a",
            "published_at": "2026-05-20T10:00:00+00:00",
            "source_type": "blog",
            "language": "en",
            "topic": "AI Agents",
            "entities": ["OpenAI"],
            "event_type": "model_release",
            "summary": "OpenAI announced a model release for developer agents.",
            "key_points": ["Developer agents were announced."],
            "sentiment": "positive",
            "impact_scope": "technology",
            "importance_score": 88,
            "importance_rationale": "开发者智能体模型发布影响开发者工作流。",
            "risk_level": "low",
            "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
            "opportunity_level": "high",
            "opportunity_rationale": "开发者工作流改进强化企业采用机会。",
            "evidence": ["The release improves agent workflows."],
            "evidence_sources": [
                {
                    "source_item_id": "raw-1",
                    "evidence_field": "content",
                    "evidence_quote": "The release improves agent workflows for developers and enterprise teams.",
                    "claim": "OpenAI announced a model release for developer agents.",
                }
            ],
            "content_hash": "hash-1",
        }


class PipelineIntegrationTests(unittest.TestCase):
    def test_small_file_backed_pipeline_is_repeatable_without_llm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {
                "raw": root / "data/raw.json",
                "cleaned": root / "data/cleaned.json",
                "relevant": root / "data/relevant.json",
                "structured": root / "data/structured.json",
                "validated": root / "data/validated.json",
                "relevance_report": root / "logs/relevance_report.json",
                "report_sections": root / "outputs/sections.json",
                "validation_report": root / "logs/validation.json",
                "charts_dir": root / "outputs/charts",
                "daily_report": root / "outputs/daily_report.md",
                "llm_audit_report": root / "logs/llm_audit_report.json",
                "memory": root / "memory/topic_index.json",
                "trace": root / "logs/run_trace.jsonl",
            }
            paths["raw"].parent.mkdir(parents=True)
            paths["raw"].write_text(
                json.dumps(
                    [
                        self._raw_payload("raw-1", title="OpenAI launches developer agents"),
                        self._raw_payload(
                            "raw-2",
                            title="Google releases Gemini model updates",
                            url="https://example.com/b",
                        ),
                        self._raw_payload(
                            "raw-3",
                            title="NVIDIA expands AI infrastructure",
                            url="https://example.com/c",
                        ),
                    ]
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                paths=paths,
                config={
                    "report_timezone": "Asia/Shanghai",
                    "paths": {name: str(path) for name, path in paths.items()},
                },
            )

            collect.run(context)
            clean.run(context)
            relevance.run(context)
            extract.run(context)
            validate.run(context)
            visualize.run(context)
            analyze.run(context)
            generate_report.run(context)

            # Repeat the deterministic steps once to catch accidental hidden state.
            clean.run(context)
            relevance.run(context)
            extract.run(context)
            validate.run(context)
            report_text = paths["daily_report"].read_text(encoding="utf-8")
            chart_exists = (paths["charts_dir"] / "topic_distribution.png").exists()

            self.assertTrue(paths["daily_report"].exists())
            self.assertIn("AI 洞察日报", report_text)
            self.assertTrue(paths["validation_report"].exists())
            self.assertTrue(paths["llm_audit_report"].exists())
            self.assertTrue(paths["relevant"].exists())
            self.assertTrue(paths["relevance_report"].exists())
            self.assertTrue(chart_exists)
            self.assertTrue(context.get("relevance_skill_validation").passed)
            self.assertTrue(context.get("news_extraction_skill_validation").passed)
            self.assertTrue(context.get("trend_analysis_skill_validation").passed)
            self.assertTrue(context.get("risk_detection_skill_validation").passed)

    def test_llm_prompts_include_skill_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            paths = {
                "raw": root / "data/raw.json",
                "cleaned": root / "data/cleaned.json",
                "relevant": root / "data/relevant.json",
                "structured": root / "data/structured.json",
                "validated": root / "data/validated.json",
                "relevance_report": root / "logs/relevance_report.json",
                "report_sections": root / "outputs/sections.json",
                "validation_report": root / "logs/validation.json",
                "charts_dir": root / "outputs/charts",
                "daily_report": root / "outputs/daily_report.md",
                "llm_audit_report": root / "logs/llm_audit_report.json",
                "memory": root / "memory/topic_index.json",
                "trace": root / "logs/run_trace.jsonl",
            }
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                paths=paths,
                config={"mode": {"llm": "auto"}},
            )
            item = StructuredNewsItem.model_validate(
                {
                    "id": "structured-1",
                    "title": "OpenAI launches developer agents",
                    "source": "Example Source",
                    "url": "https://example.com/a",
                    "published_at": "2026-05-20T10:00:00+00:00",
                    "source_type": "blog",
                    "language": "en",
                    "topic": "AI Agents",
                    "entities": ["OpenAI"],
                    "event_type": "model_release",
                    "summary": "OpenAI announced a model release for developer agents.",
                    "key_points": ["Developer agents were announced."],
                    "sentiment": "positive",
                    "impact_scope": "technology",
                    "importance_score": 88,
                    "importance_rationale": "开发者智能体模型发布影响开发者工作流。",
                    "risk_level": "low",
                    "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
                    "opportunity_level": "high",
                    "opportunity_rationale": "开发者工作流改进强化企业采用机会。",
                    "evidence": ["The release improves agent workflows."],
                    "evidence_sources": [
                        {
                            "source_item_id": "raw-1",
                            "evidence_field": "content",
                            "evidence_quote": "OpenAI launches developer agents affects developers, enterprise AI, and infrastructure.",
                            "claim": "OpenAI announced a model release for developer agents.",
                        }
                    ],
                    "content_hash": "hash-1",
                }
            )
            report = analyze._rule_based_report(context, [item]).model_dump(mode="json")
            adapter = MockLLMAdapter(
                [
                    json.dumps(
                        [
                            {
                                "item_id": "raw-1",
                                "title": "OpenAI launches developer agents",
                                "url": "https://example.com/a",
                                "published_at": "2026-05-20T10:00:00+00:00",
                                "content_hash": "hash-1",
                                "is_ai_related": True,
                                "relevance_score": 90,
                                "relevance_reason": "Directly covers AI agents.",
                                "relevance_evidence": ["OpenAI launches developer agents"],
                                "decision_source": "llm",
                            }
                        ]
                    ),
                    json.dumps([item.model_dump(mode="json")]),
                    json.dumps(report),
                ]
            )
            context.set("llm_adapter", adapter)
            context.set(
                "raw_items",
                [
                    RawNewsItem(
                        **self._raw_payload(
                            "raw-1",
                            title="OpenAI launches developer agents",
                        )
                    )
                ],
            )
            context.set("cleaned_items", clean.run(context))

            relevance.run(context)
            extract.run(context)
            validate.run(context)
            analyze.run(context)

            prompts = "\n\n".join(adapter.prompts)

        self.assertIn("Skill Context: ai_news_relevance", prompts)
        self.assertIn("Skill Context: news_extraction", prompts)
        self.assertIn("Skill Context: trend_analysis", prompts)
        self.assertIn("Skill Context: risk_detection", prompts)

    def test_pipeline_fails_when_no_same_day_items_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            raw_path.write_text(
                json.dumps(
                    [
                        self._raw_payload(
                            "raw-1",
                            title="OpenAI launches developer agents",
                            published_at="2026-05-18T10:00:00+00:00",
                        )
                    ]
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                paths={"raw": raw_path, "cleaned": Path(tmp_dir) / "cleaned.json"},
            )
            context.set("source_adapter", LocalJsonSourceAdapter(raw_path))

            collect.run(context)
            with self.assertRaisesRegex(ValueError, "no report-date news items remain after cleaning"):
                clean.run(context)

    def test_pipeline_fails_when_same_day_items_are_not_ai_relevant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            raw_path = Path(tmp_dir) / "raw.json"
            raw_path.write_text(
                json.dumps(
                    [
                        self._raw_payload(
                            "raw-1",
                            title="Valve raises Steam Deck prices by more than $200",
                            summary="The handheld gaming device now costs much more.",
                            content="The article is about gaming hardware pricing and market pressure.",
                        )
                    ]
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                run_date=date(2026, 5, 20),
                paths={
                    "raw": raw_path,
                    "cleaned": Path(tmp_dir) / "cleaned.json",
                    "relevant": Path(tmp_dir) / "relevant.json",
                    "relevance_report": Path(tmp_dir) / "relevance_report.json",
                },
            )
            context.set("source_adapter", LocalJsonSourceAdapter(raw_path))

            collect.run(context)
            clean.run(context)
            with self.assertRaisesRegex(
                ValueError,
                "no AI-relevant report-date news items remain after relevance filtering",
            ):
                relevance.run(context)

    def test_resolve_run_date_prefers_explicit_config_value(self) -> None:
        resolved = _resolve_run_date({"run_date": "2026-05-29"})

        self.assertEqual(resolved, date(2026, 5, 29))

    def test_resolve_run_date_defaults_to_current_day_in_report_timezone(self) -> None:
        frozen_now = datetime(2026, 5, 28, 16, 30, tzinfo=timezone.utc)

        with patch("src.main.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = frozen_now
            resolved = _resolve_run_date({"report_timezone": "Asia/Shanghai"})

        self.assertEqual(resolved, date(2026, 5, 29))

    def _raw_payload(
        self,
        item_id: str,
        *,
        title: str,
        url: str = "https://example.com/a",
        published_at: str = "2026-05-20T10:00:00+00:00",
        summary: str | None = None,
        content: str | None = None,
    ) -> dict[str, object]:
        summary_text = summary or f"{title} with agent and model implications."
        content_text = content or f"{title} affects developers, enterprise AI, and infrastructure."
        return {
            "id": item_id,
            "title": title,
            "source": "Example Source",
            "url": url,
            "published_at": published_at,
            "source_type": "blog",
            "language": "en",
            "summary": summary_text,
            "content": content_text,
        }


def _rss_feed(
    title: str,
    link: str,
    published: str = "Wed, 20 May 2026 10:00:00 GMT",
) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <item>
      <title>{title}</title>
      <link>{link}</link>
      <guid>{link}</guid>
      <pubDate>{published}</pubDate>
      <description><![CDATA[量子位 AI 摘要。]]></description>
    </item>
  </channel>
</rss>
"""


if __name__ == "__main__":
    unittest.main()
