"""Soft Memory similarity tests."""

from __future__ import annotations

import unittest

from src.harness.memory_similarity import assess_soft_similarity
from src.schemas import StructuredNewsItem


class MemorySimilarityTests(unittest.TestCase):
    def test_assess_soft_similarity_marks_likely_duplicate(self) -> None:
        result = assess_soft_similarity(
            [
                self._item(
                    "item-current",
                    title="OpenAI releases a new model",
                    summary="OpenAI released a new model.",
                )
            ],
            {
                "Foundation Models": [
                    {
                        "id": "item-history",
                        "title": "OpenAI releases a new model",
                        "summary": "OpenAI released a new model.",
                        "topic": "Foundation Models",
                        "entities": ["OpenAI"],
                        "event_type": "model_release",
                    }
                ]
            },
        )

        self.assertEqual(result["relationships"]["likely_duplicate"], 1)
        self.assertEqual(result["items"][0]["relationship"], "likely_duplicate")
        self.assertEqual(result["matches"][0]["memory_item_id"], "item-history")

    def test_assess_soft_similarity_marks_continuing_when_novelty_exists(self) -> None:
        result = assess_soft_similarity(
            [
                self._item(
                    "item-current",
                    title="OpenAI expands GPT-5.2 rollout",
                    summary="OpenAI expands the API rollout after its model launch.",
                )
            ],
            {
                "Foundation Models": [
                    {
                        "id": "item-history",
                        "title": "OpenAI releases GPT-5.1 model",
                        "summary": "OpenAI released a GPT-5.1 model update.",
                        "topic": "Foundation Models",
                        "entities": ["OpenAI"],
                        "event_type": "model_release",
                    }
                ]
            },
        )

        self.assertEqual(result["relationships"]["continuing"], 1)
        self.assertEqual(result["items"][0]["relationship"], "continuing")
        self.assertIn("novelty_signal", result["matches"][0]["matched_signals"])

    def test_assess_soft_similarity_marks_new_without_candidates(self) -> None:
        result = assess_soft_similarity(
            [self._item("item-current")],
            {"AI Agents": []},
        )

        self.assertEqual(result["relationships"]["new"], 1)
        self.assertEqual(result["items"][0]["relationship"], "new")
        self.assertEqual(result["match_count"], 0)

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
            topic="Foundation Models",
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
            content_hash=f"hash-{item_id}",
        )


if __name__ == "__main__":
    unittest.main()
