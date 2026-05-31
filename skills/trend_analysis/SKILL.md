---
name: trend-analysis
description: Reusable Harness skill for generating, reviewing, repairing, and validating trend_insights in the Daily AI Insight Engine. Use when Codex or an agent needs topic-based daily trend analysis, historical context checks from memory/topic_index.json, TrendInsight report validation, topic distribution consistency, or evidence_item_ids auditing for StructuredNewsItem records.
---

# Trend Analysis

Use this skill to create or validate trend insights from structured AI news.

The current MVP generates trends in `src/pipeline/analyze.py` by grouping validated `StructuredNewsItem` records by `topic`, selecting the most frequent topics, and producing `TrendInsight` records. Historical context is injected by the pre-analyze hook and surfaced through `historical_context_used`.

## Core Workflow

1. Start from validated `StructuredNewsItem` records.
2. Group records by `topic` and inspect frequency, importance, recency, risk, opportunity, and impact scope.
3. Create trend claims only when supported by item evidence and topic distribution.
4. Use `evidence_item_ids` to point to existing items in the same topic as the trend.
5. If memory is used, keep historical facts separate from today's new information.
6. Run the bundled validation script before accepting report sections.

## Resources

- Read `references/trend_schema.md` when you need the exact `TrendInsight`, report, item, and memory fields.
- Read `references/trend_guidelines.md` when you need rules for topic grouping, trend strength, historical context, evidence, and repair.
- Read `references/examples.json` when you need concrete examples.
- Run `scripts/validate_trend_insights.py` for deterministic validation checks.

## Validation

From the repository root:

```bash
python skills/trend_analysis/scripts/validate_trend_insights.py data/processed/ai_news_validated.json --report outputs/report_sections.json
python skills/trend_analysis/scripts/validate_trend_insights.py data/processed/ai_news_validated.json --report outputs/report_sections.json --memory memory/topic_index.json
```

The script validates schema conformance, trend evidence references, same-topic support, top-topic coverage, and optional memory topic consistency.

## Guardrails

- Do not claim a trend from a single weak item unless the summary explicitly frames it as a narrow signal.
- Do not mix historical context with today's events as if both happened today.
- Do not cite evidence ids from unrelated topics.
- Do not invent trend direction, acceleration, or industry impact beyond what topic distribution, importance, and evidence support.
- Prefer concise trend summaries that name the topic, frequency, strongest score, and why the cluster matters.
