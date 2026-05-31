"""Safe fulltext selection and loading for local Memory items."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_MAX_FULLTEXT_ITEMS = 5
DEFAULT_MAX_FULLTEXT_CHARS_PER_ITEM = 2000
DEFAULT_MAX_SELECTOR_CATALOG_CHARS = 16000


def build_fulltext_candidates(
    topic_entries: dict[str, list[dict[str, Any]]],
    *,
    memory_path: str | Path,
    items_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Build a safe, deduplicated catalog of Memory item fulltext candidates."""

    index_path = Path(memory_path)
    root = Path(items_dir) if items_dir is not None else index_path.parent / "items"
    root = root.resolve(strict=False)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for topic, entries in topic_entries.items():
        for entry in entries:
            memory_item_id = _memory_item_id(entry)
            if not memory_item_id or memory_item_id in seen:
                continue
            seen.add(memory_item_id)
            path, path_error = _resolve_memory_item_path(
                entry.get("memory_item_path"),
                memory_item_id=memory_item_id,
                memory_path=index_path,
                items_dir=root,
            )
            candidates.append(
                {
                    "memory_item_id": memory_item_id,
                    "topic": entry.get("topic") or topic,
                    "title": entry.get("title"),
                    "source": entry.get("source"),
                    "url": entry.get("url"),
                    "published_at": entry.get("published_at"),
                    "summary": entry.get("summary"),
                    "entities": list(entry.get("entities") or []),
                    "event_type": entry.get("event_type"),
                    "importance_score": entry.get("importance_score"),
                    "importance_rationale": entry.get("importance_rationale"),
                    "risk_level": entry.get("risk_level"),
                    "risk_rationale": entry.get("risk_rationale"),
                    "opportunity_level": entry.get("opportunity_level"),
                    "opportunity_rationale": entry.get("opportunity_rationale"),
                    "content_hash": entry.get("content_hash"),
                    "metadata": dict(entry.get("metadata") or {}),
                    "memory_item_path": path.as_posix() if path is not None else None,
                    "path_error": path_error,
                    "exists": bool(path and path.exists()),
                }
            )
    return candidates


def available_fulltext_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return candidates that have safe, existing Memory item files."""

    return [
        candidate
        for candidate in candidates
        if candidate.get("memory_item_id")
        and candidate.get("memory_item_path")
        and not candidate.get("path_error")
        and candidate.get("exists")
    ]


def heuristic_select_fulltext_ids(
    candidates: list[dict[str, Any]],
    soft_similarity: dict[str, Any],
    *,
    limit: int = DEFAULT_MAX_FULLTEXT_ITEMS,
) -> list[str]:
    """Select fulltext records deterministically when no LLM selector is available."""

    max_items = max(0, int(limit))
    if max_items == 0:
        return []

    by_id = {str(candidate["memory_item_id"]): candidate for candidate in candidates}
    selected: list[str] = []
    seen: set[str] = set()

    relationship_order = {
        "likely_duplicate": 4,
        "continuing": 3,
        "related_context": 2,
        "new": 1,
    }
    relationships = [
        relationship
        for relationship in list(soft_similarity.get("items") or [])
        if isinstance(relationship, dict)
    ]
    relationships.sort(
        key=lambda relationship: (
            relationship_order.get(str(relationship.get("relationship") or ""), 0),
            _float(relationship.get("confidence")),
        ),
        reverse=True,
    )
    for relationship in relationships:
        for memory_item_id in list(relationship.get("matched_memory_item_ids") or []):
            item_id = str(memory_item_id).strip()
            if item_id in by_id and item_id not in seen:
                selected.append(item_id)
                seen.add(item_id)
            if len(selected) >= max_items:
                return selected

    ranked_candidates = sorted(
        candidates,
        key=lambda candidate: (
            _int(candidate.get("importance_score")),
            str(candidate.get("published_at") or ""),
        ),
        reverse=True,
    )
    for candidate in ranked_candidates:
        item_id = str(candidate.get("memory_item_id") or "").strip()
        if item_id and item_id not in seen:
            selected.append(item_id)
            seen.add(item_id)
        if len(selected) >= max_items:
            break
    return selected


def validate_fulltext_selection(
    requested_ids: list[Any],
    candidates: list[dict[str, Any]],
    *,
    limit: int = DEFAULT_MAX_FULLTEXT_ITEMS,
) -> dict[str, Any]:
    """Validate selector output against the candidate catalog and count budget."""

    max_items = max(0, int(limit))
    candidate_ids = {str(candidate["memory_item_id"]) for candidate in candidates}
    requested: list[str] = []
    selected: list[str] = []
    invalid: list[str] = []
    seen: set[str] = set()

    for raw_id in requested_ids:
        item_id = str(raw_id).strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        requested.append(item_id)
        if item_id not in candidate_ids:
            invalid.append(item_id)
            continue
        if len(selected) < max_items:
            selected.append(item_id)

    overflow = [
        item_id
        for item_id in requested
        if item_id in candidate_ids and item_id not in selected
    ]
    return {
        "requested_item_ids": requested,
        "selected_item_ids": selected,
        "invalid_item_ids": invalid,
        "overflow_item_ids": overflow,
    }


def read_fulltext_items(
    selected_ids: list[str],
    candidates: list[dict[str, Any]],
    *,
    max_chars_per_item: int = DEFAULT_MAX_FULLTEXT_CHARS_PER_ITEM,
) -> dict[str, Any]:
    """Read selected Memory item files after validation and truncate body text."""

    by_id = {str(candidate["memory_item_id"]): candidate for candidate in candidates}
    max_chars = max(0, int(max_chars_per_item))
    items: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    truncated = False

    for item_id in selected_ids:
        candidate = by_id.get(item_id)
        if candidate is None:
            skipped.append({"memory_item_id": item_id, "reason": "not_in_catalog"})
            continue
        path_error = str(candidate.get("path_error") or "")
        if path_error:
            skipped.append({"memory_item_id": item_id, "reason": path_error})
            continue
        path_text = str(candidate.get("memory_item_path") or "")
        if not path_text:
            skipped.append({"memory_item_id": item_id, "reason": "missing_path"})
            continue
        path = Path(path_text)
        if not path.exists():
            skipped.append({"memory_item_id": item_id, "reason": "missing_file"})
            continue

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            skipped.append({"memory_item_id": item_id, "reason": "invalid_json"})
            continue
        if not isinstance(payload, dict):
            skipped.append({"memory_item_id": item_id, "reason": "invalid_shape"})
            continue

        item = _context_item(payload, candidate, max_chars=max_chars)
        truncated = truncated or bool(item.get("truncated"))
        items.append(item)

    return {
        "items": items,
        "skipped_items": skipped,
        "truncated": truncated,
        "read_item_ids": [str(item.get("memory_item_id")) for item in items],
    }


def selector_catalog(
    candidates: list[dict[str, Any]],
    *,
    max_chars: int = DEFAULT_MAX_SELECTOR_CATALOG_CHARS,
) -> dict[str, Any]:
    """Return a metadata-only catalog sized for an LLM selector prompt."""

    budget = max(0, int(max_chars))
    records: list[dict[str, Any]] = []
    truncated = False

    for candidate in candidates:
        record = {
            "memory_item_id": candidate.get("memory_item_id"),
            "topic": candidate.get("topic"),
            "title": candidate.get("title"),
            "source": candidate.get("source"),
            "url": candidate.get("url"),
            "published_at": candidate.get("published_at"),
            "summary": candidate.get("summary"),
            "entities": candidate.get("entities", []),
            "event_type": candidate.get("event_type"),
            "importance_score": candidate.get("importance_score"),
            "importance_rationale": candidate.get("importance_rationale"),
            "risk_level": candidate.get("risk_level"),
            "risk_rationale": candidate.get("risk_rationale"),
            "opportunity_level": candidate.get("opportunity_level"),
            "opportunity_rationale": candidate.get("opportunity_rationale"),
        }
        encoded = json.dumps(records + [record], ensure_ascii=False)
        if budget and len(encoded) > budget:
            truncated = True
            break
        records.append(record)

    return {
        "records": records,
        "truncated": truncated,
        "candidate_count": len(candidates),
        "included_count": len(records),
    }


def empty_fulltext_selection(
    *,
    status: str = "skipped",
    mode: str | None = None,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an empty fulltext selection payload for context and audit."""

    return {
        "status": status,
        "mode": mode,
        "candidate_count": 0,
        "selected_count": 0,
        "requested_item_ids": [],
        "selected_item_ids": [],
        "invalid_item_ids": [],
        "overflow_item_ids": [],
        "read_item_ids": [],
        "skipped_items": [],
        "budget": budget or {},
        "truncated": False,
    }


def _context_item(
    payload: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_chars: int,
) -> dict[str, Any]:
    text, source = _fulltext_source(payload)
    excerpt, truncated = _truncate(text, max_chars)
    validated = payload.get("validated") if isinstance(payload.get("validated"), dict) else {}
    clean = _without_body(payload.get("clean"))
    relevant = _without_body(payload.get("relevant"))
    return {
        "memory_item_id": str(payload.get("memory_item_id") or candidate["memory_item_id"]),
        "topic": payload.get("topic") or candidate.get("topic"),
        "title": validated.get("title") or candidate.get("title"),
        "source": validated.get("source") or candidate.get("source"),
        "url": validated.get("url") or candidate.get("url"),
        "published_at": validated.get("published_at") or candidate.get("published_at"),
        "path": candidate.get("memory_item_path"),
        "run_id": payload.get("run_id"),
        "run_date": payload.get("run_date"),
        "validated": validated,
        "clean": clean,
        "relevant": relevant,
        "metadata": dict(payload.get("metadata") or {}),
        "source_artifact_paths": dict(payload.get("source_artifact_paths") or {}),
        "content_source": source,
        "content_excerpt": excerpt,
        "content_original_chars": len(text),
        "content_chars": len(excerpt),
        "truncated": truncated,
    }


def _fulltext_source(payload: dict[str, Any]) -> tuple[str, str]:
    for section_name in ("relevant", "clean"):
        section = payload.get(section_name)
        if isinstance(section, dict):
            content = section.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip(), f"{section_name}.content"
    validated = payload.get("validated")
    if isinstance(validated, dict):
        for field in ("summary", "title"):
            value = validated.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip(), f"validated.{field}"
    return "", "none"


def _without_body(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    payload = dict(value)
    payload.pop("content", None)
    return payload


def _truncate(value: str, max_chars: int) -> tuple[str, bool]:
    if max_chars == 0:
        return "", bool(value)
    if len(value) <= max_chars:
        return value, False
    suffix = "\n[truncated]"
    keep = max(0, max_chars - len(suffix))
    return f"{value[:keep]}{suffix}", True


def _resolve_memory_item_path(
    raw_path: Any,
    *,
    memory_item_id: str,
    memory_path: Path,
    items_dir: Path,
) -> tuple[Path | None, str | None]:
    paths: list[Path] = []
    if raw_path not in (None, ""):
        candidate = Path(str(raw_path))
        if candidate.is_absolute():
            paths.append(candidate)
        else:
            paths.append(memory_path.parent / candidate)
            paths.append(Path.cwd() / candidate)
    paths.append(items_dir / f"{memory_item_id}.json")

    safe_paths: list[Path] = []
    for path in _dedupe_paths(paths):
        resolved = path.resolve(strict=False)
        if _is_relative_to(resolved, items_dir):
            safe_paths.append(resolved)

    if not safe_paths:
        return None, "unsafe_path"

    for path in safe_paths:
        if path.exists():
            return path, None
    return safe_paths[0], None


def _memory_item_id(entry: dict[str, Any]) -> str:
    value = entry.get("memory_item_id") or entry.get("id")
    return str(value or "").strip()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


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
