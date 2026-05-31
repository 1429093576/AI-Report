"""MemoryManager tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.harness import MemoryManager
from src.schemas import StructuredNewsItem


class MemoryManagerTests(unittest.TestCase):
    def test_append_creates_topic_index_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "memory" / "topic_index.json"
            manager = MemoryManager(path)

            added = manager.append([self._item("item-1", topic="AI Model")])
            payload = json.loads(path.read_text(encoding="utf-8"))
            item_path = Path(tmp_dir) / "memory" / "items" / "item-1.json"
            item_payload = json.loads(item_path.read_text(encoding="utf-8"))

        self.assertEqual(added, 1)
        self.assertEqual(payload["schema_version"], 2)
        self.assertIn("topics", payload)
        self.assertIn("foundation models", payload["topics"])
        self.assertEqual(payload["topics"]["foundation models"][0]["id"], "item-1")
        self.assertEqual(
            payload["topics"]["foundation models"][0]["memory_item_id"],
            "item-1",
        )
        self.assertTrue(
            payload["topics"]["foundation models"][0]["memory_item_path"].endswith(
                "memory/items/item-1.json"
            )
        )
        self.assertEqual(
            payload["topics"]["foundation models"][0]["evidence_sources"][0]["source_item_id"],
            "raw-item-1",
        )
        self.assertIn(
            "模型发布",
            payload["topics"]["foundation models"][0]["importance_rationale"],
        )
        self.assertIn(
            "突出风险",
            payload["topics"]["foundation models"][0]["risk_rationale"],
        )
        self.assertIn(
            "采用机会",
            payload["topics"]["foundation models"][0]["opportunity_rationale"],
        )
        self.assertEqual(item_payload["memory_item_id"], "item-1")
        self.assertEqual(item_payload["validated"]["id"], "item-1")
        self.assertIsNone(item_payload["clean"])
        self.assertIsNone(item_payload["relevant"])

    def test_append_writes_full_memory_item_payload_with_source_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "memory" / "topic_index.json"
            manager = MemoryManager(path)
            clean_item = self._clean_item("raw-item-1")

            added = manager.append(
                [self._item("item-1", topic="AI Model")],
                clean_items=[clean_item],
                relevant_items=[clean_item],
                run_id="run-test",
                run_date="2026-05-27",
                artifact_paths={
                    "cleaned": root / "data" / "cleaned.json",
                    "relevant": root / "data" / "relevant.json",
                    "validated": root / "data" / "validated.json",
                },
            )
            item_payload = json.loads(
                (root / "memory" / "items" / "item-1.json").read_text(encoding="utf-8")
            )

        self.assertEqual(added, 1)
        self.assertEqual(item_payload["schema_version"], 1)
        self.assertEqual(item_payload["run_id"], "run-test")
        self.assertEqual(item_payload["run_date"], "2026-05-27")
        self.assertEqual(item_payload["clean"]["id"], "raw-item-1")
        self.assertEqual(item_payload["relevant"]["content"], "Full clean article body.")
        self.assertEqual(item_payload["metadata"]["content_source"], "article_html")
        self.assertTrue(item_payload["source_artifact_paths"]["cleaned"].endswith("cleaned.json"))

    def test_append_deduplicates_by_id_url_or_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")

            first = self._item("item-1", url="https://example.com/a", hash_="hash-a")
            same_id = self._item("item-1", url="https://example.com/b", hash_="hash-b")
            same_url = self._item("item-2", url="https://example.com/a", hash_="hash-c")
            same_hash = self._item("item-3", url="https://example.com/c", hash_="hash-a")

            added = manager.append([first, same_id, same_url, same_hash])

        self.assertEqual(added, 1)

    def test_append_accepts_dict_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")

            added = manager.append([self._item("item-1").model_dump(mode="json")])

        self.assertEqual(added, 1)

    def test_retrieve_returns_recent_items_sorted_by_published_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append(
                [
                    self._item(
                        "item-old",
                        published_at="2026-05-25T10:00:00+00:00",
                        title="Older model update",
                    ),
                    self._item(
                        "item-new",
                        published_at="2026-05-27T10:00:00+00:00",
                        title="Newer model update",
                    ),
                ]
            )

            context = manager.retrieve("AI Model", window_days=365, limit=2)

        self.assertIn("Historical context for topic: foundation models", context)
        self.assertLess(
            context.index("Newer model update"),
            context.index("Older model update"),
        )

    def test_retrieve_applies_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append(
                [
                    self._item("item-1", title="First"),
                    self._item("item-2", title="Second", hash_="hash-2"),
                ]
            )

            context = manager.retrieve("ai model", window_days=365, limit=1)

        self.assertEqual(context.count("attention="), 1)

    def test_retrieve_entries_returns_structured_context_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append([self._item("item-1", topic="AI Model")])

            entries = manager.retrieve_entries("AI Model", window_days=365, limit=5)

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["id"], "item-1")
        self.assertEqual(entries[0]["topic"], "foundation models")
        self.assertEqual(entries[0]["source"], "OpenAI Blog")
        self.assertEqual(entries[0]["entities"], ["OpenAI"])
        self.assertEqual(entries[0]["event_type"], "model_release")
        self.assertEqual(entries[0]["summary"], "Summary for OpenAI releases a new model.")
        self.assertEqual(entries[0]["memory_item_id"], "item-1")
        self.assertTrue(entries[0]["memory_item_path"].endswith("items/item-1.json"))
        self.assertEqual(entries[0]["source_item_id"], "raw-item-1")

    def test_retrieve_excludes_current_item_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append(
                [
                    self._item("item-current", url="https://example.com/current"),
                    self._item(
                        "item-history",
                        url="https://example.com/history",
                        hash_="hash-history",
                        title="Historical model update",
                    ),
                ]
            )

            context = manager.retrieve(
                "ai model",
                window_days=365,
                limit=5,
                exclude_keys={"item-current", "https://example.com/current"},
            )

        self.assertNotIn("item-current", context)
        self.assertIn("Historical model update", context)

    def test_retrieve_returns_empty_for_unknown_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")

            context = manager.retrieve("missing")

        self.assertEqual(context, "")

    def test_strong_duplicate_matches_by_id_url_or_content_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append(
                [
                    self._item(
                        "item-1",
                        url="https://example.com/history",
                        hash_="hash-history",
                    )
                ]
            )

            matches = manager.strong_duplicate_matches(
                [
                    {
                        "id": "item-current",
                        "title": "OpenAI releases a new model",
                        "source": "OpenAI Blog",
                        "url": "https://example.com/history",
                        "published_at": "2026-05-27T10:00:00+00:00",
                        "source_type": "blog",
                        "language": "en",
                        "summary": "Summary.",
                        "content": "Content.",
                        "content_hash": "hash-current",
                    },
                    {
                        "id": "item-hash",
                        "title": "OpenAI releases another model",
                        "source": "OpenAI Blog",
                        "url": "https://example.com/current",
                        "published_at": "2026-05-27T10:00:00+00:00",
                        "source_type": "blog",
                        "language": "en",
                        "summary": "Summary.",
                        "content": "Content.",
                        "content_hash": "hash-history",
                    },
                ]
            )

        self.assertIn("item-current", matches)
        self.assertIn("item-hash", matches)
        self.assertEqual(matches["item-current"]["matched_keys"][0]["field"], "url")
        self.assertEqual(matches["item-hash"]["matched_keys"][0]["field"], "content_hash")
        self.assertEqual(matches["item-current"]["memory_entries"][0]["id"], "item-1")
        self.assertEqual(
            matches["item-current"]["memory_entries"][0]["memory_item_id"],
            "item-1",
        )

    def test_strong_duplicate_matches_new_index_source_item_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = MemoryManager(Path(tmp_dir) / "topic_index.json")
            manager.append([self._item("item-1")])

            matches = manager.strong_duplicate_matches(
                [
                    {
                        "id": "raw-item-1",
                        "title": "OpenAI releases a new model",
                        "source": "OpenAI Blog",
                        "url": "https://example.com/current",
                        "published_at": "2026-05-27T10:00:00+00:00",
                        "source_type": "blog",
                        "language": "en",
                        "summary": "Summary.",
                        "content": "Content.",
                        "content_hash": "hash-current",
                    }
                ]
            )

        self.assertIn("raw-item-1", matches)
        self.assertEqual(matches["raw-item-1"]["matched_keys"][0]["field"], "id")
        self.assertEqual(
            matches["raw-item-1"]["memory_entries"][0]["source_item_id"],
            "raw-item-1",
        )

    def test_retrieve_rejects_invalid_window_or_limit(self) -> None:
        manager = MemoryManager("unused.json")

        with self.assertRaises(ValueError):
            manager.retrieve("ai model", window_days=-1)

        with self.assertRaises(ValueError):
            manager.retrieve("ai model", limit=0)

    def test_load_supports_legacy_topic_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "topic_index.json"
            path.write_text(
                json.dumps(
                    {
                        "ai model": [
                            {
                                "id": "item-1",
                                "title": "Legacy item",
                                "source": "Source",
                                "published_at": "2026-05-27T10:00:00+00:00",
                                "summary": "Stored before topics wrapper.",
                                "importance_score": 80,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            manager = MemoryManager(path)

            context = manager.retrieve("AI Model", window_days=365)

        self.assertIn("Legacy item", context)

    def _item(
        self,
        item_id: str,
        *,
        topic: str = "AI Model",
        url: str | None = None,
        hash_: str | None = None,
        published_at: str = "2026-05-27T10:00:00+00:00",
        title: str = "OpenAI releases a new model",
    ) -> StructuredNewsItem:
        return StructuredNewsItem(
            id=item_id,
            title=title,
            source="OpenAI Blog",
            url=url or f"https://example.com/{item_id}",
            published_at=published_at,
            source_type="blog",
            language="en",
            topic=topic,
            entities=["OpenAI"],
            event_type="model_release",
            summary=f"Summary for {title}.",
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
                    "claim": f"Summary for {title}.",
                }
            ],
            content_hash=hash_ or f"hash-{item_id}",
        )

    def _clean_item(self, item_id: str) -> dict[str, object]:
        return {
            "id": item_id,
            "title": "OpenAI releases a new model",
            "source": "OpenAI Blog",
            "url": "https://example.com/item-1",
            "published_at": "2026-05-27T10:00:00+00:00",
            "source_type": "blog",
            "language": "en",
            "summary": "Clean summary.",
            "content": "Full clean article body.",
            "metadata": {
                "content_source": "article_html",
                "source_name": "fixture",
            },
            "content_hash": "hash-item-1",
        }


if __name__ == "__main__":
    unittest.main()
