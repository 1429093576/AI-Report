# Relevance Guidelines

Use these rules when deciding whether a cleaned same-day item belongs in the AI daily report.

## Pass Conditions

Pass the item only when the main event is about one or more of the following:

- AI models, model releases, reasoning systems, multimodal systems
- AI products, copilots, agents, developer AI workflows
- AI labs, AI vendors, AI platform launches
- AI infrastructure such as GPUs, inference, training, model serving, AI cloud
- AI governance, policy, regulation, safety, audits, misuse, model security
- AI market or adoption changes where AI is the central business topic
- Open-source AI ecosystems, model communities, toolchains, licenses

## Boundary Rules

Use these narrower rules for borderline technology stories. The item should pass because
the main event, affected object, and evidence all point to AI technology, AI products,
AI infrastructure, AI governance, or AI industry change. Do not pass an item merely
because it contains the word "AI".

### AI Infrastructure

Pass when the story directly supports AI workloads:

- GPUs, AI accelerators, TPUs, NPUs, or AI chips for model training or inference
- inference engines, model serving, CUDA/CANN/ROCm, vLLM, Transformers, model runtime updates
- AI data center networking, memory, HBM, cluster scheduling, all-reduce, or AI cloud capacity
- compute deals, capacity shortages, or deployment costs where model training/inference is central

Reject when the story is general infrastructure:

- generic chip, server, cloud, or data center news without a concrete AI workload
- ordinary HPC, enterprise cloud, telecom, or semiconductor updates where AI is only a side reference
- consumer hardware performance claims that mention AI vaguely

### Policy, Governance, And Legal

Pass when the policy or legal event targets AI systems:

- AI model regulation, model registration, safety evaluation, audits, watermarking, generated-content labels
- training-data copyright, model output liability, deepfake rules, synthetic media policy
- enforcement or compliance actions against AI products, AI labs, or model providers

Reject when the item is general policy:

- privacy, cybersecurity, antitrust, labor, platform, or data rules that do not clearly target AI
- general company compliance news where AI is background context rather than the regulated object

### Academic And Research

Pass when the technical contribution is an AI contribution:

- LLM, VLM, multimodal, agent, reasoning, alignment, safety, architecture, training, or inference research
- benchmarks, datasets, evaluation methods, or papers that test or improve AI systems
- arXiv or academic items whose core method or result is about AI models or machine learning systems

Reject when AI is only a tool:

- a domain-science paper that uses AI for analysis but is mainly about biology, physics, finance, medicine, etc.
- robotics, HCI, neuroscience, or hardware papers without a clear AI model/system contribution
- generic statistics or optimization work without an explicit AI model or ML system connection

### Products And Consumer Hardware

Pass when AI is the core product capability:

- AI agents, copilots, assistants, model APIs, developer tools, enterprise AI workflows
- product launches whose main value proposition is a concrete model capability or AI automation workflow

Reject when AI is a minor feature or marketing phrase:

- phones, PCs, cars, cameras, appliances, or games with a small "AI feature"
- "AI-powered" branding without a concrete model, workflow, or industry impact

### Business And Market

Pass when AI is the central business event:

- funding, revenue, IPO, partnership, acquisition, adoption, or compute spend for an AI company/product
- major AI labs, AI infrastructure providers, model companies, or AI platform vendors

Reject when AI is incidental:

- general big-tech earnings, layoffs, leadership changes, or marketing campaigns that only mention AI
- broad market commentary where AI is one of many background factors

## Reject Conditions

Reject the item when:

- AI is only mentioned in passing
- The main story is not about AI technology or AI industry scope
- The item is primarily about gaming, entertainment, general hardware, lifestyle, or unrelated consumer news
- The story uses AI as a side comparison, a buzzword, or a speculative framing device
- The evidence is too weak to prove that AI is the central event

## Borderline Cases

Reject by default unless the AI signal is explicit and central:

- General cloud or chip news without clear AI workload relevance
- Consumer product updates with only a small AI feature mention
- Broader tech-business stories that mention AI spending but are really about another domain
- Social or cultural commentary that references AI trends without a concrete AI event
- Research that uses AI as an analysis tool while the main contribution is in another domain
- Policy or legal news that is about privacy, cybersecurity, antitrust, or labor without a specific AI target

## Evidence Rules

- Evidence must come from `title`, `summary`, or `content`
- Prefer 1 to 2 short, direct snippets
- Do not rely on background knowledge
- If evidence is vague, lower the score and reject if needed
- Evidence should show that AI is central, not merely present in a keyword list or marketing sentence

## Scoring Guide

- `85-100`: direct and important AI technology or AI industry event
- `70-84`: clearly AI-related and report-worthy, but narrower in scope
- `50-69`: AI-adjacent, weak, indirect, or mixed-signal item; reject
- `0-49`: not suitable for the AI daily report

## Fail-Closed Rule

When uncertain, reject the item. The daily report should optimize for precision, not recall.
