# Agent Instructions

This file applies to the whole repository. Keep it short and follow it before adding new behavior.

## Project Shape

- This is a Python MVP for an AI news daily report pipeline: collect -> clean -> relevance -> memory dedupe -> extract -> validate -> memory context -> visualize -> analyze -> report -> memory write.
- Keep business steps in `src/pipeline/`; keep orchestration, hooks, trace, validation helpers, and memory support in `src/harness/`, because pipeline code should stay independently testable.
- Keep external systems behind adapters in `src/adapters/`; business modules must not call vendor SDKs, HTTP news APIs, or LLM endpoints directly.
- Keep data contracts in `src/schemas/` with Pydantic; downstream files and reports depend on these schemas staying consistent.

## Boundaries

- Do not bypass the file flow in `config/pipeline.yaml`: raw, cleaned, relevant, structured, validated, report sections, charts, report, trace, relevance report, validation report, LLM audit, memory report, and memory paths are the integration surface.
- `collect` may fetch and normalize source data, but must not do LLM extraction or analysis; raw data should preserve available content so later stages can decide truncation.
- `clean` must stay deterministic and non-LLM; it handles filtering, normalization, URL/content-hash dedupe, and report-date filtering.
- News data used for the daily report must be published on the configured `run_date` only. Treat this as a hard rule, not a scoring hint: if a news item's `published_at` does not fall on the `run_date` in the configured report timezone, it must be filtered out before `extract`.
- Treat naive `published_at` values as UTC before converting them into the configured report timezone for report-date checks.
- The default report timezone for this repository is `Asia/Shanghai`, and the canonical config key is top-level `report_timezone`.
- Do not treat source-level `lookback_days`, feed ordering, or "latest" windows as sufficient for a daily report. The pipeline itself must explicitly enforce the report-date boundary using `run_date` and a concrete report timezone.
- `relevance` is a hard admission gate between `clean` and `extract`; only same-day items that pass the explicit AI-tech relevance threshold may enter extraction, analysis, and report generation.
- `relevance`, `extract`, `analyze`, and Memory fulltext selection may use a real LLM only through the shared adapter; strict `mode.llm: llm` must fail fast without `LLM_API_KEY`, while explicit offline / auto / test paths must keep deterministic fallback working.
- LLM outputs must be schema-validated before they enter analysis, reports, or memory; this prevents malformed model text from becoming trusted data.
- `visualize` must not call LLMs; charts are derived from validated structured fields.
- `generate_report` only assembles validated data, analysis sections, and chart paths; do not move scoring, trend logic, or memory writes into it.
- Analyzer reads `PipelineContext.historical_context`; it must not read or write `memory/topic_index.json` directly because hooks own memory lifecycle.
- Memory strong dedupe runs after `relevance` and before `extract`; it may filter historical duplicates by `id`, `url`, or `content_hash`, and must write its effect to `logs/memory_report.json`.
- Memory context retrieval writes both `PipelineContext.historical_context` and structured `PipelineContext.state["memory_context"]`; Analyzer may consume both, but storage lifecycle remains in hooks/harness.
- Replay / resume reads parent run snapshots from `state/runs/<run_id>/` and must not re-fetch online sources for restored inputs.
- Replay / resume must not update latest Memory by default; only fresh runs with a successfully generated report may write latest Memory.
- Disabled sources in `config/pipeline.yaml` should stay disabled until auth or parser strategy exists; this keeps default runs reproducible.

## Runtime And Data

- Default run command: `python -m src.main`.
- Default runtime mode in `config/pipeline.yaml`: `mode.source: online`, `mode.llm: llm`, `report_timezone: Asia/Shanghai`, `report_language: zh-CN`.
- `mode.llm: llm` is strict and requires `LLM_API_KEY`; deterministic fallback is reserved for explicit offline / auto / test paths.
- Replay / resume command forms: `python -m src.main --replay-run-id <run_id> --from raw|relevant|validated`.
- Default test command: `python -m unittest discover -q`.
- Real LLM smoke test: `python scripts/smoke_llm.py`; use only for manual checks because it requires a key and may cost money.
- Limit each single evaluation run to 10 minutes; if the run exceeds that budget, stop it and record the timeout rather than continuing silently.
- Unit tests should not require network or real LLM credentials; use fixture data, local fallback, or mocks.
- Generated artifacts under `data/processed/`, `outputs/`, `logs/`, and `memory/` may change after pipeline runs; do not hand-edit them to hide code or schema problems.

## Change Rules

- Do not write code. Only start writing or modifying code when I explicitly say so.
- After every code task is completed, check whether the current `todolist.md` needs to be updated.
- When changing a schema field, update all affected pipeline steps, prompts, reports, memory handling, and tests in the same change.
- When adding a data source, implement or extend an adapter and record `metadata.content_source`; source-level failures should be logged/skipped unless every enabled source fails.
- When touching memory behavior, preserve topic normalization, time-window retrieval, and dedupe by `id`, `url`, or `content_hash`.
- Do not log API keys, full prompts, or full LLM responses; traces should keep model, token, latency, success, and short error summaries only.
- Prefer small, local tests near the touched module; broaden to pipeline or harness tests when behavior crosses module boundaries.

## Skill Framework

- Reusable decision skills must be stored under `skills/<skill_name>/`.
- Every decision skill must include:
  - `SKILL.md`
  - `references/` documents for rules, edge cases, and examples
  - `scripts/` validation tooling for the skill output contract
- Every decision skill must define, in English:
  - purpose and trigger
  - input contract
  - output contract
  - pass/fail threshold
  - evidence rules
  - fallback behavior
  - validation script
  - fail-closed policy for ambiguous borderline cases
- Decision skills are reusable policy assets. They do not replace pipeline hard gates; pipeline code must still enforce thresholds and blocking behavior.
- The first report-admission decision skill is `skills/ai_news_relevance/`. It is the source of truth for deciding whether a same-day cleaned item belongs in the AI daily report scope.
