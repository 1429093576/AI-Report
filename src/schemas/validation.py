"""Validator 与 Harness 共享的校验结果 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, computed_field

from .base import NonEmptyStr, SchemaBase, StrippedStr
from .enums import ValidationSeverity


class ValidationIssue(SchemaBase):
    """单个校验问题。

    `item_id` 与 `field` 都允许为空字符串，用于表达全局问题或无法定位到
    具体字段的问题。
    """

    severity: ValidationSeverity
    message: NonEmptyStr
    item_id: StrippedStr = ""
    field: StrippedStr = ""
    code: StrippedStr = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(SchemaBase):
    """一次校验运行的汇总结果。

    Validator 可以用 `is_valid` 判断数据是否允许进入分析阶段；
    Harness 可以用 `issue_count` 记录本次运行的质量指标。
    """

    run_id: NonEmptyStr
    checked_at: datetime
    total_items: int = Field(ge=0)
    valid_items: int = Field(ge=0)
    issues: list[ValidationIssue] = Field(default_factory=list)

    @computed_field
    @property
    def issue_count(self) -> int:
        """校验问题总数。"""

        return len(self.issues)

    @computed_field
    @property
    def error_count(self) -> int:
        """严重程度为 error 的问题数量。"""

        return sum(
            1 for issue in self.issues if issue.severity == ValidationSeverity.ERROR
        )

    @computed_field
    @property
    def is_valid(self) -> bool:
        """是否通过校验，可作为进入下游流程的开关。"""

        return self.error_count == 0 and self.valid_items == self.total_items
