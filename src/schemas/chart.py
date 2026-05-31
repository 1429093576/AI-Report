"""可视化模块使用的图表描述 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import NonEmptyStr, SchemaBase, StrippedStr
from .enums import ChartType


class ChartDataPoint(SchemaBase):
    """单个图表数据点。

    `value` 使用浮点数，方便同时表达计数、占比和评分。
    """

    label: NonEmptyStr
    value: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChartSpec(SchemaBase):
    """可视化模块的图表生成合同。

    该模型只描述“画什么”，不绑定 Matplotlib、Plotly 或其他具体渲染库。
    """

    id: NonEmptyStr
    title: NonEmptyStr
    chart_type: ChartType
    data: list[ChartDataPoint] = Field(min_length=1)
    output_path: NonEmptyStr
    x_label: StrippedStr = ""
    y_label: StrippedStr = ""
    description: StrippedStr = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
