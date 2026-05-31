---
name: ai-news-relevance
description: Reusable decision skill for determining whether cleaned same-day news items belong in the Daily AI Insight Engine. Use when Codex or an agent needs to classify report-date candidate news as in-scope or out-of-scope for an AI technology daily report, including borderline AI-adjacent stories, weak AI mentions, infrastructure items, governance items, and fail-closed rejection decisions.
---

# AI News Relevance

Use this skill to decide whether a `CleanNewsItem` should enter the AI daily report pipeline.

The goal is not generic topic tagging. The goal is a fail-closed admission decision for the report dataset.

## Core Workflow

1. Start from one or more `CleanNewsItem` objects that have already passed report-date filtering.
2. Preserve source facts exactly: `title`, `url`, `published_at`, and `content_hash`.
3. Decide whether each item is clearly relevant to an AI technology daily report.
4. Produce one structured relevance assessment per input item.
5. Reject borderline or weakly related items unless the AI-tech signal is explicit and central.
6. Validate the output with the bundled script before using it downstream.

## Resources

- Read `references/output_schema.md` for the required decision fields and threshold contract.
- Read `references/relevance_guidelines.md` for scope rules, edge cases, and fail-closed examples.
- Read `references/examples.json` for concrete pass/fail samples.
- Run `scripts/validate_relevance_assessment.py` for deterministic schema validation.

## Validation

From the repository root:

```bash
python skills/ai_news_relevance/scripts/validate_relevance_assessment.py logs/relevance_report.json
```

## Decision Rules

- Pass only when AI relevance is direct, central, and supported by the title, summary, or content.
- Reject items where AI is incidental, promotional, metaphorical, or only mentioned in a side reference.
- Keep `relevance_evidence` traceable to the input text.
- Set `decision_source` to the actual decision mode used by the pipeline.
- Follow the configured threshold contract exactly; do not accept borderline items by intuition.
- Fail closed when the item could plausibly be non-AI or outside the report scope.
