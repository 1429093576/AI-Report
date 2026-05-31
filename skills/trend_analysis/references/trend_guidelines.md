# Trend Guidelines

Use these rules when creating, reviewing, or repairing trend insights.

## Trend Signal

A trend claim should be supported by one or more of:

- Topic frequency: multiple items share the same topic.
- Importance concentration: one topic has high-scoring items.
- Source diversity: multiple sources point to a similar theme.
- Event-type clustering: launches, policy moves, security work, or partnerships cluster under a topic.
- Historical continuity: memory shows related items from previous days.
- Risk/opportunity pattern: a topic shows repeated high risk or high opportunity.

Avoid claiming broad momentum from a single low-importance item.

## Topic Grouping

Use `StructuredNewsItem.topic` as the primary grouping key. Do not create new trend topics unless the output schema or user request explicitly allows it.

Recommended topic behavior:

- Cover the most frequent topics first.
- If frequencies tie, prefer the topic with higher max or average `importance_score`.
- Make sure evidence ids for a trend belong to the trend topic.
- If a trend title names a topic, the cited evidence should include that topic.

## Summary Writing

A good trend summary includes:

- The topic or theme.
- The number of current items or the relative concentration.
- The strongest score, source, or event type if useful.
- A cautious interpretation of why the cluster matters.
- A historical note only when historical context was actually used.

Do not:

- Treat historical context as a same-day event.
- Predict future outcomes without labeling them as follow-up questions or hypotheses.
- Overstate a cluster that is driven by one source only.

## Trend State

Set exactly one `trend_state`:

- `new`: no useful historical relationship is available; this is a new same-day topic signal.
- `continuing`: Memory shows same-topic related context, likely duplicate, or continuing relationship, but the current signal is not clearly stronger or weaker.
- `heating_up`: current topic count, importance peak, or source diversity is stronger than recent Memory context.
- `cooling_down`: historical context is stronger than today's same-topic signal, especially when today has only one weaker item.
- `reversing`: risk/opportunity direction changes materially versus Memory context, such as low-risk/high-opportunity history followed by high-risk current items.

Use `trend_signals`, `memory_context.item_relationships`, and `memory_context.soft_similarity` as structured signals. In LLM mode, the model is the final judge for `trend_state`; `trend_signals[].rule_suggested_state` is advisory and may be overridden when current evidence and history support another state. Historical Memory can determine state, but `evidence_item_ids` must still cite today's `StructuredNewsItem` records only.

## Scope Selection

Choose `TrendInsight.scope` from the dominant impact scope in supporting items.

Examples:

- Model or developer-tool cluster: `technology`.
- Enterprise adoption cluster: `industry`.
- Funding or valuation cluster: `capital`.
- Regulation or governance cluster: `policy`.
- Consumer product cluster: `user`.
- Open-source or partner ecosystem cluster: `ecosystem`.
- Security or safety cluster: `security`.

When supporting items are mixed, use the scope of the highest-importance supporting item or the majority scope.

## Evidence Item IDs

Use `evidence_item_ids` to make the trend auditable:

- Include 1-3 item ids.
- Prefer high-importance items in the same topic.
- Include multiple sources when possible.
- Do not include unknown ids.
- Do not cite unrelated topics to inflate evidence.

## Historical Context

When historical context is available:

- Use it to compare continuity, recurrence, or acceleration.
- Clearly distinguish history from today's new items.
- Set `historical_context_used` to true only when the analyzer actually used or received memory context.
- Do not claim historical acceleration if memory only contains duplicates of today's items.
- Do not mark `heating_up` if the only historical signal is `likely_duplicate` and today's item has no new evidence or stronger score.

When no historical context is available:

- Set `historical_context_used` to false.
- Base trend claims only on current structured items.

## Repair Strategy

When fixing a trend insight:

1. Validate the report schema first.
2. Check every `evidence_item_ids` value exists.
3. Align the trend title and summary with the cited items' topics.
4. Adjust `scope` to match majority or highest-importance supporting item.
5. Remove unsupported historical claims when `historical_context_used` is false.
6. Re-run validation.
