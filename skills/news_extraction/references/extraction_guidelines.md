# Extraction Guidelines

Use these rules when creating or repairing `StructuredNewsItem` records.

## General Principles

- Preserve source facts from `CleanNewsItem`: `title`, `source`, `url`, `published_at`, `source_type`, `language`, `content_hash`.
- Do not invent entities, product names, dates, claims, or outcomes.
- Prefer conservative labels when text is thin.
- Use `unknown` or `other` only where the schema supports them.
- Keep `evidence` traceable to the input `title`, `summary`, or `content`.
- Add `evidence_sources` for each item. Each quote must be copied verbatim from the cited input field and must use the cleaned input `id` as `source_item_id`.
- Make derived fields mutually consistent: a high risk level needs risk evidence; a high opportunity level needs opportunity evidence.

## Topic

Choose exactly one canonical project topic. Do not invent topic labels.

- `AI Agents`: agents, copilots, automated workflows, tool use, multi-step tasks, coding agents, human-agent collaboration.
- `Foundation Models`: GPT, Gemini, Claude, Llama, Mistral, model capability, multimodal models, reasoning, frontier models.
- `AI Infrastructure`: GPUs, chips, cloud, training, inference, data centers, HBM, hardware, deployment infrastructure.
- `AI Applications`: search, office/productivity, healthcare, education, design, enterprise apps, consumer AI products.
- `Developer Tools and Open Source`: open-source models and frameworks, open weights, Hugging Face, licenses, SDKs, GitHub releases, vLLM, Ollama, LangChain, developer tooling.
- `AI Safety and Governance`: safety, governance, regulation, policy, model risk, misuse, privacy, legal disputes, defense, audits.
- `AI Research`: papers, benchmarks, algorithms, experiments, academic results, research-lab findings.
- `AI Business and Market`: funding, M&A, partnerships, revenue, enterprise adoption, competitive positioning, commercialization, company strategy.

Choose the main topic, not every topic mentioned. If title and body differ, prefer the main event in summary/content.

Priority and boundary rules:

1. If the core event is regulation, legal action, safety, privacy, misuse, audit, or model risk, choose `AI Safety and Governance`.
2. If the core event is a paper, benchmark, algorithm, experiment, or academic result, choose `AI Research`.
3. If the core event is tool use, autonomous execution, multi-step workflow, coding agent, or human-agent collaboration, choose `AI Agents`.
4. If the core event is a foundation model release or capability update, choose `Foundation Models`.
5. If the core event is GPUs, chips, data centers, cloud capacity, training, inference, deployment performance, or hardware supply, choose `AI Infrastructure`.
6. If the core event is an SDK, framework, GitHub release, model-serving tool, or open-source ecosystem item, choose `Developer Tools and Open Source`.
7. If the core event is an AI feature inside a user-facing or enterprise product, choose `AI Applications`.
8. If the core event is funding, acquisition, partnership, revenue, enterprise adoption, market competition, or strategy and no earlier category is more specific, choose `AI Business and Market`.

## Entities

Extract named companies, products, models, people, institutions, open-source projects, platforms, and technologies that appear in the input.

Common examples:

- Companies: OpenAI, Google, Anthropic, Microsoft, NVIDIA, Meta, Mistral, Hugging Face.
- Products/models: ChatGPT, Codex, Gemini, Claude, Llama, Copilot.
- Institutions: regulators, research labs, standards bodies, open-source communities.

Rules:

- Include only entities supported by the input text.
- Do not add entities based only on outside knowledge.
- Use `["AI industry"]` when no specific entity is clear.

## Event Type

Use exactly one:

- `model_release`: new model, model upgrade, benchmarked capability, multimodal/reasoning release.
- `product_launch`: app, tool, platform, feature, developer product, user product, API feature.
- `funding`: financing, investment, valuation, major capital movement.
- `policy`: law, regulation, governance framework, compliance requirement, public-sector policy.
- `research`: paper, study, benchmark, technical result, experiment.
- `controversy`: lawsuit, complaint, public dispute, copyright issue, ethics dispute.
- `partnership`: partnership, alliance, joint deployment, ecosystem collaboration.
- `market`: adoption, customer use, industry trend, commercialization, business traction.
- `security`: security, vulnerability, abuse, defense, model safety, data security.
- `other`: cannot classify reliably.

Classify by what happened, not by why it matters.

## Impact Scope

Use exactly one:

- `technology`: models, APIs, developer tooling, benchmarks, chips, training, inference.
- `industry`: enterprise adoption, business workflow, sector deployment, customer operations.
- `capital`: investment, funding, valuation, M&A.
- `policy`: regulation, law, governance, compliance.
- `user`: consumer experience, end-user workflow, productivity.
- `ecosystem`: open-source community, platform partners, developer or partner network.
- `security`: cyber safety, model safety, data security, abuse prevention.
- `other`: impact is unclear.

Impact scope can differ from event type. Example: a partnership can primarily affect `industry`.

## Summary

Write 1-2 factual sentences that answer:

- Who did what?
- What AI product, model, workflow, policy, or market issue is involved?
- Why is it relevant enough for the daily report?

Do not write predictions or analysis as facts.

## Key Points

Use 1-3 points. Each point should add a distinct factual angle:

- Product/model capability.
- Subject or partner.
- Affected users, developers, enterprises, or ecosystem.
- Risk or opportunity signal.

Avoid repeating the summary verbatim across all points.

## Evidence

Use 1-3 source-backed snippets from input `title`, `summary`, or `content`.

Good evidence:

- Directly supports the event type, topic, risk, opportunity, or score.
- Uses wording present in the source fields.
- Is specific enough to audit later.

Poor evidence:

- Generic industry interpretation.
- Claims not present in the input.
- Predictions written by the extractor.

When evidence is weak, reduce `importance_score` and choose conservative levels.

## Sentiment

Use exactly one:

- `positive`: adoption growth, capability improvement, ecosystem expansion, successful launch.
- `neutral`: factual update with no strong positive or negative signal.
- `negative`: incident, lawsuit, major criticism, regulatory penalty, severe failure.
- `mixed`: meaningful opportunity and meaningful risk both appear.
- `unknown`: too little information.

## Risk Level

Use exactly one:

- `high`: major security risk, misuse, breach, lawsuit, regulatory shock, privacy exposure, severe controversy.
- `medium`: plausible compliance, governance, security, execution, or reputational risk.
- `low`: routine launch, market update, or positive development with limited risk signal.
- `unknown`: not enough evidence.

Do not mark high risk just because the item is important.

## Opportunity Level

Use exactly one:

- `high`: clear boost to model capability, product adoption, developer ecosystem, enterprise deployment, infrastructure demand, open-source ecosystem, or commercialization.
- `medium`: possible opportunity, but adoption path, scope, or business value is still unclear.
- `low`: weak or limited opportunity, or a primarily risk/controversy story.
- `unknown`: not enough evidence.

Do not turn broad AI optimism into item-specific opportunity.

## Importance Score

Use an integer from 0 to 100.

Suggested bands:

- `85-100`: top-tier actor, major model/product/infrastructure shift, large safety or regulatory event, clear industry turning point.
- `70-84`: meaningful partnership, enterprise adoption, open-source progress, research result, or ecosystem change.
- `50-69`: routine market update, narrower launch, limited but trackable event.
- `0-49`: weak relevance, low evidence, duplicate/edge item, unclear impact.

Increase score when:

- The item involves major actors such as OpenAI, Google, Anthropic, Microsoft, NVIDIA, Meta, Mistral, or Hugging Face.
- The item affects model capability, developer ecosystem, enterprise adoption, safety governance, infrastructure, or commercialization.
- Evidence is clear and report-worthy.
- Risk or opportunity is high and the affected scope is specific.

Decrease score when:

- Evidence is thin.
- The item is mainly promotional.
- Impact is unclear.
- AI relevance is weak.
- The conclusion requires heavy inference.

## Judgment Rationales

Every item must include these non-empty Chinese rationale fields:

- `importance_rationale`: one readable sentence explaining the attention/priority score. Tie it to the topic, event type, impact scope, named actors, or concrete evidence.
- `risk_rationale`: one readable sentence explaining the risk level. Mention the risk signal, uncertainty, or why the source does not show a material risk.
- `opportunity_rationale`: one readable sentence explaining the opportunity level. Mention adoption, capability, ecosystem, commercialization, infrastructure, open-source, or deployment signals where supported.

Do not put the score or level prefix inside these fields. Store the explanation only; report rendering adds prefixes such as `90：` and `中：`.

## Self-Check

Before accepting output:

- Confirm every field is allowed by `StructuredNewsItem`.
- Confirm all preserved fields match the input.
- Confirm enum values are exact.
- Confirm `importance_score` is an integer from 0 to 100.
- Confirm `importance_rationale`, `risk_rationale`, and `opportunity_rationale` are non-empty, evidence-grounded, and not generic boilerplate.
- Confirm summary and key points are factual.
- Confirm evidence appears in the input.
- Confirm `evidence_sources[].evidence_quote` appears in `source_item_id.evidence_field`.
- Confirm risk and opportunity levels are supported by evidence.
- Confirm high scores reflect actual impact, not vague importance.
