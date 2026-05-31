"""Lightweight pipeline runner for Harness-managed workflows."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from time import perf_counter
from typing import Any

from .checkpointer import Checkpointer
from .context import PipelineContext
from .hooks import HookRegistry
from .run_store import RunStore
from .tracer import InMemoryTracer, Tracer


PipelineStep = Callable[[PipelineContext], Any]
PipelineStepSpec = tuple[str, PipelineStep] | tuple[str, PipelineStep, str | None]


class PipelineRunner:
    """Execute pipeline steps with tracing and lifecycle hooks."""

    def __init__(
        self,
        context: PipelineContext,
        tracer: Tracer | None = None,
        hooks: HookRegistry | None = None,
        run_store: RunStore | None = None,
        checkpointer: Checkpointer | None = None,
        run_mode: str = "fresh",
        parent_run_id: str | None = None,
        resume_from: str | None = None,
    ) -> None:
        self.context = context
        self.tracer = tracer if tracer is not None else InMemoryTracer()
        self.hooks = hooks if hooks is not None else HookRegistry()
        self.run_store = run_store
        self.checkpointer = checkpointer
        self.run_mode = run_mode
        self.parent_run_id = parent_run_id
        self.resume_from = resume_from
        self._run_started = False

    def run_step(
        self,
        name: str,
        fn: PipelineStep,
        after_hook: str | None = None,
    ) -> Any:
        """Run one pipeline step and record its lifecycle events."""

        step_name = self._normalize_step_name(name)
        if not callable(fn):
            raise TypeError("pipeline step must be callable")

        self._ensure_run_started()
        checkpoint = (
            self.checkpointer.create(step_name, self.context)
            if self.checkpointer is not None
            else None
        )
        if self.run_store is not None:
            self.run_store.step_started(
                self.context,
                step_name,
                checkpoint.to_manifest() if checkpoint is not None else None,
            )

        start = perf_counter()
        self.tracer.step_started(step_name, self.context)

        try:
            result = fn(self.context)
            self._store_step_result(step_name, result)
            if after_hook is not None:
                self.context = self.hooks.run(after_hook, self.context)
        except Exception as error:
            duration_ms = self._elapsed_ms(start)
            rollback = (
                self.checkpointer.rollback(checkpoint)
                if self.checkpointer is not None and checkpoint is not None
                else None
            )
            self.tracer.step_failed(
                step_name,
                self.context,
                error,
                duration_ms,
                metadata=self._step_metadata(step_name),
            )
            if self.run_store is not None:
                self.run_store.step_failed(
                    self.context,
                    step_name,
                    error,
                    rollback=rollback,
                )
            self.context = self.hooks.run_error_hooks(self.context, error)
            raise

        duration_ms = self._elapsed_ms(start)
        self.tracer.step_finished(
            step_name,
            self.context,
            duration_ms,
            metadata=self._step_metadata(step_name),
        )
        if self.run_store is not None:
            self.run_store.step_finished(self.context, step_name)
        return result

    def run(
        self,
        steps: Iterable[PipelineStepSpec],
    ) -> PipelineContext:
        """Run a sequence of named pipeline steps."""

        self._ensure_run_started()
        self.context = self.hooks.run("pre_process", self.context)

        for step in steps:
            name, fn, after_hook = self._parse_step(step)
            self.run_step(name, fn, after_hook=after_hook)

        self.context = self.hooks.run("post_process", self.context)
        if self.run_store is not None:
            self.run_store.finish_run(self.context)
        return self.context

    def _ensure_run_started(self) -> None:
        if self._run_started:
            return
        if self.run_store is not None:
            self.run_store.start_run(
                self.context,
                mode=self.run_mode,
                parent_run_id=self.parent_run_id,
                resume_from=self.resume_from,
            )
        self.context.set("run_mode", self.run_mode)
        self.context.set("parent_run_id", self.parent_run_id)
        self.context.set("resume_from", self.resume_from)
        self._run_started = True

    def _store_step_result(self, step_name: str, result: Any) -> None:
        if isinstance(result, PipelineContext):
            self.context = result
        elif result is not None:
            self.context.set(step_name, result)

    def _step_metadata(self, step_name: str) -> dict[str, Any] | None:
        if step_name == "collect":
            source_metrics = self.context.get("source_metrics")
            if isinstance(source_metrics, list):
                return {"sources": self._summarize_source_metrics(source_metrics)}
        if step_name == "clean":
            clean_quality = self.context.get("clean_quality")
            if isinstance(clean_quality, dict):
                return {"quality": clean_quality}
        llm_calls = self._step_llm_calls(step_name)
        llm_business_errors = self._step_llm_business_errors(step_name)
        llm_fallbacks = self._step_llm_fallbacks(step_name)
        if not llm_calls and not llm_business_errors and not llm_fallbacks:
            return None
        summary = self._summarize_llm_calls(llm_calls)
        if llm_business_errors:
            summary["business_errors"] = llm_business_errors
            summary["business_error_count"] = len(llm_business_errors)
            summary["business_error_types"] = self._count_values(
                error.get("error_type") for error in llm_business_errors
            )
        if llm_fallbacks:
            summary["fallbacks"] = llm_fallbacks
            summary["fallback_count"] = len(llm_fallbacks)
            summary["fallback_error_types"] = self._count_values(
                fallback.get("error_type") for fallback in llm_fallbacks
            )
            summary["fallback_item_count"] = sum(
                1 for fallback in llm_fallbacks if fallback.get("item_id")
            )
        return {"llm": summary}

    def _step_llm_calls(self, step_name: str) -> list[dict[str, Any]]:
        if step_name == "relevance":
            calls = self.context.get("relevance_llm_calls")
            if isinstance(calls, list):
                return list(calls)
            call = self.context.get("relevance_llm_call")
            return [dict(call)] if isinstance(call, dict) else []
        if step_name == "extract":
            calls = self.context.get("extract_llm_calls")
            return list(calls) if isinstance(calls, list) else []
        if step_name == "analyze":
            calls = self.context.get("analyze_llm_calls")
            if isinstance(calls, list):
                return list(calls)
            call = self.context.get("analyze_llm_call")
            return [dict(call)] if isinstance(call, dict) else []
        return []

    def _step_llm_business_errors(self, step_name: str) -> list[dict[str, Any]]:
        errors = self.context.get(f"{step_name}_llm_business_errors")
        return [dict(error) for error in errors if isinstance(error, dict)] if isinstance(errors, list) else []

    def _step_llm_fallbacks(self, step_name: str) -> list[dict[str, Any]]:
        fallbacks = self.context.get(f"{step_name}_llm_fallbacks")
        return [dict(fallback) for fallback in fallbacks if isinstance(fallback, dict)] if isinstance(fallbacks, list) else []

    @staticmethod
    def _summarize_llm_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "call_count": len(calls),
            "success": all(bool(call.get("success")) for call in calls),
            "prompt_tokens": sum(PipelineRunner._int(call.get("prompt_tokens")) for call in calls),
            "completion_tokens": sum(
                PipelineRunner._int(call.get("completion_tokens")) for call in calls
            ),
            "total_tokens": sum(PipelineRunner._int(call.get("total_tokens")) for call in calls),
            "cost_usd": round(
                sum(PipelineRunner._float(call.get("cost_usd")) for call in calls),
                10,
            ),
            "elapsed_ms": sum(PipelineRunner._int(call.get("elapsed_ms")) for call in calls),
            "errors": [
                str(call.get("error"))
                for call in calls
                if call.get("error") is not None
            ],
            "calls": calls,
        }

    @staticmethod
    def _summarize_source_metrics(metrics: list[dict[str, Any]]) -> dict[str, Any]:
        statuses = ["succeeded", "partial", "failed", "rate_limited", "empty"]
        counts = {status: 0 for status in statuses}
        errors: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            status = str(metric.get("status") or "")
            if status not in counts:
                counts[status] = 0
            counts[status] += 1
            metric_errors = metric.get("errors")
            if isinstance(metric_errors, list):
                errors.extend(error for error in metric_errors if isinstance(error, dict))
            sources.append(metric)

        return {
            "total": len(sources),
            **counts,
            "items": sum(PipelineRunner._int(metric.get("items")) for metric in sources),
            "attempts": sum(PipelineRunner._int(metric.get("attempts")) for metric in sources),
            "duration_ms": sum(
                PipelineRunner._int(metric.get("duration_ms")) for metric in sources
            ),
            "errors": errors,
            "sources": sources,
        }

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _count_values(values: Iterable[Any]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for value in values:
            key = str(value or "").strip()
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _parse_step(step: PipelineStepSpec) -> tuple[str, PipelineStep, str | None]:
        if len(step) == 2:
            name, fn = step
            return name, fn, None
        if len(step) == 3:
            name, fn, after_hook = step
            return name, fn, after_hook
        raise ValueError("pipeline step must be (name, fn) or (name, fn, after_hook)")

    @staticmethod
    def _normalize_step_name(name: str) -> str:
        step_name = name.strip()
        if not step_name:
            raise ValueError("step name must not be empty")
        return step_name

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return max(0, round((perf_counter() - start) * 1000))
