"""Daily analysis pipeline step."""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from src.harness import PipelineContext, SkillRunner, SkillSpec, filter_report_by_supported_evidence
from src.schemas import (
    CleanNewsItem,
    DailyInsightReport,
    DeepDiveSection,
    HistoricalComparison,
    HistoricalEvidenceReference,
    MemoryUsageSummary,
    OpportunityInsight,
    RiskInsight,
    StructuredNewsItem,
    TopEvent,
    TrendInsight,
    TrendState,
)

from .utils import (
    active_llm_adapter,
    LLMBusinessError,
    llm_call_with_business_retries,
    load_prompt_template,
    model_list_payload,
    parse_llm_json,
    path_for,
    read_json,
    record_llm_business_error,
    record_llm_fallback,
    require_json_list,
    requires_real_llm,
    write_json,
    write_llm_audit_report,
)

TREND_ANALYSIS_SKILL = SkillSpec(
    name="trend_analysis",
    references=(
        "references/trend_schema.md",
        "references/trend_guidelines.md",
        "references/examples.json",
    ),
    validator_script="scripts/validate_trend_insights.py",
    context_key="trend_analysis_skill_validation",
)
RISK_DETECTION_SKILL = SkillSpec(
    name="risk_detection",
    references=(
        "references/risk_opportunity_schema.md",
        "references/risk_detection_guidelines.md",
        "references/examples.json",
    ),
    validator_script="scripts/validate_risk_opportunity.py",
    context_key="risk_detection_skill_validation",
)


def run(context: PipelineContext) -> DailyInsightReport:
    """Generate report sections from validated structured data."""

    items = _validated_items(context)
    if not items:
        raise ValueError("analysis requires at least one validated item")

    adapter = active_llm_adapter(context)
    if adapter is None:
        report = _rule_based_report(context, items)
        context.set("analyze_mode", "rule_based")
    else:
        try:
            report = _llm_report(context, adapter, items)
            context.set("analyze_mode", "llm")
        except LLMBusinessError as error:
            record_llm_business_error(context, "analyze", error)
            if requires_real_llm(context):
                raise
            report = _rule_based_report(context, items)
            fallback = record_llm_fallback(
                context,
                "analyze",
                reason=str(error),
                error_type=error.error_type,
                details={"item_count": len(items)},
            )
            context.set("analyze_mode", "llm_fallback")
            context.set("analyze_fallback_reason", fallback)

    report = _repair_historical_comparisons(context, report, items)
    report, audit = filter_report_by_supported_evidence(
        report,
        items,
        run_id=context.run_id,
    )
    if not report.top_events and adapter is not None and context.get("analyze_mode") == "llm":
        error = LLMBusinessError(
            "audit_failure",
            "analyze evidence audit removed all LLM top_events",
            details={"blocked_count": audit.get("blocked_count", 0)},
        )
        record_llm_business_error(context, "analyze", error)
        if requires_real_llm(context):
            raise error
        report = _rule_based_report(context, items)
        fallback = record_llm_fallback(
            context,
            "analyze",
            reason=str(error),
            error_type=error.error_type,
            details={"item_count": len(items)},
        )
        context.set("analyze_mode", "llm_fallback")
        context.set("analyze_fallback_reason", fallback)
        report = _repair_historical_comparisons(context, report, items)
        report, audit = filter_report_by_supported_evidence(
            report,
            items,
            run_id=context.run_id,
        )
    write_llm_audit_report(context, audit)
    if not report.top_events:
        raise ValueError("analysis audit failed: no top_events have supported evidence")

    report = _repair_risk_opportunity_insights(context, report, items)
    report = _normalize_report_memory_usage(context, report)
    _write_historical_evidence_audit(context, report)
    output_path = path_for(context, "report_sections")
    write_json(output_path, report.model_dump(mode="json"))
    _validate_analysis_skills(context, items, output_path)
    context.add_artifact("report_sections", output_path)
    context.set("report_sections", report)
    return report


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


def _rule_based_report(
    context: PipelineContext,
    items: list[StructuredNewsItem],
) -> DailyInsightReport:
    sorted_items = sorted(items, key=lambda item: item.importance_score, reverse=True)
    top_items = sorted_items[:5]
    topics = Counter(item.topic for item in items)
    memory_context = _memory_context(context)
    trend_signals = _trend_signal_payloads(items, topics, memory_context)
    source_documents = _source_documents(context, items)
    historical_comparisons = _historical_comparisons(items, memory_context)
    context.set("analyze_trend_signals", trend_signals)
    context.set("analyze_source_documents", source_documents)
    context.set(
        "analyze_adopted_historical_evidence",
        _historical_comparison_audit_payload(historical_comparisons),
    )
    return DailyInsightReport(
        report_date=context.run_date,
        title=f"AI 洞察日报 - {context.run_date.isoformat()}",
        executive_summary=_executive_summary(items, topics),
        top_events=[_top_event(item) for item in top_items],
        deep_dives=[
            _deep_dive(
                item,
                source_documents=source_documents,
                memory_context=memory_context,
                trend_signals=trend_signals,
                historical_comparisons=historical_comparisons,
            )
            for item in top_items[:3]
        ],
        trend_insights=_trend_insights(
            items,
            topics,
            bool(context.historical_context),
            trend_signals,
            historical_comparisons,
        ),
        risk_insights=_risk_insights(items),
        opportunity_insights=_opportunity_insights(items),
        memory_usage=_memory_usage_summary(context, memory_context, historical_comparisons),
        historical_comparisons=historical_comparisons,
        chart_refs=_chart_refs(context),
    )


def _llm_report(
    context: PipelineContext,
    adapter: object,
    items: list[StructuredNewsItem],
) -> DailyInsightReport:
    chart_refs = _chart_refs(context)
    topics = Counter(item.topic for item in items)
    memory_context = _memory_context(context)
    trend_signals = _trend_signal_payloads(items, topics, memory_context)
    source_documents = _source_documents(context, items)
    historical_comparisons = _historical_comparisons(items, memory_context)
    context.set("analyze_trend_signals", trend_signals)
    context.set("analyze_source_documents", source_documents)
    context.set(
        "analyze_adopted_historical_evidence",
        _historical_comparison_audit_payload(historical_comparisons),
    )
    analysis_input = {
        "report_date": context.run_date.isoformat(),
        "validated_items": model_list_payload(items),
        "source_documents": source_documents,
        "evidence_policy": {
            "required_status": "supported",
            "rule": (
                "Every TopEvent, DeepDiveSection, TrendInsight, RiskInsight, "
                "and OpportunityInsight must include evidence_sources copied "
                "from the cited validated_items evidence_sources. Unsupported "
                "analysis items are removed before report generation."
            ),
        },
        "historical_context": context.historical_context,
        "memory_context": memory_context,
        "trend_signals": trend_signals,
        "memory_usage": _memory_usage_summary(
            context,
            memory_context,
            historical_comparisons,
        ).model_dump(mode="json"),
        "historical_evidence_candidates": _historical_comparison_audit_payload(
            historical_comparisons,
        ),
        "chart_refs": chart_refs,
        "report_language": _report_language(context),
    }
    template = load_prompt_template(
        context,
        "analyze_daily_report",
        "prompts/analyze_daily_report.md",
    )
    template = SkillRunner().apply_prompt_context(
        template,
        [TREND_ANALYSIS_SKILL, RISK_DETECTION_SKILL],
    )
    prompt = template.replace(
        "{{analysis_input_json}}",
        json.dumps(analysis_input, ensure_ascii=False, indent=2),
    )
    calls: list[dict[str, object]] = []
    try:
        report, _ = llm_call_with_business_retries(
            context,
            adapter,
            prompt,
            operation="analyze report",
            call_metadata={"scope": "report"},
            parse_result=_parse_report,
            calls=calls,
        )
    finally:
        context.set("analyze_llm_calls", calls)
        if calls:
            context.set("analyze_llm_call", _combined_llm_call(calls))
    return report


def _report_language(context: PipelineContext) -> str:
    value = context.config.get("report_language")
    if value:
        return str(value)
    pipeline_config = context.config.get("pipeline")
    if isinstance(pipeline_config, dict) and pipeline_config.get("report_language"):
        return str(pipeline_config["report_language"])
    return "zh-CN"


def _source_documents(
    context: PipelineContext,
    items: list[StructuredNewsItem],
) -> dict[str, dict[str, Any]]:
    source_items = _source_items(context, "relevant_items", "relevant")
    if not source_items:
        source_items = _source_items(context, "cleaned_items", "cleaned")
    if not source_items:
        return {}

    index: dict[str, dict[str, Any]] = {}
    for source in source_items:
        payload = source.model_dump(mode="json")
        for key in _source_keys(payload):
            index.setdefault(key, payload)

    documents: dict[str, dict[str, Any]] = {}
    for item in items:
        source = _matching_source_document(item, index)
        if source is None:
            continue
        documents[item.id] = {
            "item_id": item.id,
            "source_item_id": source.get("id"),
            "title": source.get("title"),
            "source": source.get("source"),
            "url": source.get("url"),
            "published_at": source.get("published_at"),
            "summary": source.get("summary", ""),
            "content": source.get("content", ""),
            "metadata": dict(source.get("metadata") or {}),
            "content_hash": source.get("content_hash"),
            "full_context_policy": "full clean/relevant content included without truncation",
        }
    return documents


def _source_items(
    context: PipelineContext,
    context_key: str,
    path_key: str,
) -> list[CleanNewsItem]:
    payload = context.get(context_key)
    if payload is None:
        try:
            path = path_for(context, path_key)
        except KeyError:
            return []
        if path.exists():
            payload = require_json_list(path)
    if payload is None:
        return []
    return [
        item if isinstance(item, CleanNewsItem) else CleanNewsItem.model_validate(item)
        for item in payload
    ]


def _source_keys(payload: dict[str, Any]) -> list[str]:
    return _unique_text(
        [
            str(payload.get("id") or ""),
            str(payload.get("url") or ""),
            str(payload.get("content_hash") or ""),
        ]
    )


def _matching_source_document(
    item: StructuredNewsItem,
    index: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    for key in _structured_source_keys(item):
        if key in index:
            return index[key]
    return None


def _structured_source_keys(item: StructuredNewsItem) -> list[str]:
    keys: list[str] = []
    for source in item.evidence_sources:
        keys.append(source.source_item_id)
    keys.extend([item.id, item.url, item.content_hash])
    if item.id.startswith("structured-"):
        keys.append(item.id.replace("structured-", "raw-", 1))
    return _unique_text(keys)


def _parse_report(content: str) -> DailyInsightReport:
    try:
        parsed = parse_llm_json(content, "analyze")
    except ValueError as exc:
        raise LLMBusinessError("invalid_json", str(exc)) from exc
    try:
        return DailyInsightReport.model_validate(parsed)
    except Exception as exc:
        raise LLMBusinessError(
            "schema_error",
            f"analyze report failed schema validation: {exc}",
        ) from exc


def _combined_llm_call(calls: list[dict[str, object]]) -> dict[str, object]:
    return {
        "model": str(calls[-1].get("model") or ""),
        "success": all(bool(call.get("success")) for call in calls),
        "prompt_tokens": sum(_int(call.get("prompt_tokens")) for call in calls),
        "completion_tokens": sum(_int(call.get("completion_tokens")) for call in calls),
        "total_tokens": sum(_int(call.get("total_tokens")) for call in calls),
        "cost_usd": round(sum(_float(call.get("cost_usd")) for call in calls), 10),
        "elapsed_ms": sum(_int(call.get("elapsed_ms")) for call in calls),
        "error": "; ".join(
            str(call.get("error"))
            for call in calls
            if call.get("error") is not None
        )
        or None,
    }


def _executive_summary(
    items: list[StructuredNewsItem],
    topics: Counter[str],
) -> str:
    top_topic, top_count = topics.most_common(1)[0]
    avg_score = round(sum(item.importance_score for item in items) / len(items), 1)
    high_risk = sum(1 for item in items if item.risk_level.value == "high")
    return (
        f"本期共处理 {len(items)} 条 AI 相关新闻，覆盖 {len(topics)} 个主题。"
        f"最集中的主题是 {top_topic}（{top_count} 条），平均关注度评分为 {avg_score}。"
        f"其中 {high_risk} 条被标记为高风险，说明本期既有产品与模型发布机会，"
        "也需要关注安全、监管与产业执行风险。"
    )


def _top_event(item: StructuredNewsItem) -> TopEvent:
    return TopEvent(
        item_id=item.id,
        title=item.title,
        source=item.source,
        importance_score=item.importance_score,
        reason=f"{item.importance_rationale}（关注度 {item.importance_score}）",
        impact=(
            f"主要影响范围为 {item.impact_scope.value}，"
            f"机会提示：{item.opportunity_rationale}；"
            f"风险提示：{item.risk_rationale}。"
        ),
        evidence_sources=item.evidence_sources[:1],
    )


def _deep_dive(
    item: StructuredNewsItem,
    *,
    source_documents: dict[str, dict[str, Any]] | None = None,
    memory_context: dict[str, Any] | None = None,
    trend_signals: list[dict[str, Any]] | None = None,
    historical_comparisons: list[HistoricalComparison] | None = None,
) -> DeepDiveSection:
    follow_up_questions = [
        "短期内跟踪后续产品或模型更新的发布时间、能力范围和兼容性变化。",
        "观察相关能力在企业或开发者工作流中的实际采用案例、集成深度和使用成本。",
    ]
    return DeepDiveSection(
        item_id=item.id,
        narrative_analysis=_narrative_analysis(
            item,
            source_documents=source_documents or {},
            memory_context=memory_context or {},
            trend_signals=trend_signals or [],
            follow_up_questions=follow_up_questions,
        ),
        historical_context_note=_historical_context_note(
            item,
            historical_comparisons or [],
            memory_context or {},
            trend_signals or [],
        ),
        background=f"该事件来自 {item.source}，主题归类为 {item.topic}。",
        current_progress=item.summary,
        involved_entities=item.entities,
        impact_analysis=(
            f"该事件可能影响 {item.impact_scope.value} 方向。"
            f"关键依据包括：{'；'.join(item.evidence[:2])}"
        ),
        follow_up_questions=follow_up_questions,
        evidence_sources=item.evidence_sources[:1],
    )


def _narrative_analysis(
    item: StructuredNewsItem,
    *,
    source_documents: dict[str, dict[str, Any]],
    memory_context: dict[str, Any],
    trend_signals: list[dict[str, Any]],
    follow_up_questions: list[str],
) -> str:
    document = source_documents.get(item.id, {})
    source_detail = _source_detail_sentence(document)
    history = _history_sentence(item, memory_context, trend_signals)
    details = _detail_sentence(item, document)
    evidence = "；".join(item.evidence[:2]) or item.summary
    follow_up = "；".join(follow_up_questions)
    return (
        f"{item.title} 之所以值得展开，不只是因为它被归入 {item.topic}，"
        f"而是它把今日 AI 新闻中的具体事实和后续产业判断连接了起来。"
        f"{source_detail}今日关键事实是：{item.summary}"
        f"从技术、产品或商业细节看，{details}"
        f"这件事在行业位置上更接近一次 {item.event_type.value} 信号，"
        f"主要影响 {item.impact_scope.value} 方向；关注度依据是{item.importance_rationale}。"
        f"{history}"
        f"影响推演上，机会侧需要看{item.opportunity_rationale}，"
        f"风险侧则要看{item.risk_rationale}。"
        f"当前可审计依据包括：{evidence}。"
        f"短期验证应聚焦：{follow_up}"
    )


def _source_detail_sentence(document: dict[str, Any]) -> str:
    content = str(document.get("content") or "").strip()
    summary = str(document.get("summary") or "").strip()
    if content:
        return "Analyzer 已重新读取本轮清洗/相关性筛选后的完整原文，用它补足结构化摘要之外的上下文。"
    if summary:
        return "Analyzer 已重新读取本轮清洗/相关性筛选后的来源摘要，用它补足结构化字段之外的上下文。"
    return "当前没有匹配到可用原文，只能基于结构化字段和证据进行保守分析。"


def _detail_sentence(item: StructuredNewsItem, document: dict[str, Any]) -> str:
    content = str(document.get("content") or document.get("summary") or "")
    detail_terms = _specific_terms(content)
    if detail_terms:
        return f"原文中可见的具体抓手包括 {', '.join(detail_terms[:6])}，这些细节比单句摘要更能说明事件的落点。"
    key_points = "；".join(item.key_points[:3])
    if key_points:
        return f"结构化要点显示：{key_points}。"
    return "目前材料给出的技术或商业细节有限，需要等待后续披露补足判断。"


def _specific_terms(text: str) -> list[str]:
    if not text:
        return []
    candidates: list[str] = []
    patterns = [
        r"\b[A-Z][A-Za-z0-9_.+-]{1,}\b",
        r"\b[A-Za-z]+[-_]?[A-Za-z0-9]*\d+[A-Za-z0-9_.-]*\b",
        r"\b\d+(?:\.\d+)?\s?(?:%|TOPS|GB|TB|tokens?|parameters?|B|M)\b",
    ]
    for pattern in patterns:
        candidates.extend(match.group(0) for match in re.finditer(pattern, text))
    return _unique_text(candidates)


def _history_sentence(
    item: StructuredNewsItem,
    memory_context: dict[str, Any],
    trend_signals: list[dict[str, Any]],
) -> str:
    signal = next(
        (payload for payload in trend_signals if str(payload.get("topic")) == item.topic),
        {},
    )
    historical_count = _int(signal.get("historical_item_count"))
    state = str(signal.get("rule_suggested_state") or "")
    if historical_count:
        return (
            f"放到历史语境里，Memory 中已有 {historical_count} 条同主题记录，"
            f"规则趋势信号判断为 {state or 'continuing'}，说明它不是孤立新闻，"
            "需要和近期同主题事件一起观察。"
        )
    relationships = [
        relationship
        for relationship in list(memory_context.get("item_relationships") or [])
        if isinstance(relationship, dict)
        and str(relationship.get("item_id") or "") == item.id
    ]
    if relationships:
        relation = str(relationships[0].get("relationship") or "related_context")
        return f"软相似度信号显示它与历史材料存在 {relation} 关系，适合放在延续脉络中复核。"
    return "当前记忆中没有足够同主题历史证据，行业位置判断应以今日材料为主，避免外推过度。"


def _unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = value.strip(" ,.;:()[]{}")
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        unique.append(text)
    return unique[:12]


def _trend_insights(
    items: list[StructuredNewsItem],
    topics: Counter[str],
    has_history: bool,
    trend_signals: list[dict[str, Any]],
    historical_comparisons: list[HistoricalComparison],
) -> list[TrendInsight]:
    insights: list[TrendInsight] = []
    signals_by_topic = {str(signal["topic"]): signal for signal in trend_signals}
    for topic, _count in topics.most_common(3):
        topic_items = [item for item in items if item.topic == topic]
        scope = topic_items[0].impact_scope
        trend_signal = signals_by_topic.get(topic, {})
        trend_state = TrendState(
            str(trend_signal.get("rule_suggested_state") or TrendState.NEW.value)
        )
        insights.append(
            TrendInsight(
                title=f"{topic} {_trend_state_title(trend_state)}",
                scope=scope,
                summary=(
                    f"{topic} 在本期出现 {len(topic_items)} 次，"
                    f"最高关注度评分为 {max(item.importance_score for item in topic_items)}。"
                    f"{_trend_state_summary(trend_state)}"
                ),
                evidence_item_ids=[item.id for item in topic_items[:3]],
                trend_state=trend_state,
                historical_context_used=has_history
                or bool(_historical_evidence_for_items(topic_items, historical_comparisons)),
                historical_evidence=_historical_evidence_for_items(
                    topic_items,
                    historical_comparisons,
                ),
                evidence_sources=[
                    source
                    for item in topic_items[:3]
                    for source in item.evidence_sources[:1]
                ],
            )
        )
    return insights


def _memory_context(context: PipelineContext) -> dict[str, Any]:
    value = context.get("memory_context", {})
    return value if isinstance(value, dict) else {}


def _repair_historical_comparisons(
    context: PipelineContext,
    report: DailyInsightReport,
    items: list[StructuredNewsItem],
) -> DailyInsightReport:
    candidates = _historical_comparisons(items, _memory_context(context))
    if not report.historical_comparisons:
        return report

    current_ids = {item.id for item in items}
    candidate_keys = {
        _historical_comparison_key(candidate)
        for candidate in candidates
    }
    repaired: list[HistoricalComparison] = []
    invalid: list[dict[str, Any]] = []
    for comparison in report.historical_comparisons:
        key = _historical_comparison_key(comparison)
        if comparison.current_item_id not in current_ids:
            invalid.append(
                {
                    "current_item_id": comparison.current_item_id,
                    "memory_item_id": comparison.memory_item_id,
                    "reason": "current_item_id_not_in_validated_items",
                }
            )
            continue
        if candidate_keys and key not in candidate_keys:
            invalid.append(
                {
                    "current_item_id": comparison.current_item_id,
                    "memory_item_id": comparison.memory_item_id,
                    "historical_event_title": comparison.historical_event_title,
                    "reason": "historical_evidence_not_in_candidate_context",
                }
            )
            continue
        repaired.append(_clamp_weak_historical_strength(comparison))

    if invalid:
        context.set("historical_evidence_invalid_adoptions", invalid)
    if len(repaired) == len(report.historical_comparisons) and not invalid:
        return report
    return report.model_copy(update={"historical_comparisons": repaired})


def _historical_comparison_key(comparison: HistoricalComparison) -> tuple[str, str, str]:
    return (
        comparison.current_item_id,
        str(comparison.memory_item_id or "").strip().lower(),
        comparison.historical_event_title.strip().lower(),
    )


def _clamp_weak_historical_strength(
    comparison: HistoricalComparison,
) -> HistoricalComparison:
    if comparison.relation_type in {"background", "related_context"}:
        return comparison.model_copy(
            update={"relevance_strength": min(comparison.relevance_strength, 59)}
        )
    return comparison


def _normalize_report_memory_usage(
    context: PipelineContext,
    report: DailyInsightReport,
) -> DailyInsightReport:
    summary = _memory_usage_summary(
        context,
        _memory_context(context),
        report.historical_comparisons,
    )
    return report.model_copy(update={"memory_usage": summary})


def _write_historical_evidence_audit(
    context: PipelineContext,
    report: DailyInsightReport,
) -> None:
    memory_report = context.get("memory_report")
    if not isinstance(memory_report, dict):
        memory_report = _load_memory_report_for_update(context)
    if not isinstance(memory_report, dict):
        return
    adopted = [
        comparison.model_dump(mode="json")
        for comparison in report.historical_comparisons
    ]
    selected_ids = [
        memory_item_id
        for comparison in report.historical_comparisons
        for memory_item_id in [comparison.memory_item_id]
        if memory_item_id
    ]
    fulltext_selection = memory_report.get("fulltext_selection")
    fulltext_selection = fulltext_selection if isinstance(fulltext_selection, dict) else {}
    requested_ids = [
        str(value)
        for value in list(fulltext_selection.get("requested_item_ids") or [])
    ]
    read_ids = [str(value) for value in list(fulltext_selection.get("read_item_ids") or [])]
    selected_set = set(selected_ids)
    unadopted = [
        {
            "memory_item_id": memory_item_id,
            "reason": "read_but_not_used_as_final_historical_evidence",
        }
        for memory_item_id in read_ids
        if memory_item_id not in selected_set
    ]
    memory_report["historical_evidence_selection"] = {
        "status": "succeeded" if adopted else "skipped",
        "requested_item_ids": requested_ids,
        "read_item_ids": read_ids,
        "adopted_count": len(adopted),
        "adopted_items": adopted,
        "unadopted_items": unadopted,
        "invalid_item_ids": fulltext_selection.get("invalid_item_ids", []),
        "invalid_adopted_items": context.get(
            "historical_evidence_invalid_adoptions",
            [],
        ),
        "fallback_mode": fulltext_selection.get("mode"),
    }
    context.set("memory_report", memory_report)
    report_path = context.paths.get("memory_report")
    if report_path is None:
        return
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(memory_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_memory_report_for_update(context: PipelineContext) -> dict[str, Any] | None:
    report_path = context.paths.get("memory_report")
    if report_path is None:
        return None
    path = Path(report_path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _memory_usage_summary(
    context: PipelineContext,
    memory_context: dict[str, Any],
    historical_comparisons: list[HistoricalComparison],
) -> MemoryUsageSummary:
    report = context.get("memory_report")
    report = report if isinstance(report, dict) else {}
    strong_dedupe = report.get("strong_dedupe")
    strong_dedupe = strong_dedupe if isinstance(strong_dedupe, dict) else {}
    context_retrieval = report.get("context_retrieval")
    context_retrieval = context_retrieval if isinstance(context_retrieval, dict) else {}
    fulltext_selection = report.get("fulltext_selection")
    fulltext_selection = fulltext_selection if isinstance(fulltext_selection, dict) else {}

    relevant_candidate_count = _int(strong_dedupe.get("input_count"))
    if not relevant_candidate_count:
        relevant_candidate_count = len(context.get("relevant_items", []) or [])
    if not relevant_candidate_count:
        relevant_candidate_count = len(context.get("validated_items", []) or [])

    retrieved_metadata_count = _int(context_retrieval.get("metadata_included_count"))
    if not retrieved_metadata_count:
        retrieved_metadata_count = _int(memory_context.get("metadata_included_count"))

    read_fulltext_count = len(memory_context.get("fulltext_items") or [])
    if not read_fulltext_count:
        read_fulltext_count = len(fulltext_selection.get("read_item_ids") or [])

    return MemoryUsageSummary(
        relevant_candidate_count=relevant_candidate_count,
        strong_duplicate_filtered_count=_int(strong_dedupe.get("filtered_count")),
        retrieved_metadata_count=retrieved_metadata_count,
        read_fulltext_count=read_fulltext_count,
        adopted_historical_evidence_count=len(historical_comparisons),
    )


def _historical_comparisons(
    items: list[StructuredNewsItem],
    memory_context: dict[str, Any],
) -> list[HistoricalComparison]:
    comparisons: list[HistoricalComparison] = []
    seen: set[tuple[str, str]] = set()
    fulltext_by_id = {
        str(item.get("memory_item_id")): item
        for item in list(memory_context.get("fulltext_items") or [])
        if isinstance(item, dict) and item.get("memory_item_id")
    }
    entries_by_id = _memory_entries_by_id(memory_context)

    for item in sorted(items, key=lambda value: value.importance_score, reverse=True):
        relationships = _relationships_for_item(item, memory_context)
        ranked_relationships = sorted(
            relationships,
            key=lambda relationship: (
                _relationship_rank(str(relationship.get("relationship") or "")),
                _float(relationship.get("confidence")),
            ),
            reverse=True,
        )
        adopted = False
        for relationship in ranked_relationships:
            for memory_item_id in list(relationship.get("matched_memory_item_ids") or []):
                memory_id = str(memory_item_id).strip()
                memory_item = fulltext_by_id.get(memory_id) or entries_by_id.get(memory_id)
                if not memory_item:
                    continue
                key = (item.id, memory_id)
                if key in seen:
                    continue
                comparisons.append(
                    _historical_comparison_from_memory(
                        item,
                        memory_item,
                        relation_type=str(
                            relationship.get("relationship") or "related_context"
                        ),
                        confidence=_float(relationship.get("confidence")),
                    )
                )
                seen.add(key)
                adopted = True
                break
            if adopted:
                break

        if adopted:
            continue

        fallback = _best_topic_memory_item(item, memory_context, fulltext_by_id)
        if fallback is None:
            continue
        memory_id = str(
            fallback.get("memory_item_id") or fallback.get("id") or fallback.get("title")
        )
        key = (item.id, memory_id)
        if key in seen:
            continue
        comparisons.append(
            _historical_comparison_from_memory(
                item,
                fallback,
                relation_type="background",
                confidence=0.45,
            )
        )
        seen.add(key)

    return comparisons[:5]


def _memory_entries_by_id(memory_context: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for topic_payload in list(memory_context.get("topics") or []):
        if not isinstance(topic_payload, dict):
            continue
        for entry in list(topic_payload.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            for key in (
                entry.get("memory_item_id"),
                entry.get("id"),
                entry.get("validated_item_id"),
                entry.get("source_item_id"),
            ):
                text = str(key or "").strip()
                if text:
                    by_id.setdefault(text, entry)
    return by_id


def _relationships_for_item(
    item: StructuredNewsItem,
    memory_context: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        relationship
        for relationship in list(memory_context.get("item_relationships") or [])
        if isinstance(relationship, dict)
        and str(relationship.get("item_id") or "") == item.id
    ]


def _relationship_rank(value: str) -> int:
    return {
        "likely_duplicate": 4,
        "continuing": 3,
        "related_context": 2,
        "background": 1,
        "new": 0,
    }.get(value, 0)


def _best_topic_memory_item(
    item: StructuredNewsItem,
    memory_context: dict[str, Any],
    fulltext_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = _memory_entries_for_topic(item.topic, memory_context)
    if not candidates:
        return None
    current_entities = {entity.lower() for entity in item.entities}

    def sort_key(candidate: dict[str, Any]) -> tuple[int, int, str]:
        candidate_entities = {
            str(entity).lower() for entity in list(candidate.get("entities") or [])
        }
        entity_overlap = len(current_entities & candidate_entities)
        memory_id = str(candidate.get("memory_item_id") or candidate.get("id") or "")
        has_fulltext = 1 if memory_id in fulltext_by_id else 0
        return (
            entity_overlap,
            has_fulltext,
            str(candidate.get("published_at") or ""),
        )

    best = max(candidates, key=sort_key)
    memory_id = str(best.get("memory_item_id") or best.get("id") or "")
    return fulltext_by_id.get(memory_id) or best


def _historical_comparison_from_memory(
    item: StructuredNewsItem,
    memory_item: dict[str, Any],
    *,
    relation_type: str,
    confidence: float,
) -> HistoricalComparison:
    memory_item_id = str(
        memory_item.get("memory_item_id")
        or memory_item.get("id")
        or memory_item.get("validated_item_id")
        or ""
    ).strip()
    historical_title = str(memory_item.get("title") or "历史同主题事件").strip()
    historical_date = str(
        memory_item.get("published_at") or memory_item.get("run_date") or "unknown"
    ).strip()
    strength = _relationship_strength(
        item,
        memory_item,
        relation_type=relation_type,
        confidence=confidence,
    )
    reason = _historical_relevance_reason(item, memory_item, relation_type)
    return HistoricalComparison(
        current_item_id=item.id,
        current_event_title=item.title,
        memory_item_id=memory_item_id or None,
        historical_event_title=historical_title,
        historical_event_date=historical_date,
        relation_type=relation_type,
        relevance_strength=strength,
        rationale=reason,
        impact_on_today=_historical_impact_sentence(relation_type, strength),
    )


def _relationship_strength(
    item: StructuredNewsItem,
    memory_item: dict[str, Any],
    *,
    relation_type: str,
    confidence: float,
) -> int:
    if confidence > 0:
        base = int(round(min(1.0, confidence) * 100))
    else:
        base = {
            "likely_duplicate": 86,
            "continuing": 76,
            "related_context": 62,
            "background": 45,
        }.get(relation_type, 40)
    historical_entities = {
        str(entity).lower() for entity in list(memory_item.get("entities") or [])
    }
    current_entities = {entity.lower() for entity in item.entities}
    if current_entities & historical_entities:
        base += 8
    if str(memory_item.get("event_type") or "") == item.event_type.value:
        base += 6
    if relation_type == "background":
        base = min(base, 59)
    return max(0, min(100, base))


def _historical_relevance_reason(
    item: StructuredNewsItem,
    memory_item: dict[str, Any],
    relation_type: str,
) -> str:
    shared_entities = [
        entity
        for entity in item.entities
        if entity.lower()
        in {str(value).lower() for value in list(memory_item.get("entities") or [])}
    ]
    title = str(memory_item.get("title") or "该历史事件")
    if shared_entities:
        return (
            f"历史事件「{title}」与今日事件共享 {', '.join(shared_entities[:3])} "
            f"等主体，且关系类型为 {relation_type}。"
        )
    if str(memory_item.get("event_type") or "") == item.event_type.value:
        return (
            f"历史事件「{title}」与今日事件同属 {item.event_type.value}，"
            "可用于观察同类事件的延续性。"
        )
    return (
        f"历史事件「{title}」与今日事件同属 {item.topic}，"
        "只能作为主题背景参照，不能替代今日事实判断。"
    )


def _historical_impact_sentence(relation_type: str, strength: int) -> str:
    if relation_type in {"continuing", "likely_duplicate"} and strength >= 70:
        return "这提高了将今日事件视为延续或升温信号的可信度，但结论仍需回到今日证据。"
    if relation_type == "related_context":
        return "这说明今日事件有可比历史背景，适合辅助判断趋势方向和风险机会变化。"
    return "这主要提供背景参照，今日新闻价值仍应由当日事实、证据质量和评分决定。"


def _historical_evidence_for_items(
    topic_items: list[StructuredNewsItem],
    historical_comparisons: list[HistoricalComparison],
) -> list[HistoricalEvidenceReference]:
    evidence: list[HistoricalEvidenceReference] = []
    seen: set[str] = set()
    item_ids = {item.id for item in topic_items}
    for comparison in historical_comparisons:
        if comparison.current_item_id not in item_ids:
            continue
        key = comparison.memory_item_id or comparison.historical_event_title
        if key in seen:
            continue
        evidence.append(
            HistoricalEvidenceReference(
                memory_item_id=comparison.memory_item_id,
                title=comparison.historical_event_title,
                published_at=comparison.historical_event_date,
                reason=comparison.rationale,
            )
        )
        seen.add(key)
        if len(evidence) >= 3:
            break
    return evidence


def _historical_context_note(
    item: StructuredNewsItem,
    historical_comparisons: list[HistoricalComparison],
    memory_context: dict[str, Any],
    trend_signals: list[dict[str, Any]],
) -> str:
    comparison = next(
        (
            value
            for value in historical_comparisons
            if value.current_item_id == item.id
        ),
        None,
    )
    if comparison is not None:
        return (
            f"该事件与「{comparison.historical_event_title}」"
            f"形成 {comparison.relation_type} 关系。{comparison.impact_on_today}"
        )
    return _history_sentence(item, memory_context, trend_signals)


def _historical_comparison_audit_payload(
    comparisons: list[HistoricalComparison],
) -> list[dict[str, Any]]:
    return [comparison.model_dump(mode="json") for comparison in comparisons]


def _trend_signal_payloads(
    items: list[StructuredNewsItem],
    topics: Counter[str],
    memory_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build advisory trend signals for LLM analysis and rule fallback."""

    signals: list[dict[str, Any]] = []
    for topic, _count in topics.most_common(5):
        topic_items = [item for item in items if item.topic == topic]
        historical_entries = _memory_entries_for_topic(topic, memory_context)
        relationships = _relationships_for_topic(topic, memory_context)
        current_peak = max(item.importance_score for item in topic_items)
        historical_scores = [
            _int(entry.get("importance_score"))
            for entry in historical_entries
            if entry.get("importance_score") is not None
        ]
        historical_peak = max(historical_scores) if historical_scores else 0
        rule_suggested_state = _trend_state_from_signals(
            topic_items,
            historical_entries,
            relationships,
        )
        signals.append(
            {
                "topic": topic,
                "rule_suggested_state": rule_suggested_state.value,
                "current_item_count": len(topic_items),
                "current_peak_importance_score": current_peak,
                "current_average_importance_score": round(
                    sum(item.importance_score for item in topic_items) / len(topic_items),
                    1,
                ),
                "current_event_types": _counter_payload(
                    item.event_type.value for item in topic_items
                ),
                "current_risk_levels": _counter_payload(
                    item.risk_level.value for item in topic_items
                ),
                "current_opportunity_levels": _counter_payload(
                    item.opportunity_level.value for item in topic_items
                ),
                "current_entities": _unique_entities(topic_items),
                "historical_item_count": len(historical_entries),
                "historical_peak_importance_score": historical_peak,
                "historical_risk_levels": _counter_payload(
                    str(entry.get("risk_level") or "")
                    for entry in historical_entries
                    if entry.get("risk_level")
                ),
                "historical_opportunity_levels": _counter_payload(
                    str(entry.get("opportunity_level") or "")
                    for entry in historical_entries
                    if entry.get("opportunity_level")
                ),
                "soft_relationships": _counter_payload(
                    str(relationship.get("relationship") or "")
                    for relationship in relationships
                    if relationship.get("relationship")
                ),
                "relationship_confidence_peak": _relationship_confidence_peak(
                    relationships
                ),
                "matched_memory_item_ids": _matched_memory_item_ids(relationships),
                "risk_direction": _risk_direction(topic_items, historical_entries),
                "opportunity_direction": _opportunity_direction(
                    topic_items,
                    historical_entries,
                ),
                "evidence_item_ids": [item.id for item in topic_items[:3]],
                "advisory_note": (
                    "rule_suggested_state is a deterministic fallback suggestion. "
                    "When an LLM is available, it should make the final trend_state "
                    "judgment from current evidence, memory_context, and these signals."
                ),
            }
        )
    return signals


def _trend_state(
    topic: str,
    topic_items: list[StructuredNewsItem],
    memory_context: dict[str, Any],
) -> TrendState:
    historical_entries = _memory_entries_for_topic(topic, memory_context)
    relationships = _relationships_for_topic(topic, memory_context)
    return _trend_state_from_signals(topic_items, historical_entries, relationships)


def _trend_state_from_signals(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> TrendState:
    if not historical_entries and not relationships:
        return TrendState.NEW
    if _has_reversal_signal(topic_items, historical_entries):
        return TrendState.REVERSING
    if _current_is_heating_up(topic_items, historical_entries):
        return TrendState.HEATING_UP
    if _current_is_cooling_down(topic_items, historical_entries):
        return TrendState.COOLING_DOWN
    if any(
        relationship.get("relationship") in {"continuing", "likely_duplicate", "related_context"}
        for relationship in relationships
    ) or historical_entries:
        return TrendState.CONTINUING
    return TrendState.NEW


def _memory_entries_for_topic(
    topic: str,
    memory_context: dict[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for topic_payload in list(memory_context.get("topics") or []):
        if not isinstance(topic_payload, dict):
            continue
        if str(topic_payload.get("topic") or "").strip().lower() != topic.strip().lower():
            continue
        for entry in list(topic_payload.get("entries") or []):
            if isinstance(entry, dict):
                entries.append(entry)
    return entries


def _relationships_for_topic(
    topic: str,
    memory_context: dict[str, Any],
) -> list[dict[str, Any]]:
    relationships: list[dict[str, Any]] = []
    for relationship in list(memory_context.get("item_relationships") or []):
        if not isinstance(relationship, dict):
            continue
        if str(relationship.get("topic") or "").strip().lower() == topic.strip().lower():
            relationships.append(relationship)
    return relationships


def _counter_payload(values: Any) -> dict[str, int]:
    counts = Counter(str(value) for value in values if str(value or "").strip())
    return dict(sorted(counts.items()))


def _unique_entities(topic_items: list[StructuredNewsItem]) -> list[str]:
    entities: list[str] = []
    seen: set[str] = set()
    for item in topic_items:
        for entity in item.entities:
            key = entity.strip().lower()
            if key and key not in seen:
                entities.append(entity)
                seen.add(key)
    return entities[:10]


def _relationship_confidence_peak(relationships: list[dict[str, Any]]) -> float:
    scores: list[float] = []
    for relationship in relationships:
        try:
            scores.append(float(relationship.get("confidence") or 0.0))
        except (TypeError, ValueError):
            continue
    return round(max(scores), 3) if scores else 0.0


def _matched_memory_item_ids(relationships: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for relationship in relationships:
        for item_id in list(relationship.get("matched_memory_item_ids") or []):
            key = str(item_id).strip()
            if key and key not in seen:
                ids.append(key)
                seen.add(key)
    return ids


def _risk_direction(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
) -> str:
    current_rank = max(_risk_rank(item.risk_level.value) for item in topic_items)
    historical_rank = max(
        (_risk_rank(entry.get("risk_level")) for entry in historical_entries),
        default=0,
    )
    return _direction(current_rank, historical_rank)


def _opportunity_direction(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
) -> str:
    current_rank = max(_opportunity_rank(item.opportunity_level.value) for item in topic_items)
    historical_rank = max(
        (_opportunity_rank(entry.get("opportunity_level")) for entry in historical_entries),
        default=0,
    )
    return _direction(current_rank, historical_rank)


def _direction(current_rank: int, historical_rank: int) -> str:
    if historical_rank <= 0:
        return "no_historical_baseline"
    if current_rank >= historical_rank + 2:
        return "much_higher"
    if current_rank > historical_rank:
        return "higher"
    if current_rank <= historical_rank - 2:
        return "much_lower"
    if current_rank < historical_rank:
        return "lower"
    return "stable"


def _has_reversal_signal(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
) -> bool:
    if not historical_entries:
        return False
    current_max_risk = max(_risk_rank(item.risk_level.value) for item in topic_items)
    historical_max_risk = max(_risk_rank(entry.get("risk_level")) for entry in historical_entries)
    current_max_opportunity = max(
        _opportunity_rank(item.opportunity_level.value) for item in topic_items
    )
    historical_max_opportunity = max(
        _opportunity_rank(entry.get("opportunity_level")) for entry in historical_entries
    )
    if current_max_risk >= 3 and historical_max_risk <= 1:
        return True
    if current_max_opportunity <= 1 and historical_max_opportunity >= 3:
        return True
    return False


def _current_is_heating_up(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
) -> bool:
    if not historical_entries:
        return len(topic_items) >= 2 or max(item.importance_score for item in topic_items) >= 85
    current_peak = max(item.importance_score for item in topic_items)
    historical_peak = max(_int(entry.get("importance_score")) for entry in historical_entries)
    return len(topic_items) >= 2 or current_peak >= historical_peak + 10


def _current_is_cooling_down(
    topic_items: list[StructuredNewsItem],
    historical_entries: list[dict[str, Any]],
) -> bool:
    if len(historical_entries) < 2:
        return False
    current_peak = max(item.importance_score for item in topic_items)
    historical_peak = max(_int(entry.get("importance_score")) for entry in historical_entries)
    return len(topic_items) == 1 and historical_peak >= current_peak + 15


def _trend_state_title(trend_state: TrendState) -> str:
    labels = {
        TrendState.NEW: "新信号出现",
        TrendState.CONTINUING: "延续跟踪",
        TrendState.HEATING_UP: "热度上升",
        TrendState.COOLING_DOWN: "热度回落",
        TrendState.REVERSING: "方向反转",
    }
    return labels[trend_state]


def _trend_state_summary(trend_state: TrendState) -> str:
    summaries = {
        TrendState.NEW: "这是本期的新主题信号，暂未形成明确历史延续。",
        TrendState.CONTINUING: "历史记忆显示该主题近期已有相关事件，本期属于延续跟踪。",
        TrendState.HEATING_UP: "结合本期集中度、重要性评分和历史参照，该主题呈现升温迹象。",
        TrendState.COOLING_DOWN: "相较历史高点，本期信号较少或强度较弱，呈现回落迹象。",
        TrendState.REVERSING: "本期风险或机会方向与历史记忆出现明显变化，需要重点复核。",
    }
    return summaries[trend_state]


def _risk_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


def _opportunity_rank(value: Any) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(str(value or "").lower(), 0)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _risk_insights(items: list[StructuredNewsItem]) -> list[RiskInsight]:
    risky = sorted(
        [item for item in items if item.risk_level.value in {"medium", "high"}],
        key=lambda item: item.importance_score,
        reverse=True,
    )
    if not risky:
        risky = sorted(items, key=lambda item: item.importance_score, reverse=True)[:1]
    return [
        RiskInsight(
            title=f"{item.topic} 风险关注",
            level=item.risk_level,
            summary=f"{item.title} 的风险提示：{item.risk_rationale}",
            evidence_item_ids=[item.id],
            evidence_sources=item.evidence_sources[:1],
        )
        for item in risky[:3]
    ]


def _opportunity_insights(items: list[StructuredNewsItem]) -> list[OpportunityInsight]:
    opportunities = sorted(
        [item for item in items if item.opportunity_level.value in {"medium", "high"}],
        key=lambda item: item.importance_score,
        reverse=True,
    )
    return [
        OpportunityInsight(
            title=f"{item.topic} 机会窗口",
            level=item.opportunity_level,
            summary=f"{item.title} 的机会提示：{item.opportunity_rationale}",
            evidence_item_ids=[item.id],
            evidence_sources=item.evidence_sources[:1],
        )
        for item in opportunities[:3]
    ]


def _repair_risk_opportunity_insights(
    context: PipelineContext,
    report: DailyInsightReport,
    items: list[StructuredNewsItem],
) -> DailyInsightReport:
    """Drop report risk/opportunity insights that conflict with item labels."""

    items_by_id = {item.id: item for item in items}
    removed: list[dict[str, Any]] = []
    repaired_risks = _repair_level_insights(
        report.risk_insights,
        items_by_id,
        label_field="risk_level",
        section="risk_insights",
        removed=removed,
    )
    repaired_opportunities = _repair_level_insights(
        report.opportunity_insights,
        items_by_id,
        label_field="opportunity_level",
        section="opportunity_insights",
        removed=removed,
    )
    if not removed:
        return report

    repaired = report.model_copy(
        update={
            "risk_insights": repaired_risks,
            "opportunity_insights": repaired_opportunities,
        }
    )
    repair_report = {
        "audit_type": "risk_opportunity_repair",
        "run_id": context.run_id,
        "checked_count": len(report.risk_insights) + len(report.opportunity_insights),
        "removed_count": len(removed),
        "removed_records": removed,
    }
    write_llm_audit_report(context, repair_report)
    context.set("risk_opportunity_repair", repair_report)
    return repaired


def _repair_level_insights(
    insights: list[RiskInsight] | list[OpportunityInsight],
    items_by_id: dict[str, StructuredNewsItem],
    *,
    label_field: str,
    section: str,
    removed: list[dict[str, Any]],
) -> list[RiskInsight] | list[OpportunityInsight]:
    repaired: list[RiskInsight] | list[OpportunityInsight] = []
    for index, insight in enumerate(insights, start=1):
        kept_ids: list[str] = []
        blocked_ids: list[str] = []
        unknown_ids: list[str] = []
        for item_id in insight.evidence_item_ids:
            item = items_by_id.get(item_id)
            if item is None:
                unknown_ids.append(item_id)
                continue
            level = getattr(item, label_field).value
            if insight.level.value in {"medium", "high"} and level not in {"medium", "high"}:
                blocked_ids.append(item_id)
                continue
            kept_ids.append(item_id)

        kept_sources = [
            source
            for source in insight.evidence_sources
            if _source_matches_kept_item(source.source_item_id, kept_ids)
        ]
        if not kept_ids or not kept_sources:
            removed.append(
                {
                    "section": section,
                    "index": index,
                    "title": insight.title,
                    "level": insight.level.value,
                    "reason": "no matching evidence items remain after level consistency repair",
                    "removed_item_ids": [*blocked_ids, *unknown_ids],
                }
            )
            continue
        if blocked_ids or unknown_ids:
            removed.append(
                {
                    "section": section,
                    "index": index,
                    "title": insight.title,
                    "level": insight.level.value,
                    "reason": "removed inconsistent or unknown evidence item references",
                    "removed_item_ids": [*blocked_ids, *unknown_ids],
                }
            )
            insight = insight.model_copy(
                update={
                    "evidence_item_ids": kept_ids,
                    "evidence_sources": kept_sources,
                }
            )
        repaired.append(insight)
    return repaired


def _source_matches_kept_item(source_item_id: str, kept_ids: list[str]) -> bool:
    candidates = {source_item_id}
    if source_item_id.startswith("raw-"):
        candidates.add(source_item_id.replace("raw-", "structured-", 1))
    if source_item_id.startswith("structured-"):
        candidates.add(source_item_id.replace("structured-", "raw-", 1))
    return any(item_id in candidates for item_id in kept_ids)


def _chart_refs(context: PipelineContext) -> list[str]:
    charts = context.get("chart_refs")
    if isinstance(charts, list):
        return [str(chart) for chart in charts]

    report_path = path_for(context, "report_sections")
    if report_path.exists():
        payload = read_json(report_path)
        refs = payload.get("chart_refs", []) if isinstance(payload, dict) else []
        return [str(ref) for ref in refs]
    return []


def _validate_analysis_skills(
    context: PipelineContext,
    items: list[StructuredNewsItem],
    report_path: Path,
) -> None:
    validated_path = _validated_skill_input_path(context, items, report_path)
    try:
        runner = SkillRunner()
        runner.validate(
            TREND_ANALYSIS_SKILL,
            validated_path,
            report_path,
            None,
            context=context,
        )
        runner.validate(
            RISK_DETECTION_SKILL,
            validated_path,
            report_path,
            context=context,
        )
    finally:
        if context.get("analysis_skill_temporary_validated_path") == str(validated_path):
            validated_path.unlink(missing_ok=True)


def _validated_skill_input_path(
    context: PipelineContext,
    items: list[StructuredNewsItem],
    report_path: Path,
) -> Path:
    validated_path = path_for(context, "validated")
    if validated_path.exists() and context.get("validated_items") is None:
        return validated_path

    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        prefix="skill_validated_",
        dir=report_path.parent,
        delete=False,
    )
    with temporary:
        json.dump(model_list_payload(items), temporary, ensure_ascii=False, indent=2)
    context.set("analysis_skill_temporary_validated_path", temporary.name)
    return Path(temporary.name)
