"""Tests for external hook scripts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hooks import on_error, post_relevance, post_validate, pre_process
from src.adapters import MockLLMAdapter
from src.harness import PipelineContext
from src.schemas import StructuredNewsItem


class HookScriptTests(unittest.TestCase):
    def test_pre_process_loads_historical_context_for_configured_topics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [
                                {
                                    "id": "item-1",
                                    "title": "Previous model update",
                                    "source": "OpenAI Blog",
                                    "published_at": "2026-05-27T10:00:00+00:00",
                                    "url": "https://example.com/history",
                                    "summary": "Historical update.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 88,
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "topics": ["Foundation Models"],
                        "memory_window_days": 365,
                        "max_history_items_per_topic": 3,
                        "report_path": str(report_path),
                    }
                },
            )

            result = pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIs(result, context)
        self.assertIn("Previous model update", context.historical_context)
        self.assertEqual(context.get("historical_context_topics"), ["Foundation Models"])
        memory_context = context.get("memory_context")
        self.assertEqual(memory_context["retrieved_count"], 1)
        self.assertEqual(memory_context["topics"][0]["topic"], "Foundation Models")
        self.assertEqual(memory_context["topics"][0]["entries"][0]["id"], "item-1")
        self.assertEqual(
            memory_context["topics"][0]["entries"][0]["entities"],
            ["OpenAI"],
        )
        self.assertEqual(report["context_retrieval"]["retrieved_count"], 1)
        self.assertEqual(report["context_retrieval"]["topics"][0]["entry_ids"], ["item-1"])
        self.assertEqual(report["soft_similarity"]["status"], "skipped")
        self.assertEqual(report["soft_similarity"]["candidate_count"], 0)

    def test_pre_process_clamps_metadata_items_per_topic_to_ten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            entries = [
                {
                    "id": f"history-{index:02d}",
                    "memory_item_id": f"history-{index:02d}",
                    "title": f"Historical update {index:02d}",
                    "source": "OpenAI Blog",
                    "published_at": f"2026-05-27T{index:02d}:00:00+00:00",
                    "url": f"https://example.com/history-{index:02d}",
                    "summary": f"Historical update summary {index:02d}.",
                    "entities": ["OpenAI"],
                    "event_type": "model_release",
                    "importance_score": 80 + index,
                }
                for index in range(12)
            ]
            memory_path.write_text(
                json.dumps({"topics": {"foundation models": entries}}),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "topics": ["Foundation Models"],
                        "memory_window_days": 365,
                        "max_history_items_per_topic": 50,
                        "report_path": str(report_path),
                    }
                },
            )

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        memory_context = context.get("memory_context")
        self.assertEqual(memory_context["retrieved_count"], 10)
        self.assertEqual(memory_context["metadata_included_count"], 10)
        self.assertEqual(len(memory_context["topics"][0]["entries"]), 10)
        self.assertEqual(
            report["context_retrieval"]["budget"]["max_history_items_per_topic"],
            10,
        )
        self.assertNotIn("Historical update 01", context.historical_context)

    def test_pre_process_limits_metadata_context_total_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            entries = [
                {
                    "id": f"history-{index}",
                    "memory_item_id": f"history-{index}",
                    "title": f"History {index}",
                    "source": "OpenAI Blog",
                    "published_at": f"2026-05-27T{12 - index:02d}:00:00+00:00",
                    "url": f"https://example.com/history-{index}",
                    "summary": f"Summary {index}.",
                    "entities": ["OpenAI"],
                    "event_type": "model_release",
                    "importance_score": 90 - index,
                }
                for index in range(3)
            ]
            budget = len(pre_process._format_context("Foundation Models", [entries[0]]))
            memory_path.write_text(
                json.dumps({"topics": {"foundation models": entries}}),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "topics": ["Foundation Models"],
                        "memory_window_days": 365,
                        "max_metadata_context_chars": budget,
                        "report_path": str(report_path),
                    }
                },
            )

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertLessEqual(len(context.historical_context), budget)
        self.assertIn("History 0", context.historical_context)
        self.assertNotIn("History 1", context.historical_context)
        metadata_context = context.get("memory_context")["metadata_context"]
        self.assertEqual(metadata_context["included_count"], 1)
        self.assertEqual(metadata_context["retrieved_count"], 3)
        self.assertTrue(metadata_context["truncated"])
        self.assertEqual(
            report["context_retrieval"]["budget"]["max_metadata_context_chars"],
            budget,
        )
        self.assertTrue(report["context_retrieval"]["truncated"])

    def test_pre_process_uses_state_topics_and_clears_context_when_missing(self) -> None:
        context = PipelineContext(
            run_id="run-test",
            historical_context="old context",
        )

        result = pre_process.run(context)

        self.assertIs(result, context)
        self.assertEqual(context.historical_context, "")
        self.assertEqual(context.get("historical_context_topics"), [])
        self.assertEqual(context.get("memory_context")["retrieved_count"], 0)
        self.assertEqual(
            context.get("memory_context")["soft_similarity"]["status"],
            "skipped",
        )

    def test_pre_process_can_derive_topics_from_validated_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [
                                {
                                    "id": "item-1",
                                    "title": "Previous model update",
                                    "source": "OpenAI Blog",
                                    "published_at": "2026-05-27T10:00:00+00:00",
                                    "summary": "Historical update.",
                                    "importance_score": 88,
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [self._item("item-current")])

            pre_process.run(context)

        self.assertIn("Previous model update", context.historical_context)
        self.assertEqual(context.get("historical_context_topics"), ["Foundation Models"])

    def test_pre_process_excludes_current_validated_items_from_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            current = self._item("item-current")
            historical = self._item("item-history")
            historical.title = "Historical model update"
            current_entry = {
                "id": current.id,
                "title": current.title,
                "source": current.source,
                "url": current.url,
                "published_at": current.published_at.isoformat(),
                "summary": current.summary,
                "importance_score": current.importance_score,
                "content_hash": current.content_hash,
            }
            historical_entry = {
                "id": historical.id,
                "title": historical.title,
                "source": historical.source,
                "url": historical.url,
                "published_at": historical.published_at.isoformat(),
                "summary": historical.summary,
                "importance_score": historical.importance_score,
                "content_hash": historical.content_hash,
            }
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [current_entry, historical_entry]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [current])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertNotIn("item-current", context.historical_context)
        self.assertIn("Historical model update", context.historical_context)
        self.assertEqual(context.get("memory_context")["retrieved_count"], 1)
        self.assertIn("item-current", report["context_retrieval"]["excluded_current_item_keys"])

    def test_pre_process_marks_likely_duplicate_soft_similarity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            current = self._item(
                "item-current",
                title="OpenAI releases a new model",
                summary="OpenAI released a new model.",
            )
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                {
                                    "id": "item-history",
                                    "title": "OpenAI releases a new model",
                                    "source": "OpenAI Blog",
                                    "url": "https://example.com/history",
                                    "published_at": "2026-05-26T10:00:00+00:00",
                                    "topic": "Foundation Models",
                                    "summary": "OpenAI released a new model.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 88,
                                    "content_hash": "hash-history",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [current])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        relationship = context.get("memory_context")["item_relationships"][0]
        self.assertEqual(relationship["relationship"], "likely_duplicate")
        self.assertEqual(relationship["matched_memory_item_ids"], ["item-history"])
        self.assertEqual(report["soft_similarity"]["match_count"], 1)
        self.assertEqual(
            report["soft_similarity"]["matches"][0]["relationship"],
            "likely_duplicate",
        )

    def test_pre_process_marks_continuing_soft_similarity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            report_path = Path(tmp_dir) / "memory_report.json"
            current = self._item(
                "item-current",
                title="OpenAI releases GPT-5.2 API rollout",
                summary="OpenAI expands its model API rollout for enterprise customers.",
            )
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                {
                                    "id": "item-history",
                                    "title": "OpenAI releases GPT-5.1 model",
                                    "source": "OpenAI Blog",
                                    "url": "https://example.com/history",
                                    "published_at": "2026-05-26T10:00:00+00:00",
                                    "topic": "Foundation Models",
                                    "summary": "OpenAI released a GPT-5.1 model update.",
                                    "entities": ["OpenAI"],
                                    "event_type": "model_release",
                                    "importance_score": 88,
                                    "content_hash": "hash-history",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [current])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        relationship = context.get("memory_context")["item_relationships"][0]
        self.assertEqual(relationship["relationship"], "continuing")
        self.assertEqual(report["soft_similarity"]["relationships"]["continuing"], 1)
        self.assertIn(
            "novelty_signal",
            report["soft_similarity"]["matches"][0]["matched_signals"],
        )

    def test_pre_process_merges_existing_memory_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "topic_index.json"
            report_path = root / "memory_report.json"
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [
                                {
                                    "id": "item-history",
                                    "title": "Historical model update",
                                    "source": "OpenAI Blog",
                                    "url": "https://example.com/history",
                                    "published_at": "2026-05-27T10:00:00+00:00",
                                    "summary": "Historical update.",
                                    "importance_score": 88,
                                    "content_hash": "hash-history",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            report_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "run_id": "run-test",
                        "run_date": "2026-05-27",
                        "paths": {},
                        "stages": [],
                        "strong_dedupe": {
                            "status": "succeeded",
                            "filtered_count": 1,
                        },
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "topics": ["Foundation Models"],
                        "memory_window_days": 365,
                    }
                },
            )

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["strong_dedupe"]["filtered_count"], 1)
        self.assertEqual(report["context_retrieval"]["retrieved_count"], 1)
        self.assertEqual(report["stages"][0]["name"], "pre_analyze_context_retrieval")

    def test_pre_process_heuristically_loads_selected_fulltext_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            report_path = root / "logs" / "memory_report.json"
            item_path = root / "memory" / "items" / "history-1.json"
            memory_path.parent.mkdir(parents=True)
            item_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                self._memory_entry(
                                    "history-1",
                                    memory_item_path=item_path.as_posix(),
                                )
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            item_path.write_text(
                json.dumps(
                    self._memory_item_payload(
                        "history-1",
                        content="Historical full text about the model launch.",
                    )
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [self._item("item-current")])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIn("Selected Memory fulltext context", context.historical_context)
        self.assertIn("Historical full text about the model launch.", context.historical_context)
        memory_context = context.get("memory_context")
        self.assertEqual(memory_context["fulltext_selection"]["mode"], "heuristic")
        self.assertEqual(memory_context["fulltext_items"][0]["memory_item_id"], "history-1")
        self.assertEqual(report["fulltext_selection"]["selected_item_ids"], ["history-1"])
        self.assertEqual(report["fulltext_selection"]["read_item_ids"], ["history-1"])

    def test_pre_process_uses_llm_fulltext_selector_and_validates_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            report_path = root / "logs" / "memory_report.json"
            first_path = root / "memory" / "items" / "history-1.json"
            second_path = root / "memory" / "items" / "history-2.json"
            memory_path.parent.mkdir(parents=True)
            first_path.parent.mkdir(parents=True)
            entries = [
                self._memory_entry("history-1", memory_item_path=first_path.as_posix()),
                self._memory_entry(
                    "history-2",
                    title="Second history",
                    memory_item_path=second_path.as_posix(),
                    importance_score=70,
                    hash_="hash-history-2",
                ),
            ]
            memory_path.write_text(
                json.dumps({"topics": {"foundation models": entries}}),
                encoding="utf-8",
            )
            first_path.write_text(
                json.dumps(self._memory_item_payload("history-1", content="First full text.")),
                encoding="utf-8",
            )
            second_path.write_text(
                json.dumps(self._memory_item_payload("history-2", content="Second full text.")),
                encoding="utf-8",
            )
            adapter = MockLLMAdapter(
                [json.dumps({"memory_item_ids": ["history-2", "bad-id", "history-1"]})]
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "mode": {"llm": "auto"},
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                        "max_fulltext_items": 1,
                    },
                },
            )
            context.set("llm_adapter", adapter)
            context.set("validated_items", [self._item("item-current")])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIn("memory_item_ids", adapter.prompts[0])
        self.assertIn("Second full text.", context.historical_context)
        self.assertNotIn("First full text.", context.historical_context)
        self.assertEqual(report["fulltext_selection"]["mode"], "llm")
        self.assertEqual(report["fulltext_selection"]["requested_item_ids"], ["history-2", "bad-id", "history-1"])
        self.assertEqual(report["fulltext_selection"]["selected_item_ids"], ["history-2"])
        self.assertEqual(report["fulltext_selection"]["invalid_item_ids"], ["bad-id"])
        self.assertEqual(report["fulltext_selection"]["overflow_item_ids"], ["history-1"])

    def test_pre_process_clamps_fulltext_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            report_path = root / "logs" / "memory_report.json"
            items_dir = root / "memory" / "items"
            memory_path.parent.mkdir(parents=True)
            items_dir.mkdir(parents=True)
            item_ids = [f"history-{index}" for index in range(6)]
            entries = []
            for index, item_id in enumerate(item_ids):
                item_path = items_dir / f"{item_id}.json"
                entries.append(
                    self._memory_entry(
                        item_id,
                        title=f"History {index}",
                        memory_item_path=item_path.as_posix(),
                        importance_score=90 - index,
                        hash_=f"hash-{item_id}",
                    )
                )
                item_path.write_text(
                    json.dumps(
                        self._memory_item_payload(
                            item_id,
                            content=f"{item_id} " + ("x" * 2500),
                        )
                    ),
                    encoding="utf-8",
                )
            memory_path.write_text(
                json.dumps({"topics": {"foundation models": entries}}),
                encoding="utf-8",
            )
            adapter = MockLLMAdapter([json.dumps({"memory_item_ids": item_ids})])
            context = PipelineContext(
                run_id="run-test",
                config={
                    "mode": {"llm": "auto"},
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                        "max_fulltext_items": 10,
                        "max_fulltext_chars_per_item": 5000,
                    },
                },
            )
            context.set("llm_adapter", adapter)
            context.set("validated_items", [self._item("item-current")])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(len(report["fulltext_selection"]["selected_item_ids"]), 5)
        self.assertEqual(report["fulltext_selection"]["overflow_item_ids"], ["history-5"])
        self.assertEqual(report["fulltext_selection"]["budget"]["max_fulltext_items"], 5)
        self.assertEqual(
            report["fulltext_selection"]["budget"]["max_fulltext_chars_per_item"],
            2000,
        )
        self.assertTrue(report["fulltext_selection"]["truncated"])
        fulltext_items = context.get("memory_context")["fulltext_items"]
        self.assertEqual(len(fulltext_items), 5)
        self.assertTrue(all(item["content_chars"] <= 2000 for item in fulltext_items))

    def test_pre_process_rejects_unsafe_fulltext_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            report_path = root / "logs" / "memory_report.json"
            outside_path = root / "outside.json"
            memory_path.parent.mkdir(parents=True)
            outside_path.write_text(
                json.dumps(self._memory_item_payload("history-1", content="Unsafe text.")),
                encoding="utf-8",
            )
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                self._memory_entry(
                                    "history-1",
                                    memory_item_path=outside_path.as_posix(),
                                )
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(memory_path),
                        "report_path": str(report_path),
                        "memory_window_days": 365,
                    }
                },
            )
            context.set("validated_items", [self._item("item-current")])

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertNotIn("Unsafe text.", context.historical_context)
        self.assertEqual(report["fulltext_selection"]["status"], "skipped")
        self.assertEqual(report["fulltext_selection"]["candidate_count"], 0)

    def test_pre_process_reads_parent_memory_snapshot_for_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            latest_memory_path = root / "latest" / "memory" / "topic_index.json"
            snapshot_memory_path = root / "state" / "runs" / "parent" / "artifacts" / "memory.json"
            report_path = root / "logs" / "memory_report.json"
            latest_memory_path.parent.mkdir(parents=True)
            snapshot_memory_path.parent.mkdir(parents=True)
            latest_memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                {
                                    "id": "latest-history",
                                    "title": "Latest Memory event",
                                    "source": "OpenAI Blog",
                                    "published_at": "2026-05-27T10:00:00+00:00",
                                    "summary": "Latest context.",
                                    "importance_score": 90,
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            snapshot_memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "foundation models": [
                                {
                                    "id": "snapshot-history",
                                    "title": "Parent snapshot event",
                                    "source": "OpenAI Blog",
                                    "published_at": "2026-05-27T10:00:00+00:00",
                                    "summary": "Snapshot context.",
                                    "importance_score": 90,
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                config={
                    "memory": {
                        "path": str(latest_memory_path),
                        "report_path": str(report_path),
                        "topics": ["Foundation Models"],
                        "memory_window_days": 365,
                    }
                },
            )
            context.set(
                "memory_replay_snapshot",
                {
                    "status": "available",
                    "source_run_id": "parent",
                    "memory_path": snapshot_memory_path.as_posix(),
                    "items_dir": None,
                },
            )

            pre_process.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIn("Parent snapshot event", context.historical_context)
        self.assertNotIn("Latest Memory event", context.historical_context)
        self.assertEqual(
            report["context_retrieval"]["memory_source"]["mode"],
            "parent_snapshot",
        )
        self.assertEqual(
            report["context_retrieval"]["memory_source"]["source_run_id"],
            "parent",
        )

    def test_post_validate_appends_validated_items_to_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "topic_index.json"
            report_path = root / "memory_report.json"
            context = PipelineContext(
                run_id="run-test",
                config={"memory": {"path": str(memory_path), "report_path": str(report_path)}},
            )
            context.set("validated_items", [self._item("item-1")])
            context.set("cleaned_items", [self._clean_item("raw-item-1")])
            context.set("relevant_items", [self._clean_item("raw-item-1")])
            self._mark_daily_report_generated(context, root)

            result = post_validate.run(context)
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
            item_payload = json.loads(
                (root / "items" / "item-1.json").read_text(encoding="utf-8")
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIs(result, context)
        self.assertEqual(context.get("memory_items_added"), 1)
        self.assertIn("foundation models", payload["topics"])
        self.assertEqual(payload["topics"]["foundation models"][0]["memory_item_id"], "item-1")
        self.assertEqual(item_payload["run_id"], "run-test")
        self.assertEqual(item_payload["clean"]["id"], "raw-item-1")
        self.assertEqual(item_payload["relevant"]["content"], "Official announcement.")
        self.assertEqual(report["memory_write"]["added_count"], 1)
        self.assertEqual(report["memory_write"]["skipped_count"], 0)
        self.assertEqual(report["stages"][-1]["name"], "post_validate_memory_write")

    def test_post_validate_uses_validate_state_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            memory_path = Path(tmp_dir) / "topic_index.json"
            context = PipelineContext(
                run_id="run-test",
                paths={"memory": memory_path},
            )
            context.set("validate", [self._item("item-1")])
            self._mark_daily_report_generated(context, Path(tmp_dir))

            post_validate.run(context)

        self.assertEqual(context.get("memory_items_added"), 1)

    def test_post_validate_skips_when_daily_report_has_not_succeeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "topic_index.json"
            report_path = root / "memory_report.json"
            context = PipelineContext(
                run_id="run-test",
                config={"memory": {"path": str(memory_path), "report_path": str(report_path)}},
            )
            context.set("validated_items", [self._item("item-1")])

            post_validate.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertFalse(memory_path.exists())
        self.assertEqual(context.get("memory_items_added"), 0)
        self.assertEqual(report["memory_write"]["status"], "skipped")
        self.assertEqual(report["memory_write"]["skipped_reasons"], ["daily_report_missing"])

    def test_post_validate_skips_memory_write_for_replay_or_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "topic_index.json"
            report_path = root / "memory_report.json"
            context = PipelineContext(
                run_id="run-test",
                config={"memory": {"path": str(memory_path), "report_path": str(report_path)}},
            )
            context.set("run_mode", "resume")
            context.set("validated_items", [self._item("item-1")])
            self._mark_daily_report_generated(context, root)

            post_validate.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertFalse(memory_path.exists())
        self.assertEqual(context.get("memory_items_added"), 0)
        self.assertEqual(report["memory_write"]["status"], "skipped")
        self.assertEqual(report["memory_write"]["skipped_reasons"], ["non_fresh_run"])

    def test_post_validate_reads_source_items_from_artifact_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            cleaned_path = root / "data" / "cleaned.json"
            relevant_path = root / "data" / "relevant.json"
            validated_path = root / "data" / "validated.json"
            cleaned_path.parent.mkdir(parents=True)
            cleaned_path.write_text(
                json.dumps([self._clean_item("raw-item-1")]),
                encoding="utf-8",
            )
            relevant_path.write_text(
                json.dumps([self._clean_item("raw-item-1")]),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "memory": memory_path,
                    "cleaned": cleaned_path,
                    "relevant": relevant_path,
                    "validated": validated_path,
                    "memory_report": root / "logs" / "memory_report.json",
                },
            )
            context.add_artifact("cleaned", cleaned_path)
            context.add_artifact("relevant", relevant_path)
            context.add_artifact("validated", validated_path)
            context.set("validated_items", [self._item("item-1")])
            self._mark_daily_report_generated(context, root)

            post_validate.run(context)
            item_payload = json.loads(
                (root / "memory" / "items" / "item-1.json").read_text(encoding="utf-8")
            )

        self.assertEqual(item_payload["clean"]["id"], "raw-item-1")
        self.assertEqual(item_payload["relevant"]["id"], "raw-item-1")
        self.assertTrue(item_payload["source_artifact_paths"]["validated"].endswith("validated.json"))

    def test_post_validate_records_zero_when_no_items_exist(self) -> None:
        context = PipelineContext(run_id="run-test")

        result = post_validate.run(context)

        self.assertIs(result, context)
        self.assertEqual(context.get("memory_items_added"), 0)

    def test_post_relevance_filters_memory_strong_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            relevant_path = root / "data" / "relevant.json"
            report_path = root / "logs" / "memory_report.json"
            duplicate = self._clean_item("raw-duplicate", url="https://example.com/old")
            fresh = self._clean_item("raw-fresh", url="https://example.com/fresh")
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [
                                {
                                    "id": "memory-1",
                                    "title": "Previous model update",
                                    "source": "OpenAI Blog",
                                    "url": "https://example.com/old",
                                    "published_at": "2026-05-26T10:00:00+00:00",
                                    "summary": "Historical update.",
                                    "importance_score": 88,
                                    "content_hash": "hash-old",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "relevant": relevant_path,
                    "memory": memory_path,
                    "memory_report": report_path,
                },
            )
            context.set("relevant_items", [duplicate, fresh])

            result = post_relevance.run(context)
            stored_relevant = json.loads(relevant_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertIs(result, context)
        self.assertEqual([item["id"] for item in stored_relevant], ["raw-fresh"])
        self.assertEqual(context.get("relevant_count"), 1)
        self.assertEqual(context.get("memory_strong_duplicate_count"), 1)
        self.assertEqual(report["strong_dedupe"]["filtered_count"], 1)
        self.assertEqual(report["strong_dedupe"]["status"], "succeeded")
        self.assertEqual(report["paths"]["memory"], memory_path.as_posix())
        self.assertEqual(report["paths"]["relevant"], relevant_path.as_posix())
        self.assertEqual(
            report["stages"][0]["name"],
            "post_relevance_strong_dedupe",
        )
        self.assertIn("soft_similarity", report)
        self.assertIn("context_retrieval", report)
        self.assertIn("fulltext_selection", report)
        self.assertIn("memory_write", report)
        self.assertEqual(
            report["strong_dedupe"]["filtered_items"][0]["matched_keys"][0]["field"],
            "url",
        )

    def test_post_relevance_fails_when_all_items_are_memory_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "topic_index.json"
            report_path = root / "logs" / "memory_report.json"
            duplicate = self._clean_item("raw-duplicate", hash_="hash-old")
            memory_path.parent.mkdir(parents=True)
            memory_path.write_text(
                json.dumps(
                    {
                        "topics": {
                            "ai model": [
                                {
                                    "id": "memory-1",
                                    "title": "Previous model update",
                                    "source": "OpenAI Blog",
                                    "url": "https://example.com/old",
                                    "published_at": "2026-05-26T10:00:00+00:00",
                                    "summary": "Historical update.",
                                    "importance_score": 88,
                                    "content_hash": "hash-old",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "relevant": root / "data" / "relevant.json",
                    "memory": memory_path,
                    "memory_report": report_path,
                },
            )
            context.set("relevant_items", [duplicate])

            with self.assertRaisesRegex(
                ValueError,
                "no non-duplicate AI-relevant report-date news items remain",
            ):
                post_relevance.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["strong_dedupe"]["input_count"], 1)
        self.assertEqual(report["strong_dedupe"]["kept_count"], 0)
        self.assertEqual(report["stages"][-1]["status"], "failed")
        self.assertEqual(report["errors"][0]["code"], "all_relevant_items_filtered")

    def test_post_relevance_warns_when_memory_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            memory_path = root / "memory" / "missing.json"
            relevant_path = root / "data" / "relevant.json"
            report_path = root / "logs" / "memory_report.json"
            context = PipelineContext(
                run_id="run-test",
                paths={
                    "relevant": relevant_path,
                    "memory": memory_path,
                    "memory_report": report_path,
                },
            )
            context.set("relevant_items", [self._clean_item("raw-fresh")])

            post_relevance.run(context)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            stored_relevant = json.loads(relevant_path.read_text(encoding="utf-8"))

        self.assertEqual(report["strong_dedupe"]["filtered_count"], 0)
        self.assertEqual(report["warnings"][0]["code"], "memory_not_found")
        self.assertEqual([item["id"] for item in stored_relevant], ["raw-fresh"])

    def test_on_error_stores_error_details(self) -> None:
        context = PipelineContext(run_id="run-test")
        error = RuntimeError("pipeline failed")

        result = on_error.run(context, error)

        self.assertIs(result, context)
        self.assertEqual(
            context.get("last_error"),
            {"type": "RuntimeError", "message": "pipeline failed"},
        )

    def _item(
        self,
        item_id: str,
        *,
        title: str = "OpenAI releases a new model",
        summary: str = "OpenAI released a new model.",
    ) -> StructuredNewsItem:
        return StructuredNewsItem(
            id=item_id,
            title=title,
            source="OpenAI Blog",
            url=f"https://example.com/{item_id}",
            published_at="2026-05-27T10:00:00+00:00",
            source_type="blog",
            language="en",
            topic="AI Model",
            entities=["OpenAI"],
            event_type="model_release",
            summary=summary,
            key_points=["New model released"],
            sentiment="positive",
            impact_scope="technology",
            importance_score=90,
            importance_rationale="OpenAI 模型发布会影响开发者和企业的模型选型。",
            risk_level="low",
            risk_rationale="材料未显示安全、合规或交付方面的突出风险。",
            opportunity_level="high",
            opportunity_rationale="模型发布强化了开发者生态和企业采用机会。",
            evidence=["Official announcement."],
            evidence_sources=[
                {
                    "source_item_id": item_id.replace("item-", "raw-item-"),
                    "evidence_field": "content",
                    "evidence_quote": "Official announcement.",
                    "claim": summary,
                }
            ],
            content_hash=f"hash-{item_id}",
        )

    def _clean_item(
        self,
        item_id: str,
        *,
        url: str | None = None,
        hash_: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": item_id,
            "title": "OpenAI releases a new model",
            "source": "OpenAI Blog",
            "url": url or f"https://example.com/{item_id}",
            "published_at": "2026-05-27T10:00:00+00:00",
            "source_type": "blog",
            "language": "en",
            "summary": "OpenAI released a new model.",
            "content": "Official announcement.",
            "metadata": {},
            "content_hash": hash_ or f"hash-{item_id}",
        }

    def _memory_entry(
        self,
        item_id: str,
        *,
        title: str = "OpenAI releases a new model",
        memory_item_path: str,
        importance_score: int = 90,
        hash_: str = "hash-history-1",
    ) -> dict[str, object]:
        return {
            "id": item_id,
            "memory_item_id": item_id,
            "memory_item_path": memory_item_path,
            "title": title,
            "source": "OpenAI Blog",
            "url": f"https://example.com/{item_id}",
            "published_at": "2026-05-26T10:00:00+00:00",
            "topic": "Foundation Models",
            "summary": "OpenAI released a new model.",
            "entities": ["OpenAI"],
            "event_type": "model_release",
            "importance_score": importance_score,
            "importance_rationale": "OpenAI 模型发布会影响开发者和企业的模型选型。",
            "risk_level": "low",
            "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
            "opportunity_level": "high",
            "opportunity_rationale": "模型发布强化了开发者生态和企业采用机会。",
            "content_hash": hash_,
        }

    def _memory_item_payload(
        self,
        item_id: str,
        *,
        content: str,
    ) -> dict[str, object]:
        return {
            "schema_version": 1,
            "memory_item_id": item_id,
            "run_id": "run-history",
            "run_date": "2026-05-26",
            "topic": "Foundation Models",
            "validated": {
                "id": item_id,
                "title": "OpenAI releases a new model",
                "source": "OpenAI Blog",
                "url": f"https://example.com/{item_id}",
                "published_at": "2026-05-26T10:00:00+00:00",
                "source_type": "blog",
                "language": "en",
                "topic": "Foundation Models",
                "entities": ["OpenAI"],
                "event_type": "model_release",
                "summary": "OpenAI released a new model.",
                "key_points": ["New model released"],
                "sentiment": "positive",
                "impact_scope": "technology",
                "importance_score": 90,
                "importance_rationale": "OpenAI 模型发布会影响开发者和企业的模型选型。",
                "risk_level": "low",
                "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
                "opportunity_level": "high",
                "opportunity_rationale": "模型发布强化了开发者生态和企业采用机会。",
                "evidence": ["Official announcement."],
                "content_hash": f"hash-{item_id}",
            },
            "clean": self._clean_item(f"raw-{item_id}"),
            "relevant": {
                **self._clean_item(f"raw-{item_id}"),
                "content": content,
            },
            "metadata": {},
            "source_artifact_paths": {
                "validated": "state/runs/run-history/artifacts/validated.json"
            },
        }

    def _mark_daily_report_generated(
        self,
        context: PipelineContext,
        root: Path,
    ) -> None:
        path = root / "outputs" / "daily_report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Daily Report\n", encoding="utf-8")
        context.add_artifact("daily_report", path)


if __name__ == "__main__":
    unittest.main()
