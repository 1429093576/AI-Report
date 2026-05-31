"""Harness validation tests."""

from __future__ import annotations

import unittest

from src.harness import validate_output
from src.schemas import RawNewsItem, ValidationResult


class HarnessValidationTests(unittest.TestCase):
    def test_validate_output_accepts_valid_payloads(self) -> None:
        items, result = validate_output(
            [
                self._raw_payload("raw-1"),
                self._raw_payload("raw-2"),
            ],
            RawNewsItem,
            run_id="run-test",
        )

        self.assertEqual(len(items), 2)
        self.assertIsInstance(items[0], RawNewsItem)
        self.assertIsInstance(result, ValidationResult)
        self.assertEqual(result.run_id, "run-test")
        self.assertEqual(result.total_items, 2)
        self.assertEqual(result.valid_items, 2)
        self.assertEqual(result.issue_count, 0)
        self.assertTrue(result.is_valid)

    def test_validate_output_collects_validation_issues(self) -> None:
        invalid = self._raw_payload("raw-2")
        invalid["title"] = ""
        invalid["unexpected"] = "field"

        items, result = validate_output(
            [
                self._raw_payload("raw-1"),
                invalid,
            ],
            RawNewsItem,
            run_id="run-test",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(result.total_items, 2)
        self.assertEqual(result.valid_items, 1)
        self.assertFalse(result.is_valid)
        self.assertGreaterEqual(result.error_count, 2)

        fields = {issue.field for issue in result.issues}
        self.assertIn("title", fields)
        self.assertIn("unexpected", fields)
        self.assertTrue(all(issue.item_id == "raw-2" for issue in result.issues))
        self.assertTrue(all(issue.details["index"] == 1 for issue in result.issues))

    def test_validate_output_accepts_single_dict_payload(self) -> None:
        items, result = validate_output(
            self._raw_payload("raw-1"),
            RawNewsItem,
            run_id="run-test",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(result.total_items, 1)
        self.assertTrue(result.is_valid)

    def test_validate_output_accepts_existing_model_instance(self) -> None:
        item = RawNewsItem(**self._raw_payload("raw-1"))

        items, result = validate_output(item, RawNewsItem, run_id="run-test")

        self.assertEqual(items, [item])
        self.assertTrue(result.is_valid)

    def test_validate_output_rejects_non_schema_model_type(self) -> None:
        with self.assertRaises(TypeError):
            validate_output([], dict, run_id="run-test")  # type: ignore[type-var]

    def test_validate_output_rejects_string_payload(self) -> None:
        with self.assertRaises(TypeError):
            validate_output("bad", RawNewsItem, run_id="run-test")  # type: ignore[arg-type]

    def test_validate_output_reports_invalid_item_type(self) -> None:
        items, result = validate_output(
            [self._raw_payload("raw-1"), 123],
            RawNewsItem,
            run_id="run-test",
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(result.total_items, 2)
        self.assertEqual(result.valid_items, 1)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.issues[0].item_id, "")
        self.assertEqual(result.issues[0].details["index"], 1)

    def _raw_payload(self, item_id: str) -> dict[str, object]:
        return {
            "id": item_id,
            "title": "OpenAI releases a new model",
            "source": "OpenAI Blog",
            "url": "https://example.com/news",
            "published_at": "2026-05-27T10:00:00+08:00",
            "source_type": "blog",
            "language": "en",
            "summary": "Short summary",
        }


if __name__ == "__main__":
    unittest.main()
