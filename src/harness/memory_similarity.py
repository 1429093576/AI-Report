"""Deterministic soft-similarity signals for Memory context."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from src.schemas import StructuredNewsItem
from src.schemas.topics import coerce_topic_key


RELATIONSHIP_NEW = "new"
RELATIONSHIP_RELATED = "related_context"
RELATIONSHIP_CONTINUING = "continuing"
RELATIONSHIP_DUPLICATE = "likely_duplicate"

_RELATIONSHIP_ORDER = {
    RELATIONSHIP_DUPLICATE: 3,
    RELATIONSHIP_CONTINUING: 2,
    RELATIONSHIP_RELATED: 1,
    RELATIONSHIP_NEW: 0,
}
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]")
_SPECIFIC_TOKEN_RE = re.compile(
    r"(?:gpt|claude|gemini|llama|mistral|opus|sonnet|haiku|codex)?\d+(?:\.\d+)*[a-z]*"
    r"|\$?\d+(?:\.\d+)?(?:b|m|bn|million|billion|%)?"
)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "ai",
    "artificial",
    "intelligence",
    "new",
    "news",
    "update",
    "updates",
}
_NOVELTY_MARKERS = (
    "adds",
    "added",
    "after",
    "begin",
    "begins",
    "expands",
    "expansion",
    "follow up",
    "follow-up",
    "now",
    "rollout",
    "ships",
    "shipping",
    "starts",
    "updated",
    "upgrade",
    "upgrades",
    "version",
    "新增",
    "更新",
    "升级",
    "上线",
    "落地",
    "开始",
    "进一步",
)


def assess_soft_similarity(
    current_items: list[StructuredNewsItem | dict[str, Any]],
    topic_entries: dict[str, list[dict[str, Any]]],
    *,
    max_matches_per_item: int = 3,
) -> dict[str, Any]:
    """Compare current items with same-topic Memory entries without deleting items."""

    items = [
        item if isinstance(item, StructuredNewsItem) else StructuredNewsItem.model_validate(item)
        for item in current_items
    ]
    max_matches = max(1, max_matches_per_item)
    matches: list[dict[str, Any]] = []
    item_relationships: list[dict[str, Any]] = []
    relationship_counts = {
        RELATIONSHIP_NEW: 0,
        RELATIONSHIP_RELATED: 0,
        RELATIONSHIP_CONTINUING: 0,
        RELATIONSHIP_DUPLICATE: 0,
    }
    candidate_count = 0

    for item in items:
        entries = _entries_for_item(item, topic_entries)
        assessments: list[dict[str, Any]] = []
        for entry in entries:
            candidate_count += 1
            assessment = _assess_pair(item, entry)
            if assessment["relationship"] != RELATIONSHIP_NEW:
                assessments.append(assessment)

        assessments.sort(
            key=lambda current: (
                _RELATIONSHIP_ORDER[current["relationship"]],
                current["confidence"],
            ),
            reverse=True,
        )
        selected = assessments[:max_matches]
        matches.extend(selected)
        best = selected[0] if selected else None
        relationship = str(best["relationship"]) if best else RELATIONSHIP_NEW
        relationship_counts[relationship] += 1
        item_relationships.append(
            {
                "item_id": item.id,
                "title": item.title,
                "topic": item.topic,
                "relationship": relationship,
                "confidence": round(float(best["confidence"]), 3) if best else 0.0,
                "matched_memory_item_ids": [
                    str(match["memory_item_id"])
                    for match in selected
                    if match.get("memory_item_id")
                ],
            }
        )

    status = "succeeded" if items else "skipped"
    return {
        "status": status,
        "item_count": len(items),
        "candidate_count": candidate_count,
        "match_count": len(matches),
        "matched_item_count": sum(
            1
            for item in item_relationships
            if item["relationship"] != RELATIONSHIP_NEW
        ),
        "relationships": relationship_counts,
        "items": item_relationships,
        "matches": matches,
    }


def empty_soft_similarity(status: str = "skipped") -> dict[str, Any]:
    """Return an empty soft-similarity payload."""

    return {
        "status": status,
        "item_count": 0,
        "candidate_count": 0,
        "match_count": 0,
        "matched_item_count": 0,
        "relationships": {
            RELATIONSHIP_NEW: 0,
            RELATIONSHIP_RELATED: 0,
            RELATIONSHIP_CONTINUING: 0,
            RELATIONSHIP_DUPLICATE: 0,
        },
        "items": [],
        "matches": [],
    }


def _entries_for_item(
    item: StructuredNewsItem,
    topic_entries: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    item_topic = coerce_topic_key(item.topic)
    for topic, entries in topic_entries.items():
        if coerce_topic_key(topic) == item_topic:
            return entries
    return []


def _assess_pair(item: StructuredNewsItem, entry: dict[str, Any]) -> dict[str, Any]:
    item_topic = coerce_topic_key(item.topic)
    entry_topic = coerce_topic_key(str(entry.get("topic") or item.topic))
    same_topic = item_topic == entry_topic
    entity_overlap = _entity_overlap(item.entities, entry.get("entities") or [])
    event_match = item.event_type.value == str(entry.get("event_type") or "")
    title_similarity = _text_similarity(item.title, str(entry.get("title") or ""))
    summary_similarity = _text_similarity(item.summary, str(entry.get("summary") or ""))
    combined_similarity = _text_similarity(
        f"{item.title} {item.summary}",
        f"{entry.get('title') or ''} {entry.get('summary') or ''}",
    )
    text_similarity = max(title_similarity, summary_similarity, combined_similarity)
    novelty = _has_novelty_signal(item, entry, text_similarity)
    confidence = _confidence(
        same_topic=same_topic,
        entity_overlap=entity_overlap,
        event_match=event_match,
        text_similarity=text_similarity,
        novelty=novelty,
    )
    relationship = _relationship(
        same_topic=same_topic,
        entity_overlap=entity_overlap,
        event_match=event_match,
        title_similarity=title_similarity,
        summary_similarity=summary_similarity,
        combined_similarity=combined_similarity,
        confidence=confidence,
        novelty=novelty,
    )
    signals = _signals(
        same_topic=same_topic,
        entity_overlap=entity_overlap,
        event_match=event_match,
        title_similarity=title_similarity,
        summary_similarity=summary_similarity,
        combined_similarity=combined_similarity,
        novelty=novelty,
    )
    return {
        "current_item_id": item.id,
        "current_title": item.title,
        "memory_item_id": entry.get("id"),
        "memory_title": entry.get("title"),
        "topic": item.topic,
        "relationship": relationship,
        "confidence": round(confidence, 3),
        "matched_signals": signals,
        "scores": {
            "entity_overlap": round(entity_overlap, 3),
            "title_similarity": round(title_similarity, 3),
            "summary_similarity": round(summary_similarity, 3),
            "combined_similarity": round(combined_similarity, 3),
            "event_type_match": event_match,
            "same_topic": same_topic,
            "novelty_signal": novelty,
        },
        "reason": _reason(relationship, signals),
    }


def _relationship(
    *,
    same_topic: bool,
    entity_overlap: float,
    event_match: bool,
    title_similarity: float,
    summary_similarity: float,
    combined_similarity: float,
    confidence: float,
    novelty: bool,
) -> str:
    high_text = (
        title_similarity >= 0.72
        or summary_similarity >= 0.72
        or combined_similarity >= 0.78
    )
    medium_text = (
        title_similarity >= 0.42
        or summary_similarity >= 0.42
        or combined_similarity >= 0.50
    )
    if same_topic and entity_overlap >= 0.5 and event_match and high_text and not novelty:
        return RELATIONSHIP_DUPLICATE
    if (
        same_topic
        and entity_overlap > 0
        and (event_match or medium_text)
        and (novelty or confidence >= 0.55)
    ):
        return RELATIONSHIP_CONTINUING
    if same_topic and (entity_overlap > 0 or event_match or medium_text) and confidence >= 0.32:
        return RELATIONSHIP_RELATED
    return RELATIONSHIP_NEW


def _confidence(
    *,
    same_topic: bool,
    entity_overlap: float,
    event_match: bool,
    text_similarity: float,
    novelty: bool,
) -> float:
    score = 0.0
    if same_topic:
        score += 0.22
    score += min(entity_overlap, 1.0) * 0.26
    if event_match:
        score += 0.16
    score += min(text_similarity, 1.0) * 0.31
    if novelty:
        score += 0.05
    return min(score, 1.0)


def _signals(
    *,
    same_topic: bool,
    entity_overlap: float,
    event_match: bool,
    title_similarity: float,
    summary_similarity: float,
    combined_similarity: float,
    novelty: bool,
) -> list[str]:
    signals: list[str] = []
    if same_topic:
        signals.append("same_topic")
    if entity_overlap > 0:
        signals.append("entity_overlap")
    if event_match:
        signals.append("event_type_match")
    if title_similarity >= 0.72:
        signals.append("high_title_similarity")
    elif title_similarity >= 0.42:
        signals.append("medium_title_similarity")
    if summary_similarity >= 0.72:
        signals.append("high_summary_similarity")
    elif summary_similarity >= 0.42:
        signals.append("medium_summary_similarity")
    if combined_similarity >= 0.50:
        signals.append("combined_text_similarity")
    if novelty:
        signals.append("novelty_signal")
    return signals


def _reason(relationship: str, signals: list[str]) -> str:
    if relationship == RELATIONSHIP_DUPLICATE:
        return "Same topic, overlapping entities, matching event type, and high text similarity without a clear novelty signal."
    if relationship == RELATIONSHIP_CONTINUING:
        return "Same topic with overlapping entities and either matching event type or text similarity; novelty suggests a follow-up."
    if relationship == RELATIONSHIP_RELATED:
        return "Shares enough topic, entity, event-type, or text signals to be useful historical context."
    return "No sufficient historical similarity signals."


def _entity_overlap(current: list[str], historical: list[Any]) -> float:
    left = _entity_set(current)
    right = _entity_set([str(value) for value in historical])
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _entity_set(values: list[str]) -> set[str]:
    ignored = {"ai industry", "artificial intelligence", "ai"}
    return {
        _normalize_text(value)
        for value in values
        if _normalize_text(value) and _normalize_text(value) not in ignored
    }


def _text_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    sequence = SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()
    return max(jaccard, sequence * 0.85)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(_normalize_text(text))
        if token and token not in _STOPWORDS
    }


def _has_novelty_signal(
    item: StructuredNewsItem,
    entry: dict[str, Any],
    text_similarity: float,
) -> bool:
    current_text = _normalize_text(f"{item.title} {item.summary}")
    historical_text = _normalize_text(f"{entry.get('title') or ''} {entry.get('summary') or ''}")
    current_specific = set(_SPECIFIC_TOKEN_RE.findall(current_text))
    historical_specific = set(_SPECIFIC_TOKEN_RE.findall(historical_text))
    if current_specific - historical_specific:
        return True
    if text_similarity < 0.72 and any(marker in current_text for marker in _NOVELTY_MARKERS):
        return True
    return False


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())
