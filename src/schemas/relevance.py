"""Schemas for AI news relevance assessments."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import NonEmptyStr, SchemaBase, Score0To100


class RelevanceAssessment(SchemaBase):
    """Decision record for whether a cleaned item belongs in the AI daily report."""

    item_id: NonEmptyStr
    title: NonEmptyStr
    url: NonEmptyStr
    published_at: datetime
    content_hash: NonEmptyStr
    is_ai_related: bool
    relevance_score: Score0To100
    relevance_reason: NonEmptyStr
    relevance_evidence: list[NonEmptyStr] = Field(default_factory=list)
    decision_source: NonEmptyStr
