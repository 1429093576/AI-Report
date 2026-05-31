"""日报分析内容的 schema。"""

from __future__ import annotations

from datetime import date

from pydantic import Field

from .base import NonEmptyStr, SchemaBase, Score0To100
from .enums import ImpactScope, OpportunityLevel, RiskLevel, TrendState
from .news import EvidenceSource


class TopEvent(SchemaBase):
    """日报中的重点事件条目。"""

    item_id: NonEmptyStr
    title: NonEmptyStr
    source: NonEmptyStr
    importance_score: Score0To100
    reason: NonEmptyStr
    impact: NonEmptyStr
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)


class DeepDiveSection(SchemaBase):
    """重要事件的背景与影响分析。"""

    item_id: NonEmptyStr
    narrative_analysis: NonEmptyStr
    historical_context_note: NonEmptyStr | None = None
    background: NonEmptyStr
    current_progress: NonEmptyStr
    involved_entities: list[NonEmptyStr] = Field(default_factory=list)
    impact_analysis: NonEmptyStr
    follow_up_questions: list[NonEmptyStr] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)


class HistoricalEvidenceReference(SchemaBase):
    """趋势判断中使用的历史依据。"""

    memory_item_id: NonEmptyStr | None = None
    title: NonEmptyStr
    published_at: NonEmptyStr
    reason: NonEmptyStr


class TrendInsight(SchemaBase):
    """基于结构化新闻和历史上下文形成的趋势判断。"""

    title: NonEmptyStr
    scope: ImpactScope
    summary: NonEmptyStr
    evidence_item_ids: list[NonEmptyStr] = Field(default_factory=list)
    trend_state: TrendState = TrendState.NEW
    historical_context_used: bool = False
    historical_evidence: list[HistoricalEvidenceReference] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)


class RiskInsight(SchemaBase):
    """日报中的风险提示。"""

    title: NonEmptyStr
    level: RiskLevel
    summary: NonEmptyStr
    evidence_item_ids: list[NonEmptyStr] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)


class OpportunityInsight(SchemaBase):
    """日报中的机会提示。"""

    title: NonEmptyStr
    level: OpportunityLevel
    summary: NonEmptyStr
    evidence_item_ids: list[NonEmptyStr] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)


class MemoryUsageSummary(SchemaBase):
    """日报中展示的 Memory 使用概览。"""

    relevant_candidate_count: int = Field(default=0, ge=0)
    strong_duplicate_filtered_count: int = Field(default=0, ge=0)
    retrieved_metadata_count: int = Field(default=0, ge=0)
    read_fulltext_count: int = Field(default=0, ge=0)
    adopted_historical_evidence_count: int = Field(default=0, ge=0)


class HistoricalComparison(SchemaBase):
    """今日事件与历史 Memory 事件之间的可读对照。"""

    current_item_id: NonEmptyStr
    current_event_title: NonEmptyStr
    memory_item_id: NonEmptyStr | None = None
    historical_event_title: NonEmptyStr
    historical_event_date: NonEmptyStr
    relation_type: NonEmptyStr
    relevance_strength: Score0To100
    rationale: NonEmptyStr
    impact_on_today: NonEmptyStr


class DailyInsightReport(SchemaBase):
    """Analyzer 输出给 Report Generator 的日报结构。"""

    report_date: date
    title: NonEmptyStr
    executive_summary: NonEmptyStr
    top_events: list[TopEvent] = Field(min_length=1)
    deep_dives: list[DeepDiveSection] = Field(default_factory=list)
    trend_insights: list[TrendInsight] = Field(default_factory=list)
    risk_insights: list[RiskInsight] = Field(default_factory=list)
    opportunity_insights: list[OpportunityInsight] = Field(default_factory=list)
    memory_usage: MemoryUsageSummary = Field(default_factory=MemoryUsageSummary)
    historical_comparisons: list[HistoricalComparison] = Field(default_factory=list)
    chart_refs: list[NonEmptyStr] = Field(default_factory=list)
