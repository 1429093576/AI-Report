"""schema JSON 读写工具的测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.schemas import (
    RawNewsItem,
    dump_json_model,
    export_json_schema,
    load_json_model,
)


class SchemaIoTests(unittest.TestCase):
    def test_dump_and_load_json_model_round_trip(self) -> None:
        item = RawNewsItem(
            id="raw-1",
            title="OpenAI releases a new model",
            source="OpenAI Blog",
            url="https://example.com/news",
            published_at="2026-05-27T10:00:00+08:00",
            source_type="blog",
            language="en",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "nested" / "raw_news.json"

            dump_json_model(path, item)
            loaded = load_json_model(path, RawNewsItem)

        self.assertEqual(loaded.id, item.id)
        self.assertEqual(loaded.title, item.title)
        self.assertEqual(loaded.source_type, item.source_type)

    def test_export_json_schema_writes_model_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "schemas" / "raw_news.schema.json"

            export_json_schema(path, RawNewsItem)
            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["title"], "RawNewsItem")
        self.assertIn("properties", payload)
        self.assertIn("id", payload["properties"])


if __name__ == "__main__":
    unittest.main()
