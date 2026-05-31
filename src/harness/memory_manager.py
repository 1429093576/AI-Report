"""Topic-indexed local memory manager."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.schemas import CleanNewsItem, StructuredNewsItem
from src.schemas.topics import coerce_topic_key, normalize_topic_label

MEMORY_ITEM_SCHEMA_VERSION = 1
TOPIC_INDEX_SCHEMA_VERSION = 2


class MemoryManager:
    """Manage a local topic-indexed memory file."""

    def __init__(
        self,
        path: str | Path = "memory/topic_index.json",
        *,
        items_dir: str | Path | None = None,
    ) -> None:
        self.path = Path(path)
        self.items_dir = Path(items_dir) if items_dir is not None else self.path.parent / "items"

    def retrieve(
        self,
        topic: str,
        window_days: int = 7,
        limit: int = 5,
        exclude_keys: set[str] | None = None,
    ) -> str:
        """Retrieve historical context for a topic as plain text."""

        entries = self.retrieve_entries(
            topic,
            window_days=window_days,
            limit=limit,
            exclude_keys=exclude_keys,
        )
        return self._format_context(self._normalize_topic(topic), entries)

    def retrieve_entries(
        self,
        topic: str,
        window_days: int = 7,
        limit: int = 5,
        exclude_keys: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve historical context entries for a topic."""

        normalized_topic = self._normalize_topic(topic)
        if not normalized_topic:
            return []
        if window_days < 0:
            raise ValueError("window_days must be greater than or equal to 0")
        if limit < 1:
            raise ValueError("limit must be greater than 0")

        data = self._load()
        entries = data["topics"].get(normalized_topic, [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        excluded = exclude_keys or set()
        recent_entries = [
            entry
            for entry in entries
            if self._parse_datetime(entry.get("published_at")) >= cutoff
            and not self._has_excluded_key(entry, excluded)
        ]
        recent_entries.sort(
            key=lambda entry: self._parse_datetime(entry.get("published_at")),
            reverse=True,
        )

        return [
            self._entry_ref(normalized_topic, entry)
            for entry in recent_entries[:limit]
        ]

    def append(
        self,
        items: Iterable[StructuredNewsItem | dict[str, Any]],
        *,
        clean_items: Iterable[CleanNewsItem | dict[str, Any]] | None = None,
        relevant_items: Iterable[CleanNewsItem | dict[str, Any]] | None = None,
        run_id: str | None = None,
        run_date: str | None = None,
        artifact_paths: dict[str, str | Path] | None = None,
    ) -> int:
        """Append validated items to memory and return the number added."""

        data = self._load()
        data["schema_version"] = TOPIC_INDEX_SCHEMA_VERSION
        added = 0
        clean_index = self._source_item_index(clean_items)
        relevant_index = self._source_item_index(relevant_items)
        source_artifact_paths = self._artifact_paths_payload(artifact_paths)

        for raw_item in items:
            item = self._coerce_item(raw_item)
            topic = self._normalize_topic(item.topic)
            memory_item_id = self._memory_item_id(item)
            item_path = self._item_path(memory_item_id)
            clean_item = self._matching_source_item(item, clean_index)
            relevant_item = self._matching_source_item(item, relevant_index)
            entry = self._to_entry(
                item,
                memory_item_id=memory_item_id,
                memory_item_path=self._portable_path(item_path),
                clean_item=clean_item,
                relevant_item=relevant_item,
                run_id=run_id,
                run_date=run_date,
            )
            topic_entries = data["topics"].setdefault(topic, [])

            if self._is_duplicate(entry, topic_entries):
                continue

            self._save_memory_item(
                item_path,
                self._to_memory_item_payload(
                    item,
                    memory_item_id=memory_item_id,
                    clean_item=clean_item,
                    relevant_item=relevant_item,
                    run_id=run_id,
                    run_date=run_date,
                    source_artifact_paths=source_artifact_paths,
                ),
            )
            topic_entries.append(entry)
            topic_entries.sort(
                key=lambda current: self._parse_datetime(current.get("published_at")),
                reverse=True,
            )
            added += 1

        if added:
            self._save(data)

        return added

    def strong_duplicate_matches(
        self,
        items: Iterable[CleanNewsItem | StructuredNewsItem | dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return historical strong-duplicate matches keyed by current item id."""

        data = self._load()
        index = self._strong_key_index(data)
        matches: dict[str, dict[str, Any]] = {}

        for raw_item in items:
            item_keys = self._identity_keys(raw_item)
            item_id = item_keys.get("id", "")
            if not item_id:
                continue

            matched_keys: list[dict[str, str]] = []
            matched_entries: list[dict[str, Any]] = []
            seen_entries: set[str] = set()
            for field, value in item_keys.items():
                for entry_ref in index.get((field, value), []):
                    matched_keys.append({"field": field, "value": value})
                    entry_key = self._entry_identity(entry_ref)
                    if entry_key in seen_entries:
                        continue
                    seen_entries.add(entry_key)
                    matched_entries.append(entry_ref)

            if matched_entries:
                matches[item_id] = {
                    "item_id": item_id,
                    "matched_keys": matched_keys,
                    "memory_entries": matched_entries,
                }

        return matches

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": TOPIC_INDEX_SCHEMA_VERSION, "topics": {}}

        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return {"schema_version": TOPIC_INDEX_SCHEMA_VERSION, "topics": {}}

        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("memory file must contain a JSON object")

        topics = payload.get("topics", payload)
        if not isinstance(topics, dict):
            raise ValueError("memory topics must be a JSON object")

        normalized_topics: dict[str, list[dict[str, Any]]] = {}
        for topic, entries in topics.items():
            normalized_topic = self._normalize_topic(str(topic))
            if not normalized_topic:
                continue
            if not isinstance(entries, list):
                continue
            normalized_entries = [
                self._normalize_entry_topic(normalized_topic, entry)
                for entry in entries
                if isinstance(entry, dict)
            ]
            normalized_topics.setdefault(normalized_topic, []).extend(normalized_entries)

        return {
            "schema_version": int(payload.get("schema_version") or 1),
            "topics": normalized_topics,
        }

    def _strong_key_index(
        self,
        data: dict[str, Any],
    ) -> dict[tuple[str, str], list[dict[str, Any]]]:
        index: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for topic, entry in self._iter_entries(data):
            entry_ref = self._entry_ref(topic, entry)
            for field, value in self._entry_identity_key_pairs(entry):
                index.setdefault((field, value), []).append(entry_ref)
        return index

    @staticmethod
    def _iter_entries(data: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
        topics = data.get("topics", {})
        if not isinstance(topics, dict):
            return []

        entries: list[tuple[str, dict[str, Any]]] = []
        for topic, topic_entries in topics.items():
            if not isinstance(topic_entries, list):
                continue
            for entry in topic_entries:
                if isinstance(entry, dict):
                    entries.append((str(topic), entry))
        return entries

    @staticmethod
    def _entry_ref(topic: str, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_item_id": entry.get("memory_item_id", entry.get("id")),
            "memory_item_path": entry.get("memory_item_path"),
            "topic": topic,
            "id": entry.get("id"),
            "validated_item_id": entry.get("validated_item_id", entry.get("id")),
            "source_item_id": entry.get("source_item_id"),
            "title": entry.get("title"),
            "source": entry.get("source"),
            "url": entry.get("url"),
            "published_at": entry.get("published_at"),
            "content_hash": entry.get("content_hash"),
            "summary": entry.get("summary"),
            "entities": list(entry.get("entities") or []),
            "event_type": entry.get("event_type"),
            "importance_score": entry.get("importance_score"),
            "importance_rationale": entry.get("importance_rationale"),
            "risk_level": entry.get("risk_level"),
            "risk_rationale": entry.get("risk_rationale"),
            "opportunity_level": entry.get("opportunity_level"),
            "opportunity_rationale": entry.get("opportunity_rationale"),
            "metadata": dict(entry.get("metadata") or {}),
        }

    @staticmethod
    def _normalize_entry_topic(topic: str, entry: dict[str, Any]) -> dict[str, Any]:
        current = dict(entry)
        try:
            current["topic"] = normalize_topic_label(current.get("topic") or topic)
        except ValueError:
            current["topic"] = topic
        return current

    @staticmethod
    def _entry_identity(entry: dict[str, Any]) -> str:
        return "|".join(
            str(entry.get(field) or "")
            for field in ("topic", "id", "url", "content_hash")
        )

    @staticmethod
    def _identity_keys(
        item: CleanNewsItem | StructuredNewsItem | dict[str, Any],
    ) -> dict[str, str]:
        keys: dict[str, str] = {}
        for field in ("id", "url", "content_hash"):
            value = item.get(field) if isinstance(item, dict) else getattr(item, field, None)
            if value not in (None, ""):
                keys[field] = str(value)
        return keys

    @staticmethod
    def _entry_identity_key_pairs(entry: dict[str, Any]) -> list[tuple[str, str]]:
        keys: list[tuple[str, str]] = []
        for field in ("id", "url", "content_hash", "memory_item_id", "validated_item_id"):
            value = entry.get(field)
            if value not in (None, ""):
                keys.append((field, str(value)))
        for field in ("source_item_id", "validated_item_id"):
            value = entry.get(field)
            if value not in (None, ""):
                keys.append(("id", str(value)))
        return keys

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _coerce_item(item: StructuredNewsItem | dict[str, Any]) -> StructuredNewsItem:
        if isinstance(item, StructuredNewsItem):
            return item
        return StructuredNewsItem.model_validate(item)

    @staticmethod
    def _to_entry(
        item: StructuredNewsItem,
        *,
        memory_item_id: str,
        memory_item_path: str,
        clean_item: dict[str, Any] | None = None,
        relevant_item: dict[str, Any] | None = None,
        run_id: str | None = None,
        run_date: str | None = None,
    ) -> dict[str, Any]:
        return {
            "memory_item_id": memory_item_id,
            "id": memory_item_id,
            "validated_item_id": item.id,
            "title": item.title,
            "source": item.source,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "topic": item.topic,
            "entities": list(item.entities),
            "event_type": item.event_type.value,
            "summary": item.summary,
            "key_points": list(item.key_points),
            "sentiment": item.sentiment.value,
            "impact_scope": item.impact_scope.value,
            "importance_score": item.importance_score,
            "importance_rationale": item.importance_rationale,
            "risk_level": item.risk_level.value,
            "risk_rationale": item.risk_rationale,
            "opportunity_level": item.opportunity_level.value,
            "opportunity_rationale": item.opportunity_rationale,
            "content_hash": item.content_hash,
            "evidence": list(item.evidence),
            "evidence_sources": [
                source.model_dump(mode="json") for source in item.evidence_sources
            ],
            "source_item_id": MemoryManager._primary_source_item_id(item),
            "metadata": MemoryManager._light_metadata(
                item,
                clean_item=clean_item,
                relevant_item=relevant_item,
                run_id=run_id,
                run_date=run_date,
            ),
            "memory_item_path": memory_item_path,
        }

    @staticmethod
    def _to_memory_item_payload(
        item: StructuredNewsItem,
        *,
        memory_item_id: str,
        clean_item: dict[str, Any] | None,
        relevant_item: dict[str, Any] | None,
        run_id: str | None,
        run_date: str | None,
        source_artifact_paths: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "schema_version": MEMORY_ITEM_SCHEMA_VERSION,
            "memory_item_id": memory_item_id,
            "run_id": run_id,
            "run_date": run_date,
            "created_at": MemoryManager._utc_now_iso(),
            "topic": item.topic,
            "validated": item.model_dump(mode="json"),
            "clean": clean_item,
            "relevant": relevant_item,
            "metadata": MemoryManager._merged_metadata(clean_item, relevant_item),
            "source_metadata": {
                "clean": dict((clean_item or {}).get("metadata") or {}),
                "relevant": dict((relevant_item or {}).get("metadata") or {}),
            },
            "source_artifact_paths": source_artifact_paths,
        }

    @staticmethod
    def _is_duplicate(entry: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
        candidate_keys = {
            value
            for value in (
                entry.get("id"),
                entry.get("memory_item_id"),
                entry.get("validated_item_id"),
                entry.get("source_item_id"),
                entry.get("url"),
                entry.get("content_hash"),
            )
            if value
        }
        for current in entries:
            current_keys = {
                value
                for value in (
                    current.get("id"),
                    current.get("memory_item_id"),
                    current.get("validated_item_id"),
                    current.get("source_item_id"),
                    current.get("url"),
                    current.get("content_hash"),
                )
                if value
            }
            if candidate_keys & current_keys:
                return True
        return False

    @staticmethod
    def _has_excluded_key(entry: dict[str, Any], exclude_keys: set[str]) -> bool:
        if not exclude_keys:
            return False
        entry_keys = {
            str(value)
            for value in (
                entry.get("id"),
                entry.get("memory_item_id"),
                entry.get("validated_item_id"),
                entry.get("url"),
                entry.get("content_hash"),
                entry.get("source_item_id"),
            )
            if value
        }
        return bool(entry_keys & exclude_keys)

    @staticmethod
    def _source_item_index(
        items: Iterable[CleanNewsItem | dict[str, Any]] | None,
    ) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        if items is None:
            return index

        for raw_item in items:
            item = MemoryManager._source_item_payload(raw_item)
            for key in MemoryManager._source_item_keys(item):
                index.setdefault(key, item)
        return index

    @staticmethod
    def _source_item_payload(item: CleanNewsItem | dict[str, Any]) -> dict[str, Any]:
        if isinstance(item, CleanNewsItem):
            return item.model_dump(mode="json")
        return dict(item)

    @staticmethod
    def _source_item_keys(item: dict[str, Any]) -> list[str]:
        keys: list[str] = []
        for field in ("id", "url", "content_hash"):
            value = item.get(field)
            if value not in (None, ""):
                keys.append(str(value))
        return keys

    @staticmethod
    def _matching_source_item(
        item: StructuredNewsItem,
        index: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for key in MemoryManager._structured_source_keys(item):
            if key in index:
                return index[key]
        return None

    @staticmethod
    def _structured_source_keys(item: StructuredNewsItem) -> list[str]:
        keys: list[str] = []
        for source in item.evidence_sources:
            if source.source_item_id:
                keys.append(str(source.source_item_id))
        for value in (item.id, item.url, item.content_hash):
            if value:
                keys.append(str(value))
        if item.id.startswith("structured-"):
            keys.append(item.id.replace("structured-", "raw-", 1))
        return MemoryManager._dedupe_text(keys)

    @staticmethod
    def _primary_source_item_id(item: StructuredNewsItem) -> str | None:
        for source in item.evidence_sources:
            if source.source_item_id:
                return str(source.source_item_id)
        if item.id.startswith("structured-"):
            return item.id.replace("structured-", "raw-", 1)
        return None

    @staticmethod
    def _light_metadata(
        item: StructuredNewsItem,
        *,
        clean_item: dict[str, Any] | None,
        relevant_item: dict[str, Any] | None,
        run_id: str | None,
        run_date: str | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "run_id": run_id,
            "run_date": run_date,
            "source_type": item.source_type.value,
            "language": item.language.value,
        }
        source_metadata = dict((relevant_item or clean_item or {}).get("metadata") or {})
        for key in (
            "source_name",
            "source_family",
            "content_source",
            "fetched_via",
            "canonical_url",
            "full_content_url",
            "content_chars",
            "content_truncated",
        ):
            if key in source_metadata:
                metadata[key] = source_metadata[key]
        return {key: value for key, value in metadata.items() if value is not None}

    @staticmethod
    def _merged_metadata(
        clean_item: dict[str, Any] | None,
        relevant_item: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {}
        for item in (clean_item, relevant_item):
            current = dict((item or {}).get("metadata") or {})
            metadata.update(current)
        return metadata

    @staticmethod
    def _artifact_paths_payload(
        artifact_paths: dict[str, str | Path] | None,
    ) -> dict[str, str]:
        if not artifact_paths:
            return {}
        return {
            str(name): Path(path).as_posix()
            for name, path in sorted(artifact_paths.items())
        }

    def _save_memory_item(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _item_path(self, memory_item_id: str) -> Path:
        return self.items_dir / f"{memory_item_id}.json"

    @staticmethod
    def _memory_item_id(item: StructuredNewsItem) -> str:
        item_id = item.id.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", item_id):
            return item_id

        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", item_id).strip(".-")
        if not base or base in {".", ".."}:
            base = "memory-item"
        digest = sha256(item_id.encode("utf-8")).hexdigest()[:12]
        return f"{base[:80]}-{digest}"

    @staticmethod
    def _portable_path(path: Path) -> str:
        try:
            return path.relative_to(Path.cwd()).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _dedupe_text(values: Iterable[str]) -> list[str]:
        seen: set[str] = set()
        unique: list[str] = []
        for value in values:
            text = str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            unique.append(text)
        return unique

    @staticmethod
    def _format_context(topic: str, entries: list[dict[str, Any]]) -> str:
        if not entries:
            return ""

        lines = [f"Historical context for topic: {topic}"]
        for index, entry in enumerate(entries, start=1):
            published_at = entry.get("published_at", "")
            title = entry.get("title", "")
            source = entry.get("source", "")
            summary = entry.get("summary", "")
            score = entry.get("importance_score", "")
            rationale = entry.get("importance_rationale", "")
            lines.append(
                f"{index}. [{published_at}] {title} ({source}) "
                f"attention={score}: {summary}"
                f"{f' 判断依据：{rationale}' if rationale else ''}"
            )
        return "\n".join(lines)

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        return coerce_topic_key(topic)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value.strip():
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return datetime.min.replace(tzinfo=timezone.utc)

        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
