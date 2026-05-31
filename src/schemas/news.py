"""新闻数据在 Pipeline 中流转时使用的核心 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from .base import NonEmptyStr, SchemaBase, Score0To100, StrippedStr
from .enums import (
    EventType,
    ImpactScope,
    Language,
    OpportunityLevel,
    RiskLevel,
    Sentiment,
    SourceType,
)
from .topics import normalize_topic_label


class RawNewsItem(SchemaBase):
    """采集模块输出的原始新闻条目。

    该模型只约束进入系统的最小统一形态，不承担清洗、去重或 AI 抽取职责。
    """

    id: NonEmptyStr
    title: NonEmptyStr
    source: NonEmptyStr
    url: NonEmptyStr
    published_at: datetime
    source_type: SourceType = SourceType.UNKNOWN
    language: Language = Language.UNKNOWN
    summary: StrippedStr = ""
    content: StrippedStr = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CleanNewsItem(RawNewsItem):
    """清洗去重模块输出的新闻条目。

    `content_hash` 是基于标题归一化生成的稳定身份指纹，也用于后续 Memory
    写入前的重复检查。
    """

    content_hash: NonEmptyStr


class EvidenceSource(SchemaBase):
    """A concrete source quote used to support a generated claim."""

    source_item_id: NonEmptyStr
    evidence_field: NonEmptyStr
    evidence_quote: NonEmptyStr
    claim: StrippedStr = ""


class StructuredNewsItem(SchemaBase):
    """AI 抽取模块输出的结构化新闻条目。

    该模型面向分析、可视化、报告生成和记忆写入，保留 `url` 与
    `content_hash` 是为了让结构化结论仍可追溯到原始来源。
    """

    id: NonEmptyStr
    title: NonEmptyStr
    source: NonEmptyStr
    url: NonEmptyStr
    published_at: datetime
    source_type: SourceType
    language: Language
    topic: NonEmptyStr
    entities: list[NonEmptyStr] = Field(default_factory=list)
    event_type: EventType
    summary: NonEmptyStr
    key_points: list[NonEmptyStr] = Field(default_factory=list)
    sentiment: Sentiment
    impact_scope: ImpactScope
    importance_score: Score0To100
    importance_rationale: NonEmptyStr
    risk_level: RiskLevel
    risk_rationale: NonEmptyStr
    opportunity_level: OpportunityLevel
    opportunity_rationale: NonEmptyStr
    evidence: list[NonEmptyStr] = Field(default_factory=list)
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)
    content_hash: NonEmptyStr

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: object) -> str:
        """Keep topic labels on the canonical project taxonomy."""

        return normalize_topic_label(value)
