"""新闻 schema 的测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from src.schemas import (
    AI_NEWS_TOPICS,
    CleanNewsItem,
    EventType,
    Language,
    RawNewsItem,
    RelevanceAssessment,
    Sentiment,
    SourceType,
    StructuredNewsItem,
)


class NewsSchemaTests(unittest.TestCase):
    def test_raw_news_item_parses_basic_payload_and_strips_strings(self) -> None:
        item = RawNewsItem(
            id=" raw-1 ",
            title="  OpenAI releases a new model  ",
            source=" OpenAI Blog ",
            url=" https://example.com/news ",
            published_at="2026-05-27T10:00:00+08:00",
            source_type="blog",
            language="en",
            summary="  short summary  ",
        )

        self.assertEqual(item.id, "raw-1")
        self.assertEqual(item.title, "OpenAI releases a new model")
        self.assertEqual(item.source, "OpenAI Blog")
        self.assertEqual(item.url, "https://example.com/news")
        self.assertEqual(item.source_type, SourceType.BLOG)
        self.assertEqual(item.language, Language.EN)
        self.assertIsInstance(item.published_at, datetime)

    def test_clean_news_item_requires_content_hash(self) -> None:
        with self.assertRaises(ValidationError):
            CleanNewsItem(
                id="clean-1",
                title="OpenAI releases a new model",
                source="OpenAI Blog",
                url="https://example.com/news",
                published_at="2026-05-27T10:00:00+08:00",
                source_type="blog",
                language="en",
            )

    def test_structured_news_item_parses_enums_datetime_and_score(self) -> None:
        item = StructuredNewsItem(
            id="structured-1",
            title="OpenAI releases a new model",
            source="OpenAI Blog",
            url="https://example.com/news",
            published_at="2026-05-27T10:00:00+08:00",
            source_type="blog",
            language="en",
            topic="Foundation Models",
            entities=[" OpenAI ", "GPT"],
            event_type="model_release",
            summary="OpenAI released a new model.",
            key_points=["New model released", "Developer impact"],
            sentiment="positive",
            impact_scope="technology",
            importance_score=90,
            importance_rationale="OpenAI 模型发布会影响开发者和企业的模型选型。",
            risk_level="low",
            risk_rationale="材料未显示安全、合规或交付方面的突出风险。",
            opportunity_level="high",
            opportunity_rationale="模型发布强化了开发者生态和企业采用机会。",
            evidence=["OpenAI announced the model in its official blog."],
            content_hash="hash-1",
        )

        self.assertEqual(item.entities, ["OpenAI", "GPT"])
        self.assertEqual(item.topic, "Foundation Models")
        self.assertEqual(item.event_type, EventType.MODEL_RELEASE)
        self.assertEqual(item.sentiment, Sentiment.POSITIVE)
        self.assertEqual(item.importance_score, 90)
        self.assertIsInstance(item.published_at, datetime)

    def test_structured_news_item_parses_evidence_sources(self) -> None:
        payload = self._structured_payload()
        payload["evidence_sources"] = [
            {
                "source_item_id": "raw-1",
                "evidence_field": "content",
                "evidence_quote": "OpenAI announced the model in its official blog.",
                "claim": "OpenAI released a new model.",
            }
        ]

        item = StructuredNewsItem(**payload)

        self.assertEqual(item.evidence_sources[0].source_item_id, "raw-1")
        self.assertEqual(item.evidence_sources[0].evidence_field, "content")

    def test_structured_news_item_rejects_score_above_one_hundred(self) -> None:
        payload = self._structured_payload()
        payload["importance_score"] = 101

        with self.assertRaises(ValidationError):
            StructuredNewsItem(**payload)

    def test_structured_news_item_requires_judgment_rationales(self) -> None:
        payload = self._structured_payload()
        del payload["importance_rationale"]

        with self.assertRaises(ValidationError):
            StructuredNewsItem(**payload)

    def test_structured_news_item_rejects_unknown_fields(self) -> None:
        payload = self._structured_payload()
        payload["unexpected"] = "field"

        with self.assertRaises(ValidationError):
            StructuredNewsItem(**payload)

    def test_structured_news_item_normalizes_legacy_topic_aliases(self) -> None:
        payload = self._structured_payload()
        payload["topic"] = "Open Source AI"

        item = StructuredNewsItem(**payload)

        self.assertEqual(item.topic, "Developer Tools and Open Source")

    def test_structured_news_item_rejects_out_of_taxonomy_topic(self) -> None:
        payload = self._structured_payload()
        payload["topic"] = "AI Culture"

        with self.assertRaises(ValidationError):
            StructuredNewsItem(**payload)

    def test_project_topic_taxonomy_is_stable(self) -> None:
        self.assertEqual(
            list(AI_NEWS_TOPICS),
            [
                "AI Agents",
                "Foundation Models",
                "AI Infrastructure",
                "AI Applications",
                "Developer Tools and Open Source",
                "AI Safety and Governance",
                "AI Research",
                "AI Business and Market",
            ],
        )

    def test_relevance_assessment_parses_basic_payload(self) -> None:
        assessment = RelevanceAssessment(
            item_id="raw-1",
            title="OpenAI releases a new model",
            url="https://example.com/news",
            published_at="2026-05-27T10:00:00+08:00",
            content_hash="hash-1",
            is_ai_related=True,
            relevance_score=90,
            relevance_reason="The item is directly about an AI model release.",
            relevance_evidence=["OpenAI releases a new model"],
            decision_source="rule_based",
        )

        self.assertTrue(assessment.is_ai_related)
        self.assertEqual(assessment.relevance_score, 90)
        self.assertEqual(assessment.decision_source, "rule_based")

    def _structured_payload(self) -> dict[str, object]:
        return {
            "id": "structured-1",
            "title": "OpenAI releases a new model",
            "source": "OpenAI Blog",
            "url": "https://example.com/news",
            "published_at": "2026-05-27T10:00:00+08:00",
            "source_type": "blog",
            "language": "en",
            "topic": "Foundation Models",
            "entities": ["OpenAI", "GPT"],
            "event_type": "model_release",
            "summary": "OpenAI released a new model.",
            "key_points": ["New model released", "Developer impact"],
            "sentiment": "positive",
            "impact_scope": "technology",
            "importance_score": 90,
            "importance_rationale": "OpenAI 模型发布会影响开发者和企业的模型选型。",
            "risk_level": "low",
            "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
            "opportunity_level": "high",
            "opportunity_rationale": "模型发布强化了开发者生态和企业采用机会。",
            "evidence": ["OpenAI announced the model in its official blog."],
            "evidence_sources": [],
            "content_hash": "hash-1",
        }


if __name__ == "__main__":
    unittest.main()
