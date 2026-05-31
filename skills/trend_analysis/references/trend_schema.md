# Trend Schema

This reference covers the data contracts involved in trend analysis.

Implementation source:

- `src/schemas/news.py`
- `src/schemas/report.py`
- `src/harness/memory_manager.py`
- `src/pipeline/analyze.py`

## StructuredNewsItem Fields

Trend analysis uses these fields:

| Field | Purpose |
| --- | --- |
| `id` | Evidence id used by report insights. |
| `published_at` | Recency and memory window checks. |
| `topic` | Primary grouping key. |
| `title` | Human-readable evidence label. |
| `source` | Source diversity signal. |
| `event_type` | Helps explain what kind of activity drives the topic. |
| `summary` | Factual basis for trend summary. |
| `importance_score` | Used to rank topic intensity. |
| `importance_rationale` | Reader-facing explanation of why the item deserves attention. |
| `impact_scope` | Used as `TrendInsight.scope`. |
| `risk_level` | Helps identify risk-heavy trend clusters. |
| `risk_rationale` | Reader-facing explanation of risk level. |
| `opportunity_level` | Helps identify opportunity-heavy trend clusters. |
| `opportunity_rationale` | Reader-facing explanation of opportunity level. |
| `evidence` | Source-backed support for claims. |
| `evidence_sources` | Auditable source quotes that were verified before analysis. |

## TrendInsight Shape

`DailyInsightReport.trend_insights` contains `TrendInsight` records:

| Field | Type / Constraint | Rule |
| --- | --- | --- |
| `title` | Non-empty string | Name the topic and direction/signal. |
| `scope` | `ImpactScope` enum | Main impact scope represented by the trend. |
| `summary` | Non-empty string | Explain topic frequency, strength, and meaning. |
| `evidence_item_ids` | list of non-empty strings | Reference existing structured item ids that support the trend. |
| `trend_state` | enum string | One of `new`, `continuing`, `heating_up`, `cooling_down`, `reversing`. |
| `historical_context_used` | boolean | True only when Analyzer received historical context. |
| `evidence_sources` | list of evidence source objects | At least one supported quote copied from cited structured items. |

Allowed `scope` values:

- `technology`
- `industry`
- `capital`
- `policy`
- `user`
- `ecosystem`
- `security`
- `other`

## Memory Shape

`memory/topic_index.json` stores topic-indexed historical entries:

```json
{
  "topics": {
    "ai agents": [
      {
        "id": "structured-001",
        "title": "Example title",
        "source": "Example Source",
        "url": "https://example.com",
        "published_at": "2026-05-14T09:00:00+00:00",
        "topic": "AI Agents",
        "summary": "Example summary.",
        "importance_score": 74,
        "risk_level": "low",
        "opportunity_level": "high",
        "content_hash": "stable-hash",
        "evidence": ["Evidence text."],
        "evidence_sources": [
          {
            "source_item_id": "raw-001",
            "evidence_field": "content",
            "evidence_quote": "Evidence text.",
            "claim": "Example summary."
          }
        ]
      }
    ]
  }
}
```

Memory topic keys are normalized with `topic.strip().lower()`.

## Memory Context Shape

The pre-analyze hook also writes structured signals to `PipelineContext.state["memory_context"]`:

```json
{
  "item_relationships": [
    {
      "item_id": "structured-001",
      "title": "Current item",
      "topic": "AI Agents",
      "relationship": "continuing",
      "confidence": 0.76,
      "matched_memory_item_ids": ["historical-001"]
    }
  ],
  "soft_similarity": {
    "relationships": {
      "new": 0,
      "related_context": 0,
      "continuing": 1,
      "likely_duplicate": 0
    }
  }
}
```

Use these relationships to choose `trend_state`, but do not cite Memory-only items as today's evidence.

## Trend Signals Shape

Analyzer also builds `analysis_input.trend_signals` before an LLM call:

```json
[
  {
    "topic": "AI Agents",
    "rule_suggested_state": "continuing",
    "current_item_count": 2,
    "current_peak_importance_score": 88,
    "current_average_importance_score": 81.0,
    "current_event_types": {"model_release": 1},
    "current_risk_levels": {"medium": 1},
    "current_opportunity_levels": {"high": 1},
    "historical_item_count": 1,
    "historical_peak_importance_score": 76,
    "soft_relationships": {"continuing": 1},
    "relationship_confidence_peak": 0.76,
    "risk_direction": "higher",
    "opportunity_direction": "stable",
    "evidence_item_ids": ["structured-001"]
  }
]
```

`rule_suggested_state` is advisory. It is the deterministic fallback state for offline runs and a compact signal for online LLM analysis, not a binding rule.

## Current MVP Trend Logic

The current analyzer fallback:

- Groups validated items by `topic`.
- Sorts topics by frequency.
- Emits up to 3 `TrendInsight` records.
- Uses the first item's `impact_scope` as the trend `scope`.
- Sets `evidence_item_ids` to the first 3 item ids from that topic.
- Sets `trend_state` from advisory trend signals.
- Copies supported `evidence_sources` from cited items.
- Sets `historical_context_used` from whether `PipelineContext.historical_context` is non-empty.

When a real LLM adapter is active, the LLM is the final trend-state judge. It should use `trend_signals`, current evidence, `historical_context`, and `memory_context` together, and may override `rule_suggested_state` when the evidence supports a different state.
