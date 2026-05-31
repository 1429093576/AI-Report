"""Shared helpers for file-backed pipeline steps."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.adapters import LLMAdapter, LLMResult, MockLLMAdapter, create_llm_adapter
from src.harness import PipelineContext, merge_audit_reports


DEFAULT_PATHS = {
    "raw": "data/raw/ai_news_raw.json",
    "cleaned": "data/processed/ai_news_cleaned.json",
    "relevant": "data/processed/ai_news_relevant.json",
    "structured": "data/processed/ai_news_structured.json",
    "validated": "data/processed/ai_news_validated.json",
    "relevance_report": "logs/relevance_report.json",
    "memory_report": "logs/memory_report.json",
    "report_sections": "outputs/report_sections.json",
    "validation_report": "logs/validation_report.json",
    "llm_audit_report": "logs/llm_audit_report.json",
    "charts_dir": "outputs/charts",
    "daily_report": "outputs/daily_report.md",
    "memory": "memory/topic_index.json",
    "trace": "logs/run_trace.jsonl",
}

OFFLINE_LLM_MODES = {"mock", "offline", "rule_based", "rule_based_mock", "rules"}
ONLINE_LLM_MODES = {"auto", "llm", "openai_compatible", "real"}
REQUIRED_LLM_MODES = {"llm", "openai_compatible", "real"}
LLM_BUSINESS_ERROR_TYPES = {
    "transport_error",
    "invalid_json",
    "schema_error",
    "item_count_mismatch",
    "audit_failure",
}
DEFAULT_LLM_BUSINESS_RETRY_ATTEMPTS = 3

T = TypeVar("T")
U = TypeVar("U")


class LLMBusinessError(ValueError):
    """A sanitized, classified LLM business-layer error."""

    def __init__(
        self,
        error_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        normalized = error_type.strip()
        if normalized not in LLM_BUSINESS_ERROR_TYPES:
            raise ValueError(f"unsupported LLM business error type: {error_type}")
        super().__init__(message)
        self.error_type = normalized
        self.details = dict(details or {})


def path_for(context: PipelineContext, name: str) -> Path:
    """Return a configured path with a project default fallback."""

    if name in context.paths:
        return context.paths[name]

    configured = context.config.get("paths", {})
    if isinstance(configured, dict) and name in configured:
        return Path(configured[name])

    return Path(DEFAULT_PATHS[name])


def read_json(path: str | Path) -> Any:
    """Read a JSON file."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: Any) -> None:
    """Write JSON with stable formatting."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_llm_audit_report(
    context: PipelineContext,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write the latest merged LLM audit report and register it as an artifact."""

    section_reports = dict(context.get("llm_audit_section_reports", {}))
    if report is not None:
        audit_type = str(report.get("audit_type") or "").strip()
        if audit_type:
            section_reports[audit_type] = report

    merged = merge_audit_reports(
        run_id=context.run_id,
        reports=section_reports.values(),
    )
    output_path = path_for(context, "llm_audit_report")
    write_json(output_path, merged)
    context.add_artifact("llm_audit_report", output_path)
    context.set("llm_audit_section_reports", section_reports)
    context.set("llm_audit_report", merged)
    return merged


def model_list_payload(items: list[Any]) -> list[dict[str, Any]]:
    """Serialize a list of Pydantic models or dictionaries for JSON output."""

    payload: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            payload.append(item.model_dump(mode="json"))
        else:
            payload.append(dict(item))
    return payload


def require_json_list(path: str | Path) -> list[Any]:
    """Read a JSON array and fail clearly when the file shape is wrong."""

    payload = read_json(path)
    if not isinstance(payload, list):
        raise ValueError(f"{Path(path)} must contain a JSON array")
    return payload


def active_llm_adapter(context: PipelineContext) -> LLMAdapter | None:
    """Return the configured LLM adapter when this run should call a model."""

    injected = context.get("llm_adapter")
    if injected is not None:
        if not isinstance(injected, LLMAdapter):
            raise TypeError("context llm_adapter must implement LLMAdapter")
        return injected

    mode = llm_mode(context)
    if mode in OFFLINE_LLM_MODES:
        return None
    if mode not in ONLINE_LLM_MODES:
        raise ValueError(f"unsupported LLM mode: {mode}")

    adapter = create_llm_adapter(context.config)
    if isinstance(adapter, MockLLMAdapter):
        return None
    return adapter


def requires_real_llm(context: PipelineContext) -> bool:
    """Return whether the current mode forbids deterministic LLM fallback."""

    return llm_mode(context) in REQUIRED_LLM_MODES


def llm_mode(context: PipelineContext) -> str:
    """Return the configured LLM mode with a safe offline default."""

    mode_config = context.config.get("mode")
    if isinstance(mode_config, Mapping) and mode_config.get("llm") is not None:
        return str(mode_config["llm"]).strip().lower()

    llm_config = context.config.get("llm")
    if isinstance(llm_config, Mapping) and llm_config.get("mode") is not None:
        return str(llm_config["mode"]).strip().lower()

    return os.getenv("LLM_MODE", "rule_based_mock").strip().lower()


def load_prompt_template(
    context: PipelineContext,
    config_key: str,
    default_path: str | Path,
) -> str:
    """Load a prompt template from config or the default prompt path."""

    path = Path(default_path)
    configured = context.config.get("prompts")
    if isinstance(configured, Mapping) and configured.get(config_key):
        path = Path(str(configured[config_key]))
    return path.read_text(encoding="utf-8")


def parse_llm_json(content: str, label: str) -> Any:
    """Parse JSON returned by an LLM, accepting a single fenced JSON block."""

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        preview = text[:200].replace("\n", " ")
        raise ValueError(f"{label} returned invalid JSON: {exc}; preview={preview}") from exc


def llm_result_payload(result: LLMResult) -> dict[str, Any]:
    """Return JSON-friendly LLM result metadata without prompt or API key."""

    return {
        "model": result.model,
        "success": result.success,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "total_tokens": result.total_tokens,
        "cost_usd": result.cost_usd,
        "elapsed_ms": result.elapsed_ms,
        "error": result.error,
    }


def llm_exception_payload(adapter: object, error: Exception) -> dict[str, Any]:
    """Return JSON-friendly metadata when an adapter raises before returning."""

    model = getattr(adapter, "model", adapter.__class__.__name__)
    return {
        "model": str(model),
        "success": False,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "elapsed_ms": 0,
        "error": _short_error(error),
    }


def llm_error_payload(error: LLMBusinessError) -> dict[str, Any]:
    """Serialize a classified LLM business-layer error."""

    payload: dict[str, Any] = {
        "error_type": error.error_type,
        "message": _trim_error_message(str(error)),
    }
    if error.details:
        payload["details"] = _json_safe(error.details)
    return payload


def record_llm_business_error(
    context: PipelineContext,
    step_name: str,
    error: LLMBusinessError,
) -> dict[str, Any]:
    """Record a classified LLM business-layer error for trace and metrics."""

    payload = llm_error_payload(error)
    _append_state_list(context, f"{step_name}_llm_business_errors", payload)
    _append_state_list(context, "llm_business_errors", {"step_name": step_name, **payload})
    return payload


def record_llm_fallback(
    context: PipelineContext,
    step_name: str,
    *,
    reason: str,
    error_type: str,
    item_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record an LLM fallback action without prompts or full model output."""

    if error_type not in LLM_BUSINESS_ERROR_TYPES:
        raise ValueError(f"unsupported LLM fallback error type: {error_type}")
    payload: dict[str, Any] = {
        "reason": _trim_error_message(reason),
        "error_type": error_type,
    }
    if item_id:
        payload["item_id"] = item_id
    if details:
        payload["details"] = _json_safe(details)
    _append_state_list(context, f"{step_name}_llm_fallbacks", payload)
    _append_state_list(context, "llm_fallbacks", {"step_name": step_name, **payload})
    return payload


def llm_call_with_business_retries(
    context: PipelineContext,
    adapter: object,
    prompt: str,
    *,
    operation: str,
    call_metadata: dict[str, Any] | None = None,
    parse_result: Callable[[str], T],
    calls: list[dict[str, Any]] | None = None,
) -> tuple[T, list[dict[str, Any]]]:
    """Call an LLM with business-layer retries around parsing and validation."""

    max_attempts = llm_business_retry_attempts(context) + 1
    base_metadata = dict(call_metadata or {})
    recorded_calls = calls if calls is not None else []
    last_error: LLMBusinessError | None = None

    for attempt in range(1, max_attempts + 1):
        attempt_metadata = {
            **base_metadata,
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        try:
            result = adapter.generate(prompt)
        except Exception as exc:
            recorded_calls.append({**attempt_metadata, **llm_exception_payload(adapter, exc)})
            last_error = LLMBusinessError(
                "transport_error",
                f"{operation} LLM call raised before returning: {exc}",
                details=attempt_metadata,
            )
            continue

        recorded_calls.append({**attempt_metadata, **llm_result_payload(result)})
        if not result.success:
            last_error = LLMBusinessError(
                "transport_error",
                f"{operation} LLM call failed: {result.error}",
                details=attempt_metadata,
            )
            continue

        try:
            return parse_result(result.content), recorded_calls
        except LLMBusinessError as error:
            last_error = LLMBusinessError(
                error.error_type,
                str(error),
                details={**error.details, **attempt_metadata},
            )
        except Exception as exc:
            last_error = LLMBusinessError(
                "schema_error",
                f"{operation} failed after LLM response validation: {exc}",
                details=attempt_metadata,
            )

    if last_error is None:  # pragma: no cover - defensive guard
        last_error = LLMBusinessError(
            "transport_error",
            f"{operation} LLM call failed without a returned result",
            details=base_metadata,
        )
    raise last_error


def parallel_map_ordered(
    items: list[T],
    worker: Callable[[T], U],
    *,
    max_workers: int | None = None,
) -> list[U]:
    """Run a worker concurrently while preserving input order in the result."""

    if not items:
        return []
    workers = max_workers if max_workers is not None else len(items)
    workers = max(1, min(int(workers), len(items)))
    if workers == 1:
        return [worker(item) for item in items]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(worker, items))


def llm_max_concurrency(context: PipelineContext, item_count: int) -> int:
    """Return the LLM item-level concurrency limit; 0 or missing means all items."""

    if item_count <= 0:
        return 1
    pipeline_config = context.config.get("pipeline")
    configured: Any = None
    if isinstance(pipeline_config, Mapping):
        configured = pipeline_config.get("llm_max_concurrency")
    if configured is None:
        return item_count
    value = int(configured)
    if value <= 0:
        return item_count
    return max(1, min(value, item_count))


def llm_business_retry_attempts(context: PipelineContext) -> int:
    """Return business-layer retry attempts after the initial LLM call."""

    pipeline_config = context.config.get("pipeline")
    if isinstance(pipeline_config, Mapping):
        for key in ("llm_business_retry_attempts", "llm_retry_attempts"):
            if pipeline_config.get(key) is not None:
                return max(0, int(pipeline_config[key]))
    return DEFAULT_LLM_BUSINESS_RETRY_ATTEMPTS


def config_int(
    context: PipelineContext,
    section: str,
    key: str,
    default: int,
) -> int:
    """Read a positive integer from a config section."""

    value: Any = default
    section_config = context.config.get(section)
    if isinstance(section_config, Mapping) and section_config.get(key) is not None:
        value = section_config[key]
    return max(1, int(value))


def report_timezone_name(context: PipelineContext) -> str:
    """Return the configured report timezone name."""

    configured = context.config.get("report_timezone")
    if configured:
        return str(configured).strip()

    pipeline_config = context.config.get("pipeline")
    if isinstance(pipeline_config, Mapping) and pipeline_config.get("report_timezone"):
        return str(pipeline_config["report_timezone"]).strip()

    return "UTC"


def report_timezone(context: PipelineContext) -> timezone | ZoneInfo:
    """Return the configured report timezone object."""

    return parse_timezone(report_timezone_name(context))


def parse_timezone(value: str) -> timezone | ZoneInfo:
    """Parse a timezone from config."""

    normalized = value.strip()
    if not normalized or normalized.upper() == "UTC":
        return timezone.utc
    if normalized == "Asia/Shanghai":
        return timezone(timedelta(hours=8), name="Asia/Shanghai")
    if re.fullmatch(r"[+-]\d{2}:\d{2}", normalized):
        sign = 1 if normalized[0] == "+" else -1
        hours = int(normalized[1:3])
        minutes = int(normalized[4:6])
        offset = timedelta(hours=hours, minutes=minutes) * sign
        return timezone(offset, name=normalized)

    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as error:
        raise ValueError(f"unsupported report timezone: {normalized}") from error


def normalize_timestamp(value: datetime) -> datetime:
    """Normalize timestamps, treating naive datetimes as UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def is_on_report_date(
    published_at: datetime,
    run_date: date,
    timezone_info: timezone | ZoneInfo,
) -> bool:
    """Return whether a timestamp falls on the report date in the given timezone."""

    return normalize_timestamp(published_at).astimezone(timezone_info).date() == run_date


def _append_state_list(
    context: PipelineContext,
    key: str,
    payload: dict[str, Any],
) -> None:
    current = context.get(key)
    values = list(current) if isinstance(current, list) else []
    values.append(payload)
    context.set(key, values)


def _short_error(error: Exception) -> str:
    return _trim_error_message(f"{error.__class__.__name__}: {error}")


def _trim_error_message(value: str, max_len: int = 240) -> str:
    normalized = " ".join(str(value).split())
    if len(normalized) <= max_len:
        return normalized
    return f"{normalized[: max_len - 3]}..."


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
