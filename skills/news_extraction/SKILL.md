---
name: news-extraction
description: Reusable Harness skill for turning CleanNewsItem records into validated StructuredNewsItem records in the Daily AI Insight Engine. Use when Codex or an agent needs to extract, review, repair, or validate AI news structure, including topic/entity/event tagging, evidence-backed summaries, risk and opportunity levels, and importance scoring for data shaped by src.schemas.StructuredNewsItem.
---

# News Extraction

Use this skill to transform cleaned AI news records into schema-valid structured news records for the Daily AI Insight Engine.

The project currently runs `rule_based_mock` extraction in `src/pipeline/extract.py`; this skill is the reusable Harness/Agent package for future LLM or agent-assisted extraction, review, and repair.

## Core Workflow

1. Start from one or more `CleanNewsItem` objects.
2. Preserve source facts exactly: `title`, `source`, `url`, `published_at`, `source_type`, `language`, and `content_hash`.
3. Produce one `StructuredNewsItem` per input item.
4. Ground all derived fields in the input `title`, `summary`, or `content`.
5. Validate the output with the bundled script before using it downstream.

## Resources

- Read `references/structured_news_schema.md` when you need the exact output fields, enum values, constraints, or JSON shape.
- Read `references/extraction_guidelines.md` when you need decision rules for topic, entities, event type, impact scope, sentiment, risk, opportunity, evidence, or importance scoring.
- Read `references/examples.json` when you need concrete input/output examples.
- Run `scripts/validate_structured_news.py` for deterministic validation against the project Pydantic schema.

## Validation

From the repository root:

```bash
python skills/news_extraction/scripts/validate_structured_news.py data/processed/ai_news_structured.json
```

The script accepts either a JSON array of structured news items or a single structured news object. It prints item-level errors and exits non-zero when validation fails.

## Extraction Rules

- Do not invent sources, URLs, dates, entities, product names, model names, or event outcomes.
- Do not add fields outside `StructuredNewsItem`; project schemas use `extra="forbid"`.
- Use only the canonical project topic taxonomy; out-of-taxonomy topic labels are invalid.
- Use `unknown` or `other` where the schema allows them and the input does not support a stronger label.
- Keep `evidence` as direct source-backed text from the input.
- Lower confidence should reduce `importance_score`; do not compensate for thin evidence with broad industry assumptions.
- If repairing existing output, make the smallest schema-valid correction that preserves the original source facts.
