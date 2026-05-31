"""校验结果 schema 的测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from pydantic import ValidationError

from src.schemas import ValidationIssue, ValidationResult, ValidationSeverity


class ValidationSchemaTests(unittest.TestCase):
    def test_validation_issue_parses_severity_and_strips_strings(self) -> None:
        issue = ValidationIssue(
            severity="warning",
            message="  evidence is empty  ",
            item_id=" item-1 ",
            field=" evidence ",
            code=" empty_evidence ",
        )

        self.assertEqual(issue.severity, ValidationSeverity.WARNING)
        self.assertEqual(issue.message, "evidence is empty")
        self.assertEqual(issue.item_id, "item-1")
        self.assertEqual(issue.field, "evidence")
        self.assertEqual(issue.code, "empty_evidence")

    def test_validation_result_computes_counts_and_valid_state(self) -> None:
        result = ValidationResult(
            run_id="run-1",
            checked_at="2026-05-27T10:00:00+08:00",
            total_items=2,
            valid_items=2,
            issues=[
                ValidationIssue(
                    severity="warning",
                    message="Evidence is short",
                    item_id="item-1",
                    field="evidence",
                )
            ],
        )

        self.assertIsInstance(result.checked_at, datetime)
        self.assertEqual(result.issue_count, 1)
        self.assertEqual(result.error_count, 0)
        self.assertTrue(result.is_valid)

    def test_validation_result_with_error_is_not_valid(self) -> None:
        result = ValidationResult(
            run_id="run-1",
            checked_at="2026-05-27T10:00:00+08:00",
            total_items=1,
            valid_items=0,
            issues=[
                ValidationIssue(
                    severity="error",
                    message="Missing title",
                    item_id="item-1",
                    field="title",
                )
            ],
        )

        self.assertEqual(result.error_count, 1)
        self.assertFalse(result.is_valid)

    def test_validation_result_rejects_negative_counts(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationResult(
                run_id="run-1",
                checked_at="2026-05-27T10:00:00+08:00",
                total_items=-1,
                valid_items=0,
            )

    def test_validation_issue_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationIssue(
                severity="info",
                message="ok",
                unexpected="field",
            )


if __name__ == "__main__":
    unittest.main()
