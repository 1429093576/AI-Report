# Risk And Opportunity Schema

This reference covers the schema fields used by risk detection and opportunity assessment.

Implementation source:

- `src/schemas/news.py`
- `src/schemas/report.py`
- `src/schemas/enums.py`

## StructuredNewsItem Fields

Risk/opportunity review uses these `StructuredNewsItem` fields:

| Field | Purpose |
| --- | --- |
| `id` | Stable item id used by report insight references. |
| `title` | Source-backed headline. |
| `topic` | Topic context for risk/opportunity grouping. |
| `event_type` | Main event classification. |
| `summary` | Factual event summary. |
| `key_points` | Supporting structured points. |
| `sentiment` | Overall tone: `positive`, `neutral`, `negative`, `mixed`, `unknown`. |
| `impact_scope` | Main affected area. |
| `importance_score` | Ranking score from 0 to 100. |
| `risk_level` | Risk level: `low`, `medium`, `high`, `unknown`. |
| `risk_rationale` | Reader-facing rationale explaining the risk level. |
| `opportunity_level` | Opportunity level: `low`, `medium`, `high`, `unknown`. |
| `opportunity_rationale` | Reader-facing rationale explaining the opportunity level. |
| `evidence` | Source-backed snippets supporting the labels. |
| `evidence_sources` | Auditable source quotes verified before analysis. |

## Enum Values

`event_type`:

- `product_launch`
- `funding`
- `policy`
- `research`
- `controversy`
- `partnership`
- `market`
- `security`
- `model_release`
- `other`

`impact_scope`:

- `technology`
- `industry`
- `capital`
- `policy`
- `user`
- `ecosystem`
- `security`
- `other`

`sentiment`:

- `positive`
- `neutral`
- `negative`
- `mixed`
- `unknown`

`risk_level`:

- `low`
- `medium`
- `high`
- `unknown`

`opportunity_level`:

- `low`
- `medium`
- `high`
- `unknown`

## Report Fields

`DailyInsightReport.risk_insights` contains:

| Field | Rule |
| --- | --- |
| `title` | Non-empty string. |
| `level` | `RiskLevel`. |
| `summary` | Non-empty risk summary. |
| `evidence_item_ids` | Item ids that support this risk insight. |
| `evidence_sources` | At least one supported quote copied from cited structured items. |

`DailyInsightReport.opportunity_insights` contains:

| Field | Rule |
| --- | --- |
| `title` | Non-empty string. |
| `level` | `OpportunityLevel`. |
| `summary` | Non-empty opportunity summary. |
| `evidence_item_ids` | Item ids that support this opportunity insight. |
| `evidence_sources` | At least one supported quote copied from cited structured items. |

## Validation Expectations

- Each `evidence_item_ids` value should exist in the structured news set.
- Each report insight must have at least one supported `evidence_sources` entry or it is excluded from the final report.
- A high/medium report risk should reference at least one item with matching high/medium `risk_level`.
- A high/medium report opportunity should reference at least one item with matching high/medium `opportunity_level`.
- Empty evidence on high/medium items is invalid for this skill even if the Pydantic schema allows an empty list.
