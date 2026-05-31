# Structured News Schema

This reference describes the project contract for `CleanNewsItem -> StructuredNewsItem`.

Implementation source:

- `src/schemas/news.py`
- `src/schemas/enums.py`
- `src/schemas/base.py`

## Input: CleanNewsItem

Use cleaned records with these fields:

| Field | Rule |
| --- | --- |
| `id` | Non-empty string from the cleaned item. |
| `title` | Non-empty source title. Preserve meaning. |
| `source` | Non-empty source name. |
| `url` | Non-empty source URL. Preserve exactly unless upstream cleaning already changed it. |
| `published_at` | Datetime string accepted by Pydantic. |
| `source_type` | One of `news`, `blog`, `research`, `social`, `release`, `forum`, `unknown`. |
| `language` | One of `zh`, `en`, `other`, `unknown`. |
| `summary` | Optional stripped string. |
| `content` | Optional stripped string. |
| `metadata` | Object. Do not copy into structured output unless schema changes. |
| `content_hash` | Non-empty stable hash from cleaning. |

## Output: StructuredNewsItem

The output must contain exactly these fields:

| Field | Type / Constraint | Rule |
| --- | --- | --- |
| `id` | Non-empty string | Stable structured id. Current mock maps `raw-001` to `structured-001`. |
| `title` | Non-empty string | Preserve from input. |
| `source` | Non-empty string | Preserve from input. |
| `url` | Non-empty string | Preserve from input. |
| `published_at` | datetime | Preserve from input. |
| `source_type` | enum | Preserve from input. |
| `language` | enum | Preserve from input. |
| `topic` | Canonical topic string | Must be one of the 8 project topics from `extraction_guidelines.md`; do not invent topic labels. |
| `entities` | list of non-empty strings | Extract only input-supported entities; use `["AI industry"]` when none are clear. |
| `event_type` | enum | Use one value from the allowed event type list. |
| `summary` | Non-empty string | 1-2 factual sentences grounded in input. |
| `key_points` | list of non-empty strings | 1-3 concise points. |
| `sentiment` | enum | Overall news tone. |
| `impact_scope` | enum | Primary area affected by the event. |
| `importance_score` | integer 0-100 | Evidence-backed ranking score. |
| `importance_rationale` | non-empty string | One readable Chinese sentence explaining the attention/priority judgment. |
| `risk_level` | enum | Risk severity. |
| `risk_rationale` | non-empty string | One readable Chinese sentence explaining the risk judgment. |
| `opportunity_level` | enum | Opportunity strength. |
| `opportunity_rationale` | non-empty string | One readable Chinese sentence explaining the opportunity judgment. |
| `evidence` | list of non-empty strings | 1-3 direct source-backed snippets from input. |
| `evidence_sources` | list of objects | 1-3 auditable source quotes with `source_item_id`, `evidence_field`, `evidence_quote`, and `claim`. |
| `content_hash` | Non-empty string | Preserve from input. |

Unknown fields are forbidden.

## Enum Values

`source_type`:

- `news`
- `blog`
- `research`
- `social`
- `release`
- `forum`
- `unknown`

`language`:

- `zh`
- `en`
- `other`
- `unknown`

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

`sentiment`:

- `positive`
- `neutral`
- `negative`
- `mixed`
- `unknown`

`impact_scope`:

- `technology`
- `industry`
- `capital`
- `policy`
- `user`
- `ecosystem`
- `security`
- `other`

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

## JSON Shape

```json
{
  "id": "structured-001",
  "title": "Source title",
  "source": "Source name",
  "url": "https://example.com/news",
  "published_at": "2026-05-14T09:00:00Z",
  "source_type": "blog",
  "language": "en",
  "topic": "AI Agents",
  "entities": ["OpenAI", "Codex"],
  "event_type": "product_launch",
  "summary": "Factual summary grounded in the input.",
  "key_points": ["One concise point."],
  "sentiment": "positive",
  "impact_scope": "technology",
  "importance_score": 74,
  "importance_rationale": "Codex 扩展到更多开发工作流表面，提升开发者智能体采用关注度。",
  "risk_level": "low",
  "risk_rationale": "材料未显示安全、合规或交付方面的突出风险。",
  "opportunity_level": "high",
  "opportunity_rationale": "开发者工作流扩展强化了编码智能体的采用机会。",
  "evidence": ["Direct source-backed evidence text."],
  "evidence_sources": [
    {
      "source_item_id": "raw-001",
      "evidence_field": "content",
      "evidence_quote": "Direct source-backed evidence text.",
      "claim": "Factual summary grounded in the input."
    }
  ],
  "content_hash": "stable-hash"
}
```
