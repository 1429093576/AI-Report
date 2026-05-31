"""schema 对象的 JSON 读写工具。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypeVar

from .base import SchemaBase


ModelT = TypeVar("ModelT", bound=SchemaBase)


def load_json_model(path: str | Path, model_type: type[ModelT]) -> ModelT:
    """从 JSON 文件读取数据，并校验为指定 schema 模型。"""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return model_type.model_validate(payload)


def dump_json_model(path: str | Path, model: SchemaBase) -> None:
    """将单个 schema 模型写出为格式化 JSON 文件。"""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        model.model_dump_json(indent=2),
        encoding="utf-8",
    )


def export_json_schema(path: str | Path, model_type: type[SchemaBase]) -> None:
    """导出 Pydantic JSON Schema，供 Prompt 或结构化输出配置复用。"""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(model_type.model_json_schema(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
