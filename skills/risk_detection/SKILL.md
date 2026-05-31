---
name: risk-detection
description: Reusable Harness skill for reviewing, assigning, repairing, and validating risk_level, opportunity_level, sentiment, impact_scope, and Risk/Opportunity report insights for StructuredNewsItem records in the Daily AI Insight Engine. Use when Codex or an agent needs evidence-backed AI news risk detection, opportunity assessment, consistency checks, or report risk/opportunity validation.
---

# Risk Detection

Use this skill to assess risk and opportunity signals in structured AI news and to validate the risk/opportunity sections of the daily report.

The project currently assigns `risk_level` and `opportunity_level` in the offline `rule_based_mock` extractor, then converts them into `risk_insights` and `opportunity_insights` in `src/pipeline/analyze.py`. This skill is the reusable Harness/Agent package for reviewing, repairing, or validating those judgments.

## Core Workflow

1. Start from validated `StructuredNewsItem` records.
2. Check that `risk_level`, `opportunity_level`, `sentiment`, `event_type`, and `impact_scope` are mutually consistent.
3. Confirm each medium/high risk or opportunity has evidence in the item.
4. If report sections are present, confirm `risk_insights` and `opportunity_insights` reference existing items with matching levels.
5. Run the bundled validation script before accepting the result.

## Resources

- Read `references/risk_opportunity_schema.md` when you need the exact fields, enum values, and report shapes involved in risk/opportunity validation.
- Read `references/risk_detection_guidelines.md` when you need decision rules for risk, opportunity, sentiment, evidence, and consistency.
- Read `references/examples.json` when you need concrete examples of structured items and report insights.
- Run `scripts/validate_risk_opportunity.py` for deterministic validation checks.

## Validation

From the repository root:

```bash
python skills/risk_detection/scripts/validate_risk_opportunity.py data/processed/ai_news_validated.json
python skills/risk_detection/scripts/validate_risk_opportunity.py data/processed/ai_news_validated.json --report outputs/report_sections.json
```

The script validates schema conformance first, then applies risk/opportunity consistency checks. It exits non-zero for errors and prints warnings for softer quality issues.

## Guardrails

- Do not mark `high` risk without evidence of security, safety, legal, policy, privacy, abuse, governance, execution, or severe reputational exposure.
- Do not mark `high` opportunity without evidence of model capability, product adoption, enterprise deployment, developer ecosystem, infrastructure demand, open-source momentum, or commercialization.
- Do not infer risk or opportunity from general AI market beliefs; use item-specific evidence.
- Keep report insight references auditable through `evidence_item_ids`.
- Prefer a conservative level when the evidence is weak or indirect.
