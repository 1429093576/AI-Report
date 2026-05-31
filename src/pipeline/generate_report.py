"""Report generation pipeline step."""

from __future__ import annotations

import re
from pathlib import Path

from src.harness import PipelineContext
from src.schemas import DailyInsightReport, EvidenceSource, StructuredNewsItem

from .utils import path_for, read_json, require_json_list


def run(context: PipelineContext) -> str:
    """Generate the final Markdown daily report."""

    report = _report(context)
    items = _validated_items(context)
    output_path = path_for(context, "daily_report")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    markdown = _render_markdown(
        report,
        items,
        context.historical_context,
        output_path=output_path,
    )
    output_path.write_text(markdown, encoding="utf-8")

    context.add_artifact("daily_report", output_path)
    context.set("daily_report_path", output_path)
    return markdown


def _report(context: PipelineContext) -> DailyInsightReport:
    report = context.get("report_sections")
    if isinstance(report, DailyInsightReport):
        return report
    payload = read_json(path_for(context, "report_sections"))
    return DailyInsightReport.model_validate(payload)


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


def _render_markdown(
    report: DailyInsightReport,
    items: list[StructuredNewsItem],
    historical_context: str,
    *,
    output_path: Path,
) -> str:
    reference_items = _reference_items(items)
    reference_index = _reference_index(reference_items)
    lines: list[str] = [
        f"# {report.title}",
        "",
        f"报告日期：{report.report_date.isoformat()}",
        "",
        "## 执行摘要",
        "",
        _display_text(report.executive_summary),
        "",
        "## 重点事件",
        "",
    ]

    for index, event in enumerate(report.top_events, start=1):
        citation = _citation_suffix(event.evidence_sources, reference_index)
        lines.extend(
            [
                f"### {index}. {event.title}",
                "",
                f"- 来源：{event.source}",
                f"- 关注度：{event.importance_score}",
                f"- 入选原因：{_with_citation(_display_text(event.reason), citation)}",
                f"- 影响判断：{_with_citation(_display_text(event.impact), citation)}",
            ]
        )
        lines.append("")

    lines.extend(["## 深度分析", ""])
    for section in report.deep_dives:
        item = _find_item(items, section.item_id)
        citation = _citation_suffix(section.evidence_sources, reference_index)
        lines.extend([f"### {item.title if item else section.item_id}", ""])
        lines.append(
            _with_citation(_display_text(section.narrative_analysis), citation)
        )
        if section.historical_context_note:
            lines.extend(
                [
                    "",
                    f"- 历史脉络：{_display_text(section.historical_context_note)}",
                ]
            )
        lines.extend(
            [
                "",
                f"- 相关实体：{', '.join(section.involved_entities)}",
                f"- 后续验证：{_render_follow_up_actions(section.follow_up_questions)}",
            ]
        )
        lines.append("")

    if report.historical_comparisons or _has_memory_usage(report):
        lines.extend(["## 历史对照", ""])
        usage = report.memory_usage
        lines.extend(
            [
                "### Memory 使用概览",
                "",
                f"- relevance 后候选：{usage.relevant_candidate_count} 条",
                f"- 历史强重复过滤：{usage.strong_duplicate_filtered_count} 条",
                f"- 检索到同主题历史摘要：{usage.retrieved_metadata_count} 条",
                f"- 读取历史全文：{usage.read_fulltext_count} 条",
                f"- 最终采纳为历史依据：{usage.adopted_historical_evidence_count} 条",
                "",
            ]
        )
        if report.historical_comparisons:
            lines.extend(["### 事件对照", ""])
            for comparison in report.historical_comparisons:
                lines.extend(
                    [
                        f"- **今日事件**：{_display_text(comparison.current_event_title)}",
                        f"  **关联历史事件**：{_display_text(comparison.historical_event_title)}"
                        f"（{_display_text(comparison.historical_event_date)}）",
                        (
                            "  "
                            f"关系：{_display_text(comparison.relation_type)}；"
                            f"相关强度：{comparison.relevance_strength}/100"
                        ),
                        f"  为什么相关：{_display_text(comparison.rationale)}",
                        f"  对今日判断的影响：{_display_text(comparison.impact_on_today)}",
                    ]
                )
            lines.append("")

    lines.extend(["## 趋势洞察", ""])
    for insight in report.trend_insights:
        history_note = "是" if insight.historical_context_used else "否"
        citation = _citation_suffix(insight.evidence_sources, reference_index)
        lines.extend(
            [
                (
                    f"- **{insight.title}**"
                    f"（范围：{_impact_scope_label(insight.scope.value)}，"
                    f"趋势：{_trend_state_label(insight.trend_state.value)}，"
                    f"使用历史：{history_note}）"
                ),
                f"  {_with_citation(_display_text(insight.summary), citation)}",
            ]
        )
        if insight.historical_evidence:
            evidence_text = "；".join(
                f"{evidence.title}（{evidence.published_at}）：{evidence.reason}"
                for evidence in insight.historical_evidence
            )
            lines.append(f"  历史依据：{_display_text(evidence_text)}")
    lines.append("")

    lines.extend(["## 风险与机会", ""])
    lines.append("### 风险")
    for risk in report.risk_insights:
        citation = _citation_suffix(risk.evidence_sources, reference_index)
        lines.append(
            f"- **{_display_text(risk.title)}** "
            f"[{_level_label(risk.level.value)}] "
            f"{_with_citation(_display_text(risk.summary), citation)}"
        )
    lines.extend(["", "### 机会"])
    for opportunity in report.opportunity_insights:
        citation = _citation_suffix(opportunity.evidence_sources, reference_index)
        lines.append(
            f"- **{_display_text(opportunity.title)}** "
            f"[{_level_label(opportunity.level.value)}] "
            f"{_with_citation(_display_text(opportunity.summary), citation)}"
        )
    lines.append("")

    lines.extend(["## 图表", ""])
    for ref in report.chart_refs:
        alt = _chart_alt_text(ref)
        display_ref = _display_chart_ref(ref, output_path)
        lines.append(f"![{alt}]({display_ref})")
    lines.append("")

    lines.extend(["## 结构化新闻判断说明表", ""])
    lines.append(
        "| 序号 | 标题 | 来源 | 主题 | 事件 | 关注度判断 | 风险提示 | 机会提示 | URL |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for index, item in enumerate(reference_items, start=1):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    _escape_table(item.title),
                    _escape_table(item.source),
                    _escape_table(item.topic),
                    item.event_type.value,
                    _escape_table(_importance_judgment(item)),
                    _escape_table(_risk_judgment(item)),
                    _escape_table(_opportunity_judgment(item)),
                    f"[link]({item.url})",
                ]
            )
            + " |"
    )
    lines.append("")

    lines.extend(["## 参考依据", ""])
    for index, item in enumerate(reference_items, start=1):
        lines.append(f"[{index}] {item.source}: [{item.title}]({item.url})")
    lines.append("")

    if historical_context and not report.historical_comparisons:
        lines.extend(
            [
                "## 已使用历史上下文",
                "",
                "分析器已读取按主题索引的历史记忆作为趋势判断参考。",
                "",
            ]
        )

    return "\n".join(lines)


def _find_item(
    items: list[StructuredNewsItem],
    item_id: str,
) -> StructuredNewsItem | None:
    return next((item for item in items if item.id == item_id), None)


def _has_memory_usage(report: DailyInsightReport) -> bool:
    usage = report.memory_usage
    return any(
        [
            usage.relevant_candidate_count,
            usage.strong_duplicate_filtered_count,
            usage.retrieved_metadata_count,
            usage.read_fulltext_count,
            usage.adopted_historical_evidence_count,
        ]
    )


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|")


def _reference_items(items: list[StructuredNewsItem]) -> list[StructuredNewsItem]:
    return sorted(items, key=lambda value: value.importance_score, reverse=True)


def _reference_index(items: list[StructuredNewsItem]) -> dict[str, int]:
    index: dict[str, int] = {}
    for number, item in enumerate(items, start=1):
        index[item.id] = number
        index[item.id.replace("structured-", "raw-", 1)] = number
        for source in item.evidence_sources:
            index[source.source_item_id] = number
    return index


def _citation_suffix(
    evidence_sources: list[EvidenceSource],
    reference_index: dict[str, int],
) -> str:
    numbers: list[int] = []
    seen: set[int] = set()
    for evidence in evidence_sources:
        number = reference_index.get(evidence.source_item_id)
        if number is None or number in seen:
            continue
        numbers.append(number)
        seen.add(number)
    if not numbers:
        return ""
    return " [" + ", ".join(str(number) for number in numbers) + "]"


def _with_citation(value: str, citation: str) -> str:
    text = value.strip()
    if not citation or text.endswith(citation):
        return text
    return f"{text}{citation.lstrip()}"


def _render_follow_up_actions(values: list[str]) -> str:
    actions = [_display_text(value).rstrip("？?。.") for value in values if str(value).strip()]
    if not actions:
        return "短期内跟踪关键进展、第三方验证和实际采用信号。"
    return "；".join(_actionize_follow_up(value) for value in actions)


def _actionize_follow_up(value: str) -> str:
    stripped = value.strip()
    if _looks_actionable_follow_up(stripped):
        return stripped
    text = stripped
    patterns = [
        (r"^(.+?)是否验证了(.+)$", "跟踪{left}对{right}的验证结果"),
        (r"^(.+?)是否已达(.+)$", "核验{left}达到{right}的进展"),
        (r"^(.+?)是否满足(.+)$", "核验{left}满足{right}的证据"),
        (r"^(.+?)是否会(.+)$", "观察{left}{right}的实际进展"),
        (r"^(.+?)是什么$", "关注{left}的具体披露"),
        (r"^(.+?)的进展如何$", "跟踪{left}的进展"),
        (r"^(.+?)的影响如何$", "评估{left}的影响"),
        (r"^(.+?)如何$", "评估{left}的后续进展"),
    ]
    for pattern, template in patterns:
        match = re.match(pattern, text)
        if match:
            left = _clean_follow_up_fragment(match.group(1))
            right = _clean_follow_up_fragment(match.group(2) if len(match.groups()) > 1 else "")
            return _polish_follow_up_action(
                template.format(left=left, right=right).strip()
            )

    if text:
        return _polish_follow_up_action(f"关注{_clean_follow_up_fragment(text)}")
    return "短期内跟踪关键进展、第三方验证和实际采用信号"


def _clean_follow_up_fragment(value: str) -> str:
    text = value.strip(" ；，,。")
    return re.sub(r"\s+", " ", text)


def _polish_follow_up_action(value: str) -> str:
    return re.sub(r"([A-Za-z0-9])的", r"\1 的", value)


def _looks_actionable_follow_up(value: str) -> bool:
    return value.startswith(
        (
            "关注",
            "观察",
            "跟踪",
            "验证",
            "评估",
            "监测",
            "复核",
            "短期内",
            "中期",
            "需关注",
            "需要关注",
        )
    )


def _display_text(value: str) -> str:
    text = str(value)
    text = _replace_level_phrase(text, "opportunity_level", "机会等级")
    text = _replace_level_phrase(text, "risk_level", "风险等级")
    text = _replace_level_phrase(text, "机会等级", "机会等级")
    text = _replace_level_phrase(text, "风险等级", "风险等级")
    text = re.sub(
        r"\bimportance_score\s*[=:]\s*(\d+)",
        r"关注度评分 \1",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("heating_up", "升温")
    text = text.replace("cooling_down", "降温")
    text = text.replace("continuing", "延续")
    text = text.replace("reversing", "反转")
    return text


def _replace_level_phrase(value: str, field: str, label: str) -> str:
    pattern = rf"\b{re.escape(field)}\s*(?:[:=]|为|是)?\s*(high|medium|low|unknown)\b"
    return re.sub(
        pattern,
        lambda match: f"{label}{_level_label(match.group(1).lower())}",
        value,
        flags=re.IGNORECASE,
    )


def _display_chart_ref(ref: str, output_path: Path) -> str:
    chart_path = Path(ref)
    if chart_path.is_absolute():
        try:
            return chart_path.relative_to(output_path.parent).as_posix()
        except ValueError:
            return chart_path.as_posix()
    if len(chart_path.parts) >= 2 and chart_path.parts[0] == "outputs":
        candidate = Path(*chart_path.parts[1:])
        if len(candidate.parts) >= 1 and candidate.parts[0] == "charts":
            return candidate.as_posix()
    return chart_path.as_posix()


def _chart_alt_text(ref: str) -> str:
    name = Path(ref).name
    labels = {
        "topic_distribution.png": "主题分布",
        "importance_ranking.png": "关注度排行",
    }
    return labels.get(name, name.replace("_", " ").replace(".png", ""))


def _level_label(value: str) -> str:
    return {
        "low": "低",
        "medium": "中",
        "high": "高",
        "unknown": "未知",
    }.get(value, value)


def _importance_judgment(item: StructuredNewsItem) -> str:
    return f"{item.importance_score}：{item.importance_rationale}"


def _risk_judgment(item: StructuredNewsItem) -> str:
    return f"{_level_label(item.risk_level.value)}：{item.risk_rationale}"


def _opportunity_judgment(item: StructuredNewsItem) -> str:
    return f"{_level_label(item.opportunity_level.value)}：{item.opportunity_rationale}"


def _trend_state_label(value: str) -> str:
    return {
        "new": "新信号",
        "continuing": "延续",
        "heating_up": "升温",
        "cooling_down": "回落",
        "reversing": "反转",
    }.get(value, value)


def _impact_scope_label(value: str) -> str:
    return {
        "technology": "技术",
        "industry": "产业",
        "capital": "资本",
        "policy": "政策",
        "user": "用户",
        "ecosystem": "生态",
        "security": "安全",
        "other": "其他",
    }.get(value, value)
