# Relevance Output Schema

Each input `CleanNewsItem` must yield one decision object with these fields:

- `item_id`
- `title`
- `url`
- `published_at`
- `content_hash`
- `is_ai_related`
- `relevance_score`
- `relevance_reason`
- `relevance_evidence`
- `decision_source`

Threshold contract:

- Pass when `is_ai_related = true` and `relevance_score >= 70`
- Reject otherwise

Field rules:

- `relevance_score` must be an integer from 0 to 100
- `relevance_reason` must be a concise factual explanation
- `relevance_evidence` must come from the input text, not outside knowledge
- `decision_source` must identify the actual pipeline decision mode, such as `rule_based` or `llm`
