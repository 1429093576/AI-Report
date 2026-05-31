"""Pre-processing hook for the pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.harness import (
    MemoryManager,
    PipelineContext,
    add_memory_report_stage,
    add_memory_report_warning,
    new_memory_report,
    write_memory_report,
)
from src.harness.memory_fulltext import (
    DEFAULT_MAX_FULLTEXT_CHARS_PER_ITEM,
    DEFAULT_MAX_FULLTEXT_ITEMS,
    DEFAULT_MAX_SELECTOR_CATALOG_CHARS,
    available_fulltext_candidates,
    build_fulltext_candidates,
    empty_fulltext_selection,
    heuristic_select_fulltext_ids,
    read_fulltext_items,
    selector_catalog,
    validate_fulltext_selection,
)
from src.harness.memory_runtime import (
    effective_memory_items_dir,
    effective_memory_read_path,
    memory_replay_snapshot,
)
from src.harness.memory_similarity import assess_soft_similarity, empty_soft_similarity
from src.pipeline.utils import (
    LLMBusinessError,
    active_llm_adapter,
    llm_call_with_business_retries,
    parse_llm_json,
    record_llm_business_error,
    record_llm_fallback,
    requires_real_llm,
)
from src.schemas import normalize_topic_label

DEFAULT_MAX_METADATA_ITEMS_PER_TOPIC = 10
DEFAULT_MAX_METADATA_CONTEXT_CHARS = 16000


def run(context: PipelineContext) -> PipelineContext:
    """Load historical topic context before analysis-oriented steps."""

    topics = _topics_from_context(context)
    memory_path = _memory_path(context)
    report_path = _memory_report_path(context)
    if not topics:
        soft_similarity = empty_soft_similarity()
        fulltext_selection = empty_fulltext_selection(
            budget=_fulltext_budget(context),
        )
        metadata_budget = _metadata_budget(context)
        metadata_context = _empty_metadata_context(metadata_budget)
        context.historical_context = ""
        context.set("historical_context_topics", [])
        context.set(
            "memory_context",
            _empty_memory_context(
                soft_similarity=soft_similarity,
                fulltext_selection=fulltext_selection,
                metadata_context=metadata_context,
            ),
        )
        _write_context_retrieval_report(
            context,
            report_path=report_path,
            memory_path=memory_path,
            topics=[],
            topic_entries={},
            window_days=_config_int(context, "memory_window_days", 7),
            limit=metadata_budget["max_history_items_per_topic"],
            excluded_keys=set(),
            metadata_context=metadata_context,
            soft_similarity=soft_similarity,
            fulltext_selection=fulltext_selection,
            status="skipped",
        )
        return context

    memory = MemoryManager(memory_path, items_dir=_memory_items_dir(context, memory_path))
    window_days = _config_int(context, "memory_window_days", 7)
    metadata_budget = _metadata_budget(context)
    limit = metadata_budget["max_history_items_per_topic"]
    excluded_keys = _current_item_keys(context)
    topic_entries = {
        topic: memory.retrieve_entries(
            topic,
            window_days=window_days,
            limit=limit,
            exclude_keys=excluded_keys,
        )
        for topic in topics
    }
    metadata_context = _metadata_context(
        topics,
        topic_entries,
        max_chars=metadata_budget["max_metadata_context_chars"],
        budget=metadata_budget,
    )
    sections = [metadata_context["text"]] if metadata_context["text"] else []
    soft_similarity = assess_soft_similarity(
        _validated_items(context),
        topic_entries,
        max_matches_per_item=_config_int(context, "max_soft_matches_per_item", 3),
    )
    fulltext_selection = _select_fulltext_items(
        context,
        topic_entries,
        soft_similarity,
        memory_path=memory_path,
        items_dir=memory.items_dir,
    )
    fulltext_section = _format_fulltext_context(fulltext_selection.get("items", []))
    if fulltext_section:
        sections.append(fulltext_section)

    context.historical_context = "\n\n".join(sections)
    context.set("historical_context_topics", topics)
    context.set(
        "memory_context",
        _memory_context(
            topics=topics,
            topic_entries=metadata_context["topic_entries"],
            retrieved_topic_entries=topic_entries,
            window_days=window_days,
            limit=limit,
            excluded_keys=excluded_keys,
            metadata_context=metadata_context,
            soft_similarity=soft_similarity,
            fulltext_selection=fulltext_selection,
        ),
    )
    _write_context_retrieval_report(
        context,
        report_path=report_path,
        memory_path=memory_path,
        topics=topics,
        topic_entries=topic_entries,
        window_days=window_days,
        limit=limit,
        excluded_keys=excluded_keys,
        metadata_context=metadata_context,
        soft_similarity=soft_similarity,
        fulltext_selection=fulltext_selection,
    )
    return context


def _topics_from_context(context: PipelineContext) -> list[str]:
    configured = _memory_config(context).get("topics", context.get("topics", []))
    if isinstance(configured, str):
        candidates: list[Any] = [configured]
    else:
        candidates = list(configured or [])
    if not candidates:
        candidates = _topics_from_items(
            context.get("validated_items", context.get("validate", []))
        )

    topics: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        topic = _canonical_topic(candidate)
        key = topic.lower()
        if topic and key not in seen:
            topics.append(topic)
            seen.add(key)
    return topics


def _topics_from_items(items: Any) -> list[str]:
    topics: list[str] = []
    for item in list(items or []):
        if isinstance(item, dict):
            topic = item.get("topic", "")
        else:
            topic = getattr(item, "topic", "")
        if topic:
            topics.append(str(topic))
    return topics


def _canonical_topic(candidate: Any) -> str:
    topic = str(candidate).strip()
    if not topic:
        return ""
    try:
        return normalize_topic_label(topic)
    except ValueError:
        return topic


def _current_item_keys(context: PipelineContext) -> set[str]:
    items = context.get("validated_items", context.get("validate", []))
    keys: set[str] = set()
    for item in list(items or []):
        for field in ("id", "url", "content_hash"):
            if isinstance(item, dict):
                value = item.get(field)
            else:
                value = getattr(item, field, None)
            if value:
                keys.add(str(value))
    return keys


def _validated_items(context: PipelineContext) -> list[Any]:
    return list(context.get("validated_items", context.get("validate", [])) or [])


def _memory_path(context: PipelineContext) -> Path:
    return effective_memory_read_path(context)


def _memory_report_path(context: PipelineContext) -> Path:
    configured = _memory_config(context).get(
        "report_path",
        context.paths.get("memory_report", "logs/memory_report.json"),
    )
    return Path(str(configured))


def _memory_items_dir(context: PipelineContext, memory_path: Path) -> Path:
    return effective_memory_items_dir(context, memory_path)


def _config_int(context: PipelineContext, key: str, default: int) -> int:
    value = _memory_config(context).get(key, context.config.get(key, default))
    return int(value)


def _budget_int(
    context: PipelineContext,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = _config_int(context, key, default)
    return min(maximum, max(minimum, value))


def _metadata_budget(context: PipelineContext) -> dict[str, int]:
    return {
        "max_history_items_per_topic": _budget_int(
            context,
            "max_history_items_per_topic",
            DEFAULT_MAX_METADATA_ITEMS_PER_TOPIC,
            minimum=1,
            maximum=DEFAULT_MAX_METADATA_ITEMS_PER_TOPIC,
        ),
        "max_metadata_context_chars": _budget_int(
            context,
            "max_metadata_context_chars",
            DEFAULT_MAX_METADATA_CONTEXT_CHARS,
            minimum=0,
            maximum=DEFAULT_MAX_METADATA_CONTEXT_CHARS,
        ),
    }


def _memory_config(context: PipelineContext) -> dict[str, Any]:
    config = context.config.get("memory", {})
    return config if isinstance(config, dict) else {}


def _memory_context(
    *,
    topics: list[str],
    topic_entries: dict[str, list[dict[str, Any]]],
    retrieved_topic_entries: dict[str, list[dict[str, Any]]] | None = None,
    window_days: int,
    limit: int,
    excluded_keys: set[str],
    metadata_context: dict[str, Any],
    soft_similarity: dict[str, Any],
    fulltext_selection: dict[str, Any],
) -> dict[str, Any]:
    retrieved_entries = retrieved_topic_entries or topic_entries
    return {
        "schema_version": 1,
        "topics": [
            {
                "topic": topic,
                "entries": topic_entries.get(topic, []),
                "retrieved_count": len(retrieved_entries.get(topic, [])),
                "metadata_included_count": len(topic_entries.get(topic, [])),
            }
            for topic in topics
        ],
        "retrieved_count": sum(len(entries) for entries in retrieved_entries.values()),
        "metadata_included_count": sum(len(entries) for entries in topic_entries.values()),
        "window_days": window_days,
        "max_history_items_per_topic": limit,
        "metadata_context": _metadata_context_summary(metadata_context),
        "excluded_current_item_keys": sorted(excluded_keys),
        "item_relationships": list(soft_similarity.get("items", [])),
        "soft_similarity": soft_similarity,
        "fulltext_selection": {
            key: value
            for key, value in fulltext_selection.items()
            if key != "items"
        },
        "fulltext_items": list(fulltext_selection.get("items", [])),
    }


def _empty_memory_context(
    soft_similarity: dict[str, Any] | None = None,
    fulltext_selection: dict[str, Any] | None = None,
    metadata_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    similarity = soft_similarity or empty_soft_similarity()
    fulltext = fulltext_selection or empty_fulltext_selection()
    metadata = metadata_context or _empty_metadata_context(
        {
            "max_history_items_per_topic": DEFAULT_MAX_METADATA_ITEMS_PER_TOPIC,
            "max_metadata_context_chars": DEFAULT_MAX_METADATA_CONTEXT_CHARS,
        }
    )
    return {
        "schema_version": 1,
        "topics": [],
        "retrieved_count": 0,
        "metadata_included_count": 0,
        "window_days": None,
        "max_history_items_per_topic": None,
        "metadata_context": _metadata_context_summary(metadata),
        "excluded_current_item_keys": [],
        "item_relationships": [],
        "soft_similarity": similarity,
        "fulltext_selection": fulltext,
        "fulltext_items": [],
    }


def _format_context(topic: str, entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""

    lines = [f"Historical context for topic: {topic.strip().lower()}"]
    for index, entry in enumerate(entries, start=1):
        published_at = entry.get("published_at", "")
        title = entry.get("title", "")
        source = entry.get("source", "")
        summary = entry.get("summary", "")
        score = entry.get("importance_score", "")
        lines.append(
            f"{index}. [{published_at}] {title} ({source}) "
            f"importance={score}: {summary}"
        )
    return "\n".join(lines)


def _metadata_context(
    topics: list[str],
    topic_entries: dict[str, list[dict[str, Any]]],
    *,
    max_chars: int,
    budget: dict[str, int],
) -> dict[str, Any]:
    original_sections = [
        _format_context(topic, topic_entries.get(topic, []))
        for topic in topics
        if topic_entries.get(topic)
    ]
    original_text = "\n\n".join(section for section in original_sections if section)
    if max_chars <= 0:
        return {
            "topic_entries": {topic: [] for topic in topics},
            "text": "",
            "retrieved_count": sum(len(entries) for entries in topic_entries.values()),
            "included_count": 0,
            "original_context_chars": len(original_text),
            "context_chars": 0,
            "truncated": bool(original_text),
            "budget": dict(budget),
        }

    sections: list[str] = []
    included_topic_entries: dict[str, list[dict[str, Any]]] = {
        topic: [] for topic in topics
    }
    truncated = False

    for topic in topics:
        current_entries: list[dict[str, Any]] = []
        for entry in topic_entries.get(topic, []):
            candidate_entries = [*current_entries, entry]
            candidate_section = _format_context(topic, candidate_entries)
            if current_entries:
                candidate_sections = [*sections[:-1], candidate_section]
            else:
                candidate_sections = [*sections, candidate_section]
            candidate_text = "\n\n".join(candidate_sections)
            if len(candidate_text) > max_chars:
                truncated = True
                break

            current_entries.append(entry)
            included_topic_entries[topic] = current_entries
            if len(current_entries) == 1:
                sections.append(candidate_section)
            else:
                sections[-1] = candidate_section
        if truncated:
            break

    text = "\n\n".join(sections)
    retrieved_count = sum(len(entries) for entries in topic_entries.values())
    included_count = sum(len(entries) for entries in included_topic_entries.values())
    return {
        "topic_entries": included_topic_entries,
        "text": text,
        "retrieved_count": retrieved_count,
        "included_count": included_count,
        "original_context_chars": len(original_text),
        "context_chars": len(text),
        "truncated": truncated or included_count < retrieved_count,
        "budget": dict(budget),
    }


def _empty_metadata_context(budget: dict[str, int]) -> dict[str, Any]:
    return {
        "topic_entries": {},
        "text": "",
        "retrieved_count": 0,
        "included_count": 0,
        "original_context_chars": 0,
        "context_chars": 0,
        "truncated": False,
        "budget": dict(budget),
    }


def _metadata_context_summary(metadata_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "budget": dict(metadata_context.get("budget") or {}),
        "retrieved_count": int(metadata_context.get("retrieved_count") or 0),
        "included_count": int(metadata_context.get("included_count") or 0),
        "original_context_chars": int(
            metadata_context.get("original_context_chars") or 0
        ),
        "context_chars": int(metadata_context.get("context_chars") or 0),
        "truncated": bool(metadata_context.get("truncated", False)),
    }


def _select_fulltext_items(
    context: PipelineContext,
    topic_entries: dict[str, list[dict[str, Any]]],
    soft_similarity: dict[str, Any],
    *,
    memory_path: Path,
    items_dir: Path,
) -> dict[str, Any]:
    budget = _fulltext_budget(context)
    candidates = available_fulltext_candidates(
        build_fulltext_candidates(
            topic_entries,
            memory_path=memory_path,
            items_dir=items_dir,
        )
    )
    if not candidates:
        return empty_fulltext_selection(
            status="skipped",
            mode="none",
            budget=budget,
        ) | {"candidate_count": 0}

    adapter = active_llm_adapter(context)
    mode = "llm" if adapter is not None else "heuristic"
    requested_ids: list[Any]
    llm_call: dict[str, Any] | None = None
    selector_error: str | None = None
    catalog = selector_catalog(
        candidates,
        max_chars=budget["max_selector_catalog_chars"],
    )

    if adapter is None:
        requested_ids = heuristic_select_fulltext_ids(
            candidates,
            soft_similarity,
            limit=budget["max_fulltext_items"],
        )
    else:
        prompt = _fulltext_selector_prompt(context, catalog, soft_similarity)
        calls: list[dict[str, Any]] = []
        try:
            requested_ids, _ = llm_call_with_business_retries(
                context,
                adapter,
                prompt,
                operation="memory fulltext selector",
                call_metadata={"scope": "memory_fulltext_selector"},
                parse_result=_parse_selector_item_ids,
                calls=calls,
            )
        except LLMBusinessError as error:
            record_llm_business_error(context, "memory_fulltext_selector", error)
            if requires_real_llm(context):
                raise
            record_llm_fallback(
                context,
                "memory_fulltext_selector",
                reason=str(error),
                error_type=error.error_type,
            )
            selector_error = str(error)
            requested_ids = heuristic_select_fulltext_ids(
                candidates,
                soft_similarity,
                limit=budget["max_fulltext_items"],
            )
            mode = "heuristic_fallback"
        context.set("memory_fulltext_selector_llm_calls", calls)
        if calls:
            llm_call = _combined_llm_call(calls)
            context.set("memory_fulltext_selector_llm_call", llm_call)

    validation = validate_fulltext_selection(
        requested_ids,
        candidates,
        limit=budget["max_fulltext_items"],
    )
    read_result = read_fulltext_items(
        validation["selected_item_ids"],
        candidates,
        max_chars_per_item=budget["max_fulltext_chars_per_item"],
    )
    status = "succeeded" if read_result["items"] else "skipped"
    return {
        "status": status,
        "mode": mode,
        "candidate_count": len(candidates),
        "catalog_included_count": catalog["included_count"],
        "selected_count": len(read_result["items"]),
        **validation,
        "read_item_ids": read_result["read_item_ids"],
        "skipped_items": read_result["skipped_items"],
        "items": read_result["items"],
        "budget": budget,
        "truncated": bool(catalog["truncated"] or read_result["truncated"]),
        "selector_error": selector_error,
        "llm_call": llm_call,
    }


def _fulltext_selector_prompt(
    context: PipelineContext,
    catalog: dict[str, Any],
    soft_similarity: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "task": (
                "Select Memory items whose full text should be read before final "
                "analysis. Return only JSON."
            ),
            "output_contract": {
                "memory_item_ids": [
                    "IDs from candidate_catalog.records, ordered by usefulness"
                ],
            },
            "rules": [
                "Use only memory_item_id values present in candidate_catalog.records.",
                "Prefer continuing, likely_duplicate, or high-importance historical context.",
                "Do not select more than the requested max_fulltext_items.",
            ],
            "budget": _fulltext_budget(context),
            "current_items": _validated_item_catalog(context),
            "candidate_catalog": catalog,
            "soft_similarity": {
                "items": soft_similarity.get("items", []),
                "matches": soft_similarity.get("matches", []),
                "relationships": soft_similarity.get("relationships", {}),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def _parse_selector_item_ids(content: str) -> list[Any]:
    try:
        parsed = parse_llm_json(content, "memory fulltext selector")
    except ValueError as exc:
        raise LLMBusinessError("invalid_json", str(exc)) from exc
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        value = parsed.get("memory_item_ids", parsed.get("selected_item_ids", []))
        if isinstance(value, list):
            return value
    raise LLMBusinessError(
        "schema_error",
        "memory fulltext selector must return a JSON array or object with memory_item_ids",
    )


def _combined_llm_call(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "model": str(calls[-1].get("model") or ""),
        "success": all(bool(call.get("success")) for call in calls),
        "prompt_tokens": sum(_int(call.get("prompt_tokens")) for call in calls),
        "completion_tokens": sum(_int(call.get("completion_tokens")) for call in calls),
        "total_tokens": sum(_int(call.get("total_tokens")) for call in calls),
        "cost_usd": round(sum(_float(call.get("cost_usd")) for call in calls), 10),
        "elapsed_ms": sum(_int(call.get("elapsed_ms")) for call in calls),
        "error": "; ".join(
            str(call.get("error"))
            for call in calls
            if call.get("error") is not None
        )
        or None,
    }


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


def _validated_item_catalog(context: PipelineContext) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _validated_items(context):
        if isinstance(item, dict):
            payload = item
        elif hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        else:
            continue
        items.append(
            {
                "id": payload.get("id"),
                "topic": payload.get("topic"),
                "title": payload.get("title"),
                "source": payload.get("source"),
                "published_at": payload.get("published_at"),
                "summary": payload.get("summary"),
                "entities": payload.get("entities", []),
                "event_type": payload.get("event_type"),
                "importance_score": payload.get("importance_score"),
            }
        )
    return items


def _format_fulltext_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["Selected Memory fulltext context"]
    for index, item in enumerate(items, start=1):
        title = item.get("title") or ""
        source = item.get("source") or ""
        published_at = item.get("published_at") or ""
        content = item.get("content_excerpt") or ""
        lines.append(
            f"{index}. [{published_at}] {title} ({source}) "
            f"memory_item_id={item.get('memory_item_id')}"
        )
        if content:
            lines.append(str(content))
    return "\n".join(lines)


def _fulltext_budget(context: PipelineContext) -> dict[str, int]:
    return {
        "max_fulltext_items": _budget_int(
            context,
            "max_fulltext_items",
            DEFAULT_MAX_FULLTEXT_ITEMS,
            minimum=0,
            maximum=DEFAULT_MAX_FULLTEXT_ITEMS,
        ),
        "max_fulltext_chars_per_item": _budget_int(
            context,
            "max_fulltext_chars_per_item",
            DEFAULT_MAX_FULLTEXT_CHARS_PER_ITEM,
            minimum=0,
            maximum=DEFAULT_MAX_FULLTEXT_CHARS_PER_ITEM,
        ),
        "max_selector_catalog_chars": _budget_int(
            context,
            "max_selector_catalog_chars",
            DEFAULT_MAX_SELECTOR_CATALOG_CHARS,
            minimum=0,
            maximum=DEFAULT_MAX_SELECTOR_CATALOG_CHARS,
        ),
    }


def _write_context_retrieval_report(
    context: PipelineContext,
    *,
    report_path: Path,
    memory_path: Path,
    topics: list[str],
    topic_entries: dict[str, list[dict[str, Any]]],
    window_days: int,
    limit: int,
    excluded_keys: set[str],
    metadata_context: dict[str, Any],
    soft_similarity: dict[str, Any],
    fulltext_selection: dict[str, Any],
    status: str = "succeeded",
) -> None:
    report = _load_or_create_memory_report(
        context,
        report_path=report_path,
        memory_path=memory_path,
    )
    retrieved_count = sum(len(entries) for entries in topic_entries.values())
    if not memory_path.exists():
        add_memory_report_warning(
            report,
            "memory_not_found",
            "Memory file was not found; context retrieval used an empty index.",
        )

    report["context_retrieval"] = {
        "status": status,
        "memory_source": _memory_source(context),
        "topics": [
            {
                "topic": topic,
                "retrieved_count": len(topic_entries.get(topic, [])),
                "metadata_included_count": len(
                    (metadata_context.get("topic_entries") or {}).get(topic, [])
                ),
                "entry_ids": [
                    str(entry.get("id"))
                    for entry in topic_entries.get(topic, [])
                    if entry.get("id")
                ],
                "metadata_included_entry_ids": [
                    str(entry.get("id"))
                    for entry in (metadata_context.get("topic_entries") or {}).get(
                        topic,
                        [],
                    )
                    if entry.get("id")
                ],
            }
            for topic in topics
        ],
        "retrieved_count": retrieved_count,
        "metadata_included_count": int(metadata_context.get("included_count") or 0),
        "metadata_context": _metadata_context_summary(metadata_context),
        "budget": {
            "window_days": window_days,
            "max_history_items_per_topic": limit,
            "max_metadata_context_chars": int(
                (metadata_context.get("budget") or {}).get(
                    "max_metadata_context_chars",
                    DEFAULT_MAX_METADATA_CONTEXT_CHARS,
                )
            ),
        },
        "excluded_current_item_keys": sorted(excluded_keys),
        "truncated": bool(metadata_context.get("truncated", False)),
    }
    report["soft_similarity"] = {
        "status": soft_similarity.get("status", status),
        "item_count": soft_similarity.get("item_count", 0),
        "candidate_count": soft_similarity.get("candidate_count", 0),
        "match_count": soft_similarity.get("match_count", 0),
        "matched_item_count": soft_similarity.get("matched_item_count", 0),
        "relationships": soft_similarity.get("relationships", {}),
        "items": soft_similarity.get("items", []),
        "matches": soft_similarity.get("matches", []),
    }
    report["fulltext_selection"] = {
        "status": fulltext_selection.get("status", "skipped"),
        "mode": fulltext_selection.get("mode"),
        "candidate_count": fulltext_selection.get("candidate_count", 0),
        "catalog_included_count": fulltext_selection.get("catalog_included_count", 0),
        "selected_count": fulltext_selection.get("selected_count", 0),
        "requested_item_ids": fulltext_selection.get("requested_item_ids", []),
        "selected_item_ids": fulltext_selection.get("selected_item_ids", []),
        "invalid_item_ids": fulltext_selection.get("invalid_item_ids", []),
        "overflow_item_ids": fulltext_selection.get("overflow_item_ids", []),
        "read_item_ids": fulltext_selection.get("read_item_ids", []),
        "skipped_items": fulltext_selection.get("skipped_items", []),
        "budget": fulltext_selection.get("budget", {}),
        "truncated": bool(fulltext_selection.get("truncated", False)),
        "selector_error": fulltext_selection.get("selector_error"),
        "llm_call": fulltext_selection.get("llm_call"),
    }
    if fulltext_selection.get("selector_error"):
        add_memory_report_warning(
            report,
            "memory_fulltext_selector_fallback",
            str(fulltext_selection["selector_error"]),
        )
    add_memory_report_stage(
        report,
        "pre_analyze_context_retrieval",
        status=status,
        details={
            "topic_count": len(topics),
            "retrieved_count": retrieved_count,
            "metadata_included_count": int(metadata_context.get("included_count") or 0),
            "truncated": bool(metadata_context.get("truncated", False)),
        },
    )
    add_memory_report_stage(
        report,
        "pre_analyze_soft_similarity",
        status=str(soft_similarity.get("status", status)),
        details={
            "candidate_count": soft_similarity.get("candidate_count", 0),
            "match_count": soft_similarity.get("match_count", 0),
            "matched_item_count": soft_similarity.get("matched_item_count", 0),
        },
    )
    add_memory_report_stage(
        report,
        "pre_analyze_fulltext_selection",
        status=str(fulltext_selection.get("status", "skipped")),
        details={
            "mode": fulltext_selection.get("mode"),
            "candidate_count": fulltext_selection.get("candidate_count", 0),
            "selected_count": fulltext_selection.get("selected_count", 0),
            "truncated": bool(fulltext_selection.get("truncated", False)),
        },
    )
    write_memory_report(report_path, report)
    context.add_artifact("memory_report", report_path)
    context.set("memory_report", report)


def _load_or_create_memory_report(
    context: PipelineContext,
    *,
    report_path: Path,
    memory_path: Path,
) -> dict[str, Any]:
    if report_path.exists():
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            payload.setdefault("paths", {})
            if isinstance(payload["paths"], dict):
                payload["paths"].setdefault("memory", memory_path.as_posix())
                payload["paths"].setdefault("memory_report", report_path.as_posix())
            return payload

    return new_memory_report(
        context,
        memory_path=memory_path,
        report_path=report_path,
    )


def _memory_source(context: PipelineContext) -> dict[str, Any]:
    snapshot = memory_replay_snapshot(context)
    if snapshot.get("status") == "available":
        return {
            "mode": "parent_snapshot",
            "source_run_id": snapshot.get("source_run_id"),
            "memory_path": snapshot.get("memory_path"),
            "items_dir": snapshot.get("items_dir"),
        }
    if snapshot.get("status") == "missing":
        return {
            "mode": "latest_fallback",
            "source_run_id": snapshot.get("source_run_id"),
            "reason": "parent_memory_snapshot_missing",
        }
    return {"mode": "latest"}
