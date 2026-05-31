"""Run metrics aggregation for dashboard and alerting surfaces."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .context import PipelineContext


COUNT_ARTIFACTS = ("raw", "cleaned", "relevant", "structured", "validated")


def build_run_metrics(
    context: PipelineContext,
    manifest: dict[str, Any],
    *,
    metrics_path: Path,
    latest_metrics_path: Path,
) -> dict[str, Any]:
    """Build a human-facing summary for one pipeline run."""

    trace_events = _trace_events(context, manifest)
    step_events = _completion_events(trace_events)
    steps = _step_metrics(manifest, step_events)
    llm = _llm_metrics(steps)
    sources = _source_metrics(steps)
    data_quality = _data_quality_metrics(steps)
    health = _health(manifest, llm, sources, data_quality)

    return {
        "schema_version": 1,
        "run_id": manifest.get("run_id", context.run_id),
        "run_date": manifest.get("run_date", context.run_date.isoformat()),
        "report_timezone": manifest.get("report_timezone"),
        "mode": manifest.get("mode"),
        "parent_run_id": manifest.get("parent_run_id"),
        "resume_from": manifest.get("resume_from"),
        "status": manifest.get("status"),
        "health": health,
        "started_at": manifest.get("started_at"),
        "finished_at": manifest.get("finished_at"),
        "duration_ms": _duration_ms(
            manifest.get("started_at"),
            manifest.get("finished_at"),
        ),
        "counts": _artifact_counts(manifest),
        "steps": steps,
        "sources": sources,
        "data_quality": data_quality,
        "llm": llm,
        "artifacts": manifest.get("artifacts", {}),
        "manifest_path": _relative(metrics_path.with_name("manifest.json")),
        "metrics_path": _relative(metrics_path),
        "latest_metrics_path": _relative(latest_metrics_path),
    }


def _trace_events(
    context: PipelineContext,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    path = _trace_snapshot_path(manifest)
    if path is None or not path.exists():
        path = context.paths.get("trace")
    if path is None or not path.exists():
        return []

    events: list[dict[str, Any]] = []
    run_id = manifest.get("run_id", context.run_id)
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("run_id") == run_id:
            events.append(payload)
    return events


def _trace_snapshot_path(manifest: dict[str, Any]) -> Path | None:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts.get("trace"):
        return None
    return _resolve_path(artifacts["trace"])


def _completion_events(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    completions: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") not in {"step_finished", "step_failed"}:
            continue
        step_name = event.get("step_name")
        if isinstance(step_name, str) and step_name:
            completions[step_name] = event
    return completions


def _step_metrics(
    manifest: dict[str, Any],
    completion_events: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    steps = manifest.get("steps")
    if not isinstance(steps, dict):
        steps = {}

    metrics: dict[str, dict[str, Any]] = {}
    step_names = list(steps.keys())
    for step_name in completion_events:
        if step_name not in steps:
            step_names.append(step_name)

    for step_name in step_names:
        manifest_step = steps.get(step_name, {})
        if not isinstance(manifest_step, dict):
            manifest_step = {}
        event = completion_events.get(step_name, {})
        step_metric = {
            "status": manifest_step.get("status"),
            "started_at": manifest_step.get("started_at"),
            "finished_at": manifest_step.get("finished_at"),
            "duration_ms": event.get("duration_ms"),
        }
        if manifest_step.get("error") is not None:
            step_metric["error"] = manifest_step["error"]
        metadata = event.get("metadata")
        if isinstance(metadata, dict):
            if isinstance(metadata.get("sources"), dict):
                step_metric["sources"] = metadata["sources"]
            if isinstance(metadata.get("quality"), dict):
                step_metric["quality"] = metadata["quality"]
            if isinstance(metadata.get("llm"), dict):
                step_metric["llm"] = metadata["llm"]
        metrics[step_name] = step_metric

    return metrics


def _llm_metrics(steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    by_step: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    errors: list[str] = []
    business_errors: list[dict[str, Any]] = []
    fallbacks: list[dict[str, Any]] = []
    business_error_types: dict[str, int] = {}
    fallback_error_types: dict[str, int] = {}
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cost_usd = 0.0
    elapsed_ms = 0
    call_count = 0
    success = True

    for step_name, step in steps.items():
        llm = step.get("llm")
        if not isinstance(llm, dict):
            continue
        by_step[step_name] = llm
        call_count += _int(llm.get("call_count"))
        prompt_tokens += _int(llm.get("prompt_tokens"))
        completion_tokens += _int(llm.get("completion_tokens"))
        total_tokens += _int(llm.get("total_tokens"))
        cost_usd += _float(llm.get("cost_usd"))
        elapsed_ms += _int(llm.get("elapsed_ms"))
        success = success and bool(llm.get("success", True))

        llm_errors = llm.get("errors")
        if isinstance(llm_errors, list):
            errors.extend(str(error) for error in llm_errors if error is not None)

        step_business_errors = llm.get("business_errors")
        if isinstance(step_business_errors, list):
            for error in step_business_errors:
                if isinstance(error, dict):
                    business_errors.append({"step_name": step_name, **error})
                    _increment(
                        business_error_types,
                        str(error.get("error_type") or ""),
                    )

        step_fallbacks = llm.get("fallbacks")
        if isinstance(step_fallbacks, list):
            for fallback in step_fallbacks:
                if isinstance(fallback, dict):
                    fallbacks.append({"step_name": step_name, **fallback})
                    _increment(
                        fallback_error_types,
                        str(fallback.get("error_type") or ""),
                    )

        step_calls = llm.get("calls")
        if isinstance(step_calls, list):
            for call in step_calls:
                if isinstance(call, dict):
                    calls.append({"step_name": step_name, **call})

    return {
        "call_count": call_count,
        "success": success,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cost_usd": round(cost_usd, 10),
        "elapsed_ms": elapsed_ms,
        "errors": errors,
        "business_error_count": len(business_errors),
        "business_error_types": dict(sorted(business_error_types.items())),
        "business_errors": business_errors,
        "fallback_count": len(fallbacks),
        "fallback_error_types": dict(sorted(fallback_error_types.items())),
        "fallback_item_count": sum(1 for fallback in fallbacks if fallback.get("item_id")),
        "fallbacks": fallbacks,
        "by_step": by_step,
        "calls": calls,
    }


def _source_metrics(steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    collect = steps.get("collect", {})
    sources = collect.get("sources") if isinstance(collect, dict) else None
    return sources if isinstance(sources, dict) else {}


def _data_quality_metrics(steps: dict[str, dict[str, Any]]) -> dict[str, Any]:
    clean = steps.get("clean", {})
    quality = clean.get("quality") if isinstance(clean, dict) else None
    return quality if isinstance(quality, dict) else {}


def _health(
    manifest: dict[str, Any],
    llm: dict[str, Any],
    sources: dict[str, Any],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    status = str(manifest.get("status") or "unknown")
    error = manifest.get("error")
    failed_step = error.get("step_name") if isinstance(error, dict) else None

    if _int(sources.get("failed")):
        warnings.append(f"{_int(sources.get('failed'))} source(s) failed")
    if _int(sources.get("partial")):
        warnings.append(f"{_int(sources.get('partial'))} source(s) partially succeeded")
    if _int(sources.get("rate_limited")):
        warnings.append(f"{_int(sources.get('rate_limited'))} source(s) were rate limited")
    if llm.get("errors"):
        warnings.append(f"{len(llm['errors'])} LLM error(s) recorded")
    if _int(llm.get("business_error_count")):
        warnings.append(f"{_int(llm.get('business_error_count'))} LLM business error(s) recorded")
    if _int(llm.get("fallback_count")):
        warnings.append(f"{_int(llm.get('fallback_count'))} LLM fallback(s) used")
    if data_quality.get("status") not in (None, "", "ok", "succeeded"):
        warnings.append(f"clean quality status is {data_quality.get('status')}")

    if status == "failed":
        health_status = "failed"
    elif warnings:
        health_status = "warning"
    elif status == "succeeded":
        health_status = "healthy"
    else:
        health_status = status

    return {
        "status": health_status,
        "failed_step": failed_step,
        "error": error,
        "warnings": warnings,
    }


def _artifact_counts(manifest: dict[str, Any]) -> dict[str, int | None]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}

    counts: dict[str, int | None] = {}
    for name in COUNT_ARTIFACTS:
        path_value = artifacts.get(name)
        counts[name] = _json_list_count(path_value) if path_value else None
    return counts


def _json_list_count(path_value: Any) -> int | None:
    path = _resolve_path(path_value)
    if path is None or not path.exists() or path.is_dir():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return len(payload) if isinstance(payload, list) else None


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at))
        finished = datetime.fromisoformat(str(finished_at))
    except (TypeError, ValueError):
        return None
    try:
        return max(0, round((finished - started).total_seconds() * 1000))
    except TypeError:
        return None


def _resolve_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    path = Path(str(value))
    return path if path.is_absolute() else Path.cwd() / path


def _relative(path: Path) -> str:
    try:
        return path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        return path.as_posix()


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _increment(counts: dict[str, int], key: str) -> None:
    normalized = key.strip()
    if not normalized:
        return
    counts[normalized] = counts.get(normalized, 0) + 1
