"""Canonical topic taxonomy for structured AI news."""

from __future__ import annotations

import re
from typing import Any


AI_NEWS_TOPICS: tuple[str, ...] = (
    "AI Agents",
    "Foundation Models",
    "AI Infrastructure",
    "AI Applications",
    "Developer Tools and Open Source",
    "AI Safety and Governance",
    "AI Research",
    "AI Business and Market",
)

_TOPIC_ALIASES: dict[str, str] = {
    **{topic.lower(): topic for topic in AI_NEWS_TOPICS},
    "agents": "AI Agents",
    "ai agent": "AI Agents",
    "ai agents": "AI Agents",
    "agentic ai": "AI Agents",
    "agent workflows": "AI Agents",
    "autonomous agents": "AI Agents",
    "coding agents": "AI Agents",
    "foundation model": "Foundation Models",
    "foundation models": "Foundation Models",
    "ai model": "Foundation Models",
    "ai models": "Foundation Models",
    "model release": "Foundation Models",
    "model releases": "Foundation Models",
    "frontier models": "Foundation Models",
    "llms": "Foundation Models",
    "large language models": "Foundation Models",
    "ai hardware": "AI Infrastructure",
    "ai infrastructure": "AI Infrastructure",
    "infrastructure": "AI Infrastructure",
    "compute": "AI Infrastructure",
    "chips": "AI Infrastructure",
    "gpu": "AI Infrastructure",
    "gpus": "AI Infrastructure",
    "ai application": "AI Applications",
    "ai applications": "AI Applications",
    "enterprise ai applications": "AI Applications",
    "consumer ai": "AI Applications",
    "ai developer tools": "Developer Tools and Open Source",
    "developer tools": "Developer Tools and Open Source",
    "developer tooling": "Developer Tools and Open Source",
    "open source ai": "Developer Tools and Open Source",
    "open-source ai": "Developer Tools and Open Source",
    "open source": "Developer Tools and Open Source",
    "open-source": "Developer Tools and Open Source",
    "ai safety": "AI Safety and Governance",
    "ai governance": "AI Safety and Governance",
    "ai safety and governance": "AI Safety and Governance",
    "model safety": "AI Safety and Governance",
    "policy": "AI Safety and Governance",
    "regulation": "AI Safety and Governance",
    "ai policy": "AI Safety and Governance",
    "ai regulation": "AI Safety and Governance",
    "ai research": "AI Research",
    "research": "AI Research",
    "ml research": "AI Research",
    "machine learning research": "AI Research",
    "academic research": "AI Research",
    "ai industry": "AI Business and Market",
    "ai business": "AI Business and Market",
    "ai market": "AI Business and Market",
    "ai business and market": "AI Business and Market",
    "business and market": "AI Business and Market",
    "enterprise ai": "AI Business and Market",
    "market": "AI Business and Market",
}


def normalize_topic_label(value: Any) -> str:
    """Return a canonical topic label or raise when the topic is out of scope."""

    topic = _normalize_key(str(value or ""))
    if not topic:
        raise ValueError("topic must not be empty")
    if topic in _TOPIC_ALIASES:
        return _TOPIC_ALIASES[topic]
    allowed = ", ".join(AI_NEWS_TOPICS)
    raise ValueError(f"topic must be one of: {allowed}")


def normalize_topic_key(value: Any) -> str:
    """Return the canonical lowercase Memory key for a topic."""

    return normalize_topic_label(value).lower()


def coerce_topic_key(value: Any) -> str:
    """Normalize known topic aliases while preserving unknown legacy keys."""

    topic = str(value or "").strip()
    if not topic:
        return ""
    try:
        return normalize_topic_key(topic)
    except ValueError:
        return _normalize_key(topic)


def allowed_topic_values() -> list[str]:
    """Return the topic taxonomy as a JSON-friendly list."""

    return list(AI_NEWS_TOPICS)


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower().replace("_", " ").replace("-", " "))
