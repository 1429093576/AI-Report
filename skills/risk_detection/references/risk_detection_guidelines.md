# Risk Detection Guidelines

Use these rules when assigning, reviewing, or repairing risk and opportunity labels.

## Evidence First

Assess risk and opportunity from the item's `title`, `summary`, `key_points`, and `evidence`. Do not use outside assumptions unless the user explicitly asks for external research.

When the item has weak evidence:

- Prefer `low` or `unknown`.
- Avoid high confidence language.
- Do not upgrade risk or opportunity based only on the topic being important.

## Risk Levels

Use exactly one:

- `high`: major security risk, abuse path, vulnerability, breach, severe safety issue, lawsuit, regulatory shock, privacy exposure, severe controversy, or major enterprise execution failure.
- `medium`: plausible compliance, governance, security, privacy, execution, reputational, or market adoption risk that should be tracked.
- `low`: routine launch, market update, partnership, product release, or positive development with limited direct risk signal.
- `unknown`: not enough evidence to judge.

Strong risk signals:

- Security terms: vulnerability, breach, misuse, cyber, exploit, security risk, safety risk.
- Legal/policy terms: lawsuit, complaint, regulation, governance, compliance, policy, ban, scrutiny.
- Privacy/data terms: privacy, data exposure, sensitive data.
- Execution terms: deployment failure, enterprise execution, adoption blocker, reliability issue.

Event-type defaults:

- `security`: usually `high` unless evidence is clearly low severity.
- `controversy`: usually `high` or `medium`.
- `policy`: usually `medium`, possibly `high` for direct enforcement or bans.
- `model_release`, `product_launch`, `partnership`, `market`, `research`: usually `low` unless the text contains explicit risk signals.

## Opportunity Levels

Use exactly one:

- `high`: clear opportunity for model capability, product adoption, developer workflows, enterprise deployment, open-source ecosystem, infrastructure demand, commercialization, or major partner distribution.
- `medium`: plausible opportunity, but adoption path, scope, or commercial value is not yet clear.
- `low`: limited opportunity, narrow defensive/research update, or primarily risk/controversy story.
- `unknown`: not enough evidence to judge.

Strong opportunity signals:

- Product and model terms: launch, release, preview, update, capability, multimodal, reasoning.
- Adoption terms: enterprise, customer, deployment, productivity, workflow, partner, platform.
- Ecosystem terms: developer, API, open source, community, tool, framework.
- Infrastructure terms: GPU, cloud, training, inference, data center.

Event-type defaults:

- `model_release`, `product_launch`, `partnership`: often `high`.
- `market`, `research`: often `medium` unless clear adoption or capability signal exists.
- `security`, `policy`, `controversy`: often `low` unless the text clearly shows defensive, governance, or commercial opportunity.

## Sentiment Consistency

Use sentiment as an overall tone check:

- `risk_level=high` and `opportunity_level=high` usually implies `mixed`.
- `risk_level=high` and opportunity is not high usually implies `negative` or `mixed`.
- `opportunity_level=high` and risk is low usually implies `positive`.
- `unknown` sentiment is appropriate only when the item is too thin to judge.

Treat these as consistency heuristics, not absolute schema rules.

## Impact Scope Consistency

Risk and opportunity should align with `impact_scope`:

- High security risk usually pairs with `impact_scope=security`.
- Policy or regulatory risk usually pairs with `impact_scope=policy`.
- Enterprise adoption opportunity usually pairs with `impact_scope=industry`.
- Developer/model capability opportunity usually pairs with `impact_scope=technology` or `ecosystem`.
- End-user product opportunity usually pairs with `impact_scope=user`.

## Report Insight Checks

For `risk_insights`:

- Reference one or more existing `StructuredNewsItem.id` values.
- Prefer items with `risk_level` of `medium` or `high`.
- Use the strongest risk items first.
- Do not cite a low-risk item as a high-risk insight unless the summary explains a broader aggregate risk and other evidence ids support it.

For `opportunity_insights`:

- Reference one or more existing `StructuredNewsItem.id` values.
- Prefer items with `opportunity_level` of `medium` or `high`.
- Use high-importance opportunities first.

## Repair Strategy

When fixing labels:

1. Preserve source facts and non-risk fields unless they are clearly invalid.
2. Adjust the smallest number of labels needed for consistency.
3. Add or narrow evidence only from the input item.
4. Re-run validation after every repair.
