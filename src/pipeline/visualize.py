"""Visualization pipeline step."""

from __future__ import annotations

from collections import Counter
import os
from pathlib import Path
import textwrap

os.environ.setdefault("MPLCONFIGDIR", str(Path("outputs/.matplotlib").resolve()))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

from src.harness import PipelineContext
from src.schemas import StructuredNewsItem

from .utils import path_for, require_json_list


def run(context: PipelineContext) -> list[str]:
    """Generate charts from validated structured data."""

    _configure_fonts()
    items = _validated_items(context)
    if not items:
        raise ValueError("visualization requires at least one validated item")

    charts_dir = path_for(context, "charts_dir")
    charts_dir.mkdir(parents=True, exist_ok=True)

    topic_path = charts_dir / "topic_distribution.png"
    importance_path = charts_dir / "importance_ranking.png"
    _plot_topic_distribution(items, topic_path)
    _plot_importance_ranking(items, importance_path)

    refs = [topic_path.as_posix(), importance_path.as_posix()]
    context.set("chart_refs", refs)
    context.add_artifact("topic_distribution_chart", topic_path)
    context.add_artifact("importance_ranking_chart", importance_path)
    return refs


def _validated_items(context: PipelineContext) -> list[StructuredNewsItem]:
    items = context.get("validated_items")
    if items is None:
        items = require_json_list(path_for(context, "validated"))
    return [
        item
        if isinstance(item, StructuredNewsItem)
        else StructuredNewsItem.model_validate(item)
        for item in items
    ]


def _plot_topic_distribution(items: list[StructuredNewsItem], path: Path) -> None:
    counts = Counter(item.topic for item in items)
    labels = [_topic_label(label) for label in counts.keys()]
    values = list(counts.values())

    plt.figure(figsize=(9.5, 5.5))
    bars = plt.bar(labels, values, color="#2f6f73")
    plt.title("AI 新闻主题分布")
    plt.ylabel("条目数")
    plt.xticks(rotation=25, ha="right")
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, value, str(value), ha="center", va="bottom")
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def _plot_importance_ranking(items: list[StructuredNewsItem], path: Path) -> None:
    ranked = sorted(items, key=lambda item: item.importance_score)[-10:]
    labels = [_ranking_label(index, item) for index, item in enumerate(ranked, start=1)]
    values = [item.importance_score for item in ranked]

    plt.figure(figsize=(10.5, 6.5))
    bars = plt.barh(labels, values, color="#8a5a44")
    plt.title("AI 新闻关注度排行")
    plt.xlabel("关注度评分")
    plt.xlim(0, 100)
    for bar, value in zip(bars, values):
        plt.text(value + 1, bar.get_y() + bar.get_height() / 2, str(value), va="center")
    plt.tight_layout()
    plt.savefig(path, dpi=160)
    plt.close()


def _short_label(title: str, max_len: int = 58) -> str:
    return title if len(title) <= max_len else f"{title[: max_len - 1]}..."


def _configure_fonts() -> None:
    available = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in (
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Arial Unicode MS",
        "DejaVu Sans",
    ):
        if font_name in available:
            matplotlib.rcParams["font.family"] = [font_name]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


def _topic_label(topic: str) -> str:
    labels = {
        "AI Safety and Governance": "AI 安全与治理",
        "AI Research": "AI 研究",
        "AI Agents": "AI 智能体",
        "Foundation Models": "基础模型",
        "Developer Tools and Open Source": "开发者工具与开源",
        "AI Infrastructure": "AI 基础设施",
        "AI Applications": "AI 应用",
        "AI Business and Market": "AI 商业与市场",
    }
    return labels.get(topic, topic)


def _ranking_label(index: int, item: StructuredNewsItem) -> str:
    label = f"{index}. {item.source}｜{_short_label(item.title, 28)}"
    return "\n".join(textwrap.wrap(label, width=34, break_long_words=False))
