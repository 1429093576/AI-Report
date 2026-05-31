"""所有 schema 模型共享的基础定义。

这个模块刻意保持精简：具体业务字段放在各自模型文件里，公共的
Pydantic 行为和通用约束集中放在这里。
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


# 当持久化 JSON 合同发生不兼容变更时更新此版本号。
# 后续 Pipeline 文件可以记录该值，让数据迁移边界更明确。
SCHEMA_VERSION = "0.1.0"


# 通用约束类型用于避免在业务模型里重复书写字段规则。
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
StrippedStr = Annotated[str, StringConstraints(strip_whitespace=True)]
Score0To100 = Annotated[int, Field(ge=0, le=100)]


class SchemaBase(BaseModel):
    """项目内所有 schema 的共同基类。

    这里刻意采用偏严格的配置，让未知字段在校验阶段尽早失败，
    避免静默流入后续 Pipeline。
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
    )
