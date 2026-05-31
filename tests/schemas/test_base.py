"""共享 schema 基础定义的测试。"""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from src.schemas import NonEmptyStr, SchemaBase, Score0To100, SourceType


class ExampleSchema(SchemaBase):
    """用于验证共享 schema 行为的小型模型。"""

    title: NonEmptyStr
    score: Score0To100
    source_type: SourceType


class SchemaBaseTests(unittest.TestCase):
    def test_schema_base_strips_strings_and_accepts_enum_values(self) -> None:
        item = ExampleSchema(title="  AI model launch  ", score=80, source_type="news")

        self.assertEqual(item.title, "AI model launch")
        self.assertEqual(item.source_type, SourceType.NEWS)

    def test_schema_base_rejects_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ExampleSchema(
                title="AI model launch",
                score=80,
                source_type="news",
                unexpected="field",
            )

    def test_score_must_be_between_zero_and_one_hundred(self) -> None:
        with self.assertRaises(ValidationError):
            ExampleSchema(title="AI model launch", score=101, source_type="news")


if __name__ == "__main__":
    unittest.main()
