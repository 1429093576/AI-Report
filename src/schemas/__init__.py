"""Pydantic schema 与共享数据合同。"""

from .base import (
    SCHEMA_VERSION,
    NonEmptyStr,
    SchemaBase,
    Score0To100,
    StrippedStr,
)
from .chart import ChartDataPoint, ChartSpec
from .enums import (
    ChartType,
    EventType,
    ImpactScope,
    Language,
    OpportunityLevel,
    RiskLevel,
    Sentiment,
    SourceType,
    TrendState,
    ValidationSeverity,
)
from .io import dump_json_model, export_json_schema, load_json_model
from .news import CleanNewsItem, EvidenceSource, RawNewsItem, StructuredNewsItem
from .relevance import RelevanceAssessment
from .report import (
    DailyInsightReport,
    DeepDiveSection,
    HistoricalComparison,
    HistoricalEvidenceReference,
    MemoryUsageSummary,
    OpportunityInsight,
    RiskInsight,
    TopEvent,
    TrendInsight,
)
from .topics import AI_NEWS_TOPICS, allowed_topic_values, normalize_topic_label
from .validation import ValidationIssue, ValidationResult

__all__ = [
    "SCHEMA_VERSION",
    "AI_NEWS_TOPICS",
    "ChartDataPoint",
    "ChartSpec",
    "ChartType",
    "CleanNewsItem",
    "DailyInsightReport",
    "DeepDiveSection",
    "EventType",
    "EvidenceSource",
    "HistoricalComparison",
    "HistoricalEvidenceReference",
    "ImpactScope",
    "Language",
    "MemoryUsageSummary",
    "NonEmptyStr",
    "OpportunityInsight",
    "OpportunityLevel",
    "RiskLevel",
    "RiskInsight",
    "SchemaBase",
    "Score0To100",
    "Sentiment",
    "SourceType",
    "StrippedStr",
    "RawNewsItem",
    "RelevanceAssessment",
    "StructuredNewsItem",
    "TopEvent",
    "TrendInsight",
    "TrendState",
    "ValidationIssue",
    "ValidationResult",
    "ValidationSeverity",
    "allowed_topic_values",
    "dump_json_model",
    "export_json_schema",
    "load_json_model",
    "normalize_topic_label",
]
