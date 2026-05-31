"""日报分析 schema 的测试。"""

from __future__ import annotations

import unittest
from datetime import date

from pydantic import ValidationError

from src.schemas import (
    DailyInsightReport,
    DeepDiveSection,
    EvidenceSource,
    HistoricalComparison,
    MemoryUsageSummary,
    RiskInsight,
    RiskLevel,
    TopEvent,
    TrendInsight,
)


class ReportSchemaTests(unittest.TestCase):
    def test_top_event_strips_strings_and_validates_score(self) -> None:
        event = TopEvent(
            item_id=" item-1 ",
            title="  New model release  ",
            source=" OpenAI Blog ",
            importance_score=95,
            reason="Major developer impact",
            impact="May shift model adoption.",
        )

        self.assertEqual(event.item_id, "item-1")
        self.assertEqual(event.title, "New model release")
        self.assertEqual(event.source, "OpenAI Blog")

    def test_top_event_parses_evidence_sources(self) -> None:
        event = TopEvent(
            **{
                **self._top_event_payload(),
                "evidence_sources": [
                    {
                        "source_item_id": "raw-1",
                        "evidence_field": "content",
                        "evidence_quote": "OpenAI announced the model.",
                        "claim": "Major developer impact",
                    }
                ],
            }
        )

        self.assertIsInstance(event.evidence_sources[0], EvidenceSource)

    def test_daily_report_parses_date_and_nested_sections(self) -> None:
        report = DailyInsightReport(
            report_date="2026-05-28",
            title="AI Insight Daily",
            executive_summary="Model releases dominated today's AI news.",
            top_events=[self._top_event_payload()],
            trend_insights=[
                {
                    "title": "Model capability competition is heating up",
                    "scope": "technology",
                    "summary": "Multiple model releases point to faster iteration.",
                    "evidence_item_ids": ["item-1"],
                    "trend_state": "heating_up",
                    "historical_context_used": True,
                    "historical_evidence": [
                        {
                            "memory_item_id": "history-1",
                            "title": "Previous model update",
                            "published_at": "2026-05-27T10:00:00+00:00",
                            "reason": "It shares the same company and model release path.",
                        }
                    ],
                }
            ],
            risk_insights=[
                {
                    "title": "Safety scrutiny may rise",
                    "level": "medium",
                    "summary": "Rapid releases may invite more safety review.",
                    "evidence_item_ids": ["item-1"],
                }
            ],
            memory_usage={
                "relevant_candidate_count": 2,
                "strong_duplicate_filtered_count": 1,
                "retrieved_metadata_count": 3,
                "read_fulltext_count": 1,
                "adopted_historical_evidence_count": 1,
            },
            historical_comparisons=[
                {
                    "current_item_id": "item-1",
                    "current_event_title": "New model release",
                    "memory_item_id": "history-1",
                    "historical_event_title": "Previous model update",
                    "historical_event_date": "2026-05-27T10:00:00+00:00",
                    "relation_type": "continuing",
                    "relevance_strength": 82,
                    "rationale": "Same company and event type.",
                    "impact_on_today": "Supports a continuing trend judgment.",
                }
            ],
            chart_refs=["outputs/charts/topic_distribution.png"],
        )

        self.assertIsInstance(report.report_date, date)
        self.assertIsInstance(report.top_events[0], TopEvent)
        self.assertIsInstance(report.trend_insights[0], TrendInsight)
        self.assertEqual(report.trend_insights[0].trend_state.value, "heating_up")
        self.assertEqual(report.trend_insights[0].historical_evidence[0].memory_item_id, "history-1")
        self.assertIsInstance(report.risk_insights[0], RiskInsight)
        self.assertEqual(report.risk_insights[0].level, RiskLevel.MEDIUM)
        self.assertIsInstance(report.memory_usage, MemoryUsageSummary)
        self.assertIsInstance(report.historical_comparisons[0], HistoricalComparison)

    def test_deep_dive_section_requires_narrative_analysis(self) -> None:
        section = DeepDiveSection(
            item_id="item-1",
            narrative_analysis="这是一段面向读者的分析正文，解释背景、事实、影响和后续验证。",
            background="事件背景。",
            current_progress="今日进展。",
            impact_analysis="影响分析。",
        )

        self.assertIn("分析正文", section.narrative_analysis)

        with self.assertRaises(ValidationError):
            DeepDiveSection(
                item_id="item-1",
                background="事件背景。",
                current_progress="今日进展。",
                impact_analysis="影响分析。",
            )

    def test_trend_insight_defaults_to_new_state(self) -> None:
        insight = TrendInsight(
            title="Foundation Models new signal",
            scope="technology",
            summary="One model item appears without historical context.",
            evidence_item_ids=["item-1"],
        )

        self.assertEqual(insight.trend_state.value, "new")

    def test_trend_insight_rejects_invalid_state(self) -> None:
        with self.assertRaises(ValidationError):
            TrendInsight(
                title="Foundation Models strange state",
                scope="technology",
                summary="Invalid state should fail.",
                evidence_item_ids=["item-1"],
                trend_state="accelerating",
            )

    def test_daily_report_requires_at_least_one_top_event(self) -> None:
        with self.assertRaises(ValidationError):
            DailyInsightReport(
                report_date="2026-05-28",
                title="AI Insight Daily",
                executive_summary="No events.",
                top_events=[],
            )

    def test_top_event_rejects_score_above_one_hundred(self) -> None:
        payload = self._top_event_payload()
        payload["importance_score"] = 101

        with self.assertRaises(ValidationError):
            TopEvent(**payload)

    def test_daily_report_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            DailyInsightReport(
                report_date="2026-05-28",
                title="AI Insight Daily",
                executive_summary="Model releases dominated today's AI news.",
                top_events=[self._top_event_payload()],
                unexpected="field",
            )

    def _top_event_payload(self) -> dict[str, object]:
        return {
            "item_id": "item-1",
            "title": "New model release",
            "source": "OpenAI Blog",
            "importance_score": 95,
            "reason": "Major developer impact",
            "impact": "May shift model adoption.",
        }


if __name__ == "__main__":
    unittest.main()
