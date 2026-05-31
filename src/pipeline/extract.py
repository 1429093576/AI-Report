"""Structured extraction pipeline step."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from src.harness import PipelineContext, SkillRunner, SkillSpec
from src.schemas import (
    AI_NEWS_TOPICS,
    CleanNewsItem,
    EventType,
    ImpactScope,
    OpportunityLevel,
    RiskLevel,
    Sentiment,
    StructuredNewsItem,
)

from .utils import (
    active_llm_adapter,
    LLMBusinessError,
    llm_call_with_business_retries,
    llm_max_concurrency,
    load_prompt_template,
    model_list_payload,
    parallel_map_ordered,
    parse_llm_json,
    path_for,
    record_llm_business_error,
    record_llm_fallback,
    require_json_list,
    requires_real_llm,
    write_json,
)

NEWS_EXTRACTION_SKILL = SkillSpec(
    name="news_extraction",
    references=(
        "references/structured_news_schema.md",
        "references/extraction_guidelines.md",
        "references/examples.json",
    ),
    validator_script="scripts/validate_structured_news.py",
    context_key="news_extraction_skill_validation",
)


@dataclass(frozen=True)
class _ExtractItemResult:
    item: StructuredNewsItem
    calls: list[dict[str, object]]
    error: LLMBusinessError | None = None

TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI Safety and Governance", ("safety", "policy", "governance", "regulation", "risk", "lawsuit", "privacy", "audit")),
    ("AI Research", ("paper", "research", "benchmark", "study", "arxiv", "algorithm", "experiment")),
    ("AI Agents", ("agent", "agents", "autonomous", "copilot")),
    ("Foundation Models", ("foundation model", "frontier model", "model release", "new model", "gemini", "claude", "gpt", "llama", "mistral", "multimodal", "reasoning")),
    ("Developer Tools and Open Source", ("open source", "open-source", "hugging face", "open-weight", "github", "sdk", "framework", "developer tool", "vllm", "ollama", "langchain")),
    ("AI Infrastructure", ("gpu", "nvidia", "blackwell", "cloud", "chip", "training", "inference", "data center", "hbm")),
    ("AI Applications", ("chatgpt", "workspace", "health", "search", "productivity", "education", "design", "consumer")),
    ("AI Business and Market", ("funding", "investment", "valuation", "acquisition", "revenue", "market", "customer", "partnership", "enterprise adoption")),
]

ENTITY_HINTS = (
    "OpenAI",
    "Google",
    "DeepMind",
    "Anthropic",
    "Microsoft",
    "NVIDIA",
    "Meta",
    "Mistral",
    "Hugging Face",
    "Perplexity",
    "Cohere",
    "Amazon",
    "Apple",
    "Adobe",
)


def run(context: PipelineContext) -> list[StructuredNewsItem]:
    """Extract structured news items with LLM or deterministic offline rules."""

    cleaned_items = _cleaned_items(context)
    adapter = active_llm_adapter(context)
    strict_llm = requires_real_llm(context)
    if adapter is None:
        structured = [_structure_item(item) for item in cleaned_items]
        context.set("extract_mode", "rule_based")
    else:
        structured = _llm_extract(context, adapter, cleaned_items, strict_llm=strict_llm)
        context.set(
            "extract_mode",
            "llm_fallback" if context.get("extract_llm_fallbacks") else "llm",
        )

    output_path = path_for(context, "structured")
    write_json(output_path, model_list_payload(structured))
    SkillRunner().validate(NEWS_EXTRACTION_SKILL, output_path, context=context)
    context.add_artifact("structured", output_path)
    context.set("structured_items", structured)
    context.set("structured_count", len(structured))
    if not structured:
        raise ValueError("extract failed: no structured items produced after LLM fallback")
    return structured


def _cleaned_items(context: PipelineContext) -> list[CleanNewsItem]:
    items = context.get("relevant_items")
    if items is None:
        relevant_path = path_for(context, "relevant")
        if relevant_path.exists():
            items = require_json_list(relevant_path)
    if items is None:
        raise ValueError(
            "extract requires AI-relevant input from relevance; "
            "run relevance first or provide the relevant artifact"
        )
    return [
        item if isinstance(item, CleanNewsItem) else CleanNewsItem.model_validate(item)
        for item in items
    ]


def _llm_extract(
    context: PipelineContext,
    adapter: object,
    cleaned_items: list[CleanNewsItem],
    *,
    strict_llm: bool = False,
) -> list[StructuredNewsItem]:
    template = load_prompt_template(
        context,
        "extract_schema",
        "prompts/extract_schema.md",
    )
    template = SkillRunner().apply_prompt_context(template, [NEWS_EXTRACTION_SKILL])

    def extract_item(item: CleanNewsItem) -> _ExtractItemResult:
        item_calls: list[dict[str, object]] = []
        prompt = _extract_item_prompt(template, item)
        try:
            structured_item, _ = llm_call_with_business_retries(
                context,
                adapter,
                prompt,
                operation="extract item",
                call_metadata={"item_id": item.id, "scope": "single"},
                parse_result=lambda content: _parse_single_structured_item(content, item),
                calls=item_calls,
            )
            return _ExtractItemResult(item=structured_item, calls=item_calls)
        except LLMBusinessError as error:
            if strict_llm:
                raise error
            return _ExtractItemResult(
                item=_structure_item(item),
                calls=item_calls,
                error=error,
            )

    results = parallel_map_ordered(
        cleaned_items,
        extract_item,
        max_workers=llm_max_concurrency(context, len(cleaned_items)),
    )
    structured: list[StructuredNewsItem] = []
    calls: list[dict[str, object]] = []
    for result in results:
        structured.append(result.item)
        calls.extend(result.calls)
        if result.error is not None:
            record_llm_business_error(context, "extract", result.error)
            record_llm_fallback(
                context,
                "extract",
                reason=str(result.error),
                error_type=result.error.error_type,
                item_id=result.item.id.replace("structured-", "raw-"),
                details={"fallback_structured_id": result.item.id},
            )
    if calls:
        context.set("extract_llm_calls", calls)
    return structured


def _extract_item_prompt(template: str, item: CleanNewsItem) -> str:
    payload = model_list_payload([item])
    prompt = template.replace(
        "{{clean_news_items_json}}",
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return prompt


def _parse_single_structured_item(
    content: str,
    source_item: CleanNewsItem,
) -> StructuredNewsItem:
    try:
        parsed = parse_llm_json(content, f"extract item {source_item.id}")
    except ValueError as exc:
        raise LLMBusinessError(
            "invalid_json",
            str(exc),
            details={"item_id": source_item.id},
        ) from exc

    if isinstance(parsed, list):
        if len(parsed) != 1:
            raise LLMBusinessError(
                "item_count_mismatch",
                f"extract item {source_item.id} returned {len(parsed)} items for 1 input",
                details={
                    "item_id": source_item.id,
                    "returned_count": len(parsed),
                    "expected_count": 1,
                },
            )
        parsed = parsed[0]
    elif not isinstance(parsed, dict):
        raise LLMBusinessError(
            "schema_error",
            f"extract item {source_item.id} must return an object or one-item JSON array",
            details={"item_id": source_item.id},
        )

    try:
        return StructuredNewsItem.model_validate(parsed)
    except Exception as exc:
        raise LLMBusinessError(
            "schema_error",
            f"extract item {source_item.id} failed schema validation: {exc}",
            details={"item_id": source_item.id},
        ) from exc


def _structure_item(item: CleanNewsItem) -> StructuredNewsItem:
    text = " ".join([item.title, item.summary, item.content])
    lower = text.lower()
    event_type = _event_type(lower, item.source_type.value)
    topic = _topic(lower)
    entities = _entities(text)
    impact_scope = _impact_scope(lower, event_type)
    risk_level = _risk_level(lower, event_type)
    opportunity_level = _opportunity_level(lower, event_type)
    sentiment = _sentiment(lower, risk_level, opportunity_level)
    importance_score = _importance_score(item, lower, event_type, risk_level)
    summary = item.summary or _sentence(item.content) or item.title
    evidence = _evidence(item)

    return StructuredNewsItem(
        id=item.id.replace("raw-", "structured-"),
        title=item.title,
        source=item.source,
        url=item.url,
        published_at=item.published_at,
        source_type=item.source_type,
        language=item.language,
        topic=topic,
        entities=entities,
        event_type=event_type,
        summary=summary,
        key_points=_key_points(item),
        sentiment=sentiment,
        impact_scope=impact_scope,
        importance_score=importance_score,
        importance_rationale=_importance_rationale(
            topic,
            event_type,
            impact_scope,
            importance_score,
            evidence,
        ),
        risk_level=risk_level,
        risk_rationale=_risk_rationale(risk_level, event_type, impact_scope, evidence),
        opportunity_level=opportunity_level,
        opportunity_rationale=_opportunity_rationale(
            opportunity_level,
            event_type,
            impact_scope,
            evidence,
        ),
        evidence=evidence,
        evidence_sources=_evidence_sources(item, summary),
        content_hash=item.content_hash,
    )


def _topic(lower: str) -> str:
    for topic, keywords in TOPIC_RULES:
        if any(keyword in lower for keyword in keywords):
            return topic
    return AI_NEWS_TOPICS[-1]


def _event_type(lower: str, source_type: str) -> EventType:
    if any(word in lower for word in ("funding", "investment", "valuation")):
        return EventType.FUNDING
    if any(word in lower for word in ("policy", "regulation", "governance", "law")):
        return EventType.POLICY
    if any(word in lower for word in ("paper", "research", "benchmark", "study")):
        return EventType.RESEARCH
    if any(word in lower for word in ("security", "safety", "risk", "misuse")):
        return EventType.SECURITY
    if any(word in lower for word in ("partnership", "partner", "collaboration")):
        return EventType.PARTNERSHIP
    if any(word in lower for word in ("controversy", "lawsuit", "complaint")):
        return EventType.CONTROVERSY
    if any(word in lower for word in ("release", "launch", "introduce", "announce", "preview")):
        if "model" in lower or source_type == "release":
            return EventType.MODEL_RELEASE
        return EventType.PRODUCT_LAUNCH
    return EventType.MARKET


def _impact_scope(lower: str, event_type: EventType) -> ImpactScope:
    if event_type == EventType.POLICY:
        return ImpactScope.POLICY
    if event_type == EventType.FUNDING:
        return ImpactScope.CAPITAL
    if event_type == EventType.SECURITY:
        return ImpactScope.SECURITY
    if any(word in lower for word in ("developer", "api", "model", "benchmark", "gpu")):
        return ImpactScope.TECHNOLOGY
    if any(word in lower for word in ("enterprise", "business", "customer", "market")):
        return ImpactScope.INDUSTRY
    if any(word in lower for word in ("user", "consumer", "chatgpt", "workspace")):
        return ImpactScope.USER
    return ImpactScope.ECOSYSTEM


def _risk_level(lower: str, event_type: EventType) -> RiskLevel:
    high_terms = ("lawsuit", "ban", "breach", "misuse", "security risk", "safety risk")
    medium_terms = ("risk", "safety", "regulation", "policy", "concern", "privacy")
    if event_type in {EventType.CONTROVERSY, EventType.SECURITY} or any(
        term in lower for term in high_terms
    ):
        return RiskLevel.HIGH
    if event_type == EventType.POLICY or any(term in lower for term in medium_terms):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _opportunity_level(lower: str, event_type: EventType) -> OpportunityLevel:
    high_terms = ("developer", "enterprise", "open source", "gpu", "agent", "productivity")
    if event_type in {
        EventType.MODEL_RELEASE,
        EventType.PRODUCT_LAUNCH,
        EventType.PARTNERSHIP,
    } or any(term in lower for term in high_terms):
        return OpportunityLevel.HIGH
    if event_type in {EventType.RESEARCH, EventType.MARKET}:
        return OpportunityLevel.MEDIUM
    return OpportunityLevel.LOW


def _sentiment(
    lower: str,
    risk_level: RiskLevel,
    opportunity_level: OpportunityLevel,
) -> Sentiment:
    if risk_level == RiskLevel.HIGH and opportunity_level == OpportunityLevel.HIGH:
        return Sentiment.MIXED
    if risk_level == RiskLevel.HIGH:
        return Sentiment.NEGATIVE
    if any(word in lower for word in ("concern", "risk", "scrutiny", "controversy")):
        return Sentiment.MIXED
    if opportunity_level == OpportunityLevel.HIGH:
        return Sentiment.POSITIVE
    return Sentiment.NEUTRAL


def _importance_score(
    item: CleanNewsItem,
    lower: str,
    event_type: EventType,
    risk_level: RiskLevel,
) -> int:
    score = 50
    if item.source_type.value in {"blog", "release", "research"}:
        score += 8
    if event_type in {EventType.MODEL_RELEASE, EventType.PRODUCT_LAUNCH}:
        score += 14
    if event_type in {EventType.POLICY, EventType.SECURITY}:
        score += 12
    if event_type == EventType.PARTNERSHIP:
        score += 8
    if any(word in lower for word in ("google", "openai", "anthropic", "microsoft", "nvidia", "meta")):
        score += 10
    if any(word in lower for word in ("developer", "enterprise", "agent", "frontier", "gpu")):
        score += 6
    if risk_level == RiskLevel.HIGH:
        score += 8
    elif risk_level == RiskLevel.MEDIUM:
        score += 4
    return min(score, 100)


def _importance_rationale(
    topic: str,
    event_type: EventType,
    impact_scope: ImpactScope,
    importance_score: int,
    evidence: list[str],
) -> str:
    reason = _evidence_hint(evidence)
    scope = _impact_scope_phrase(impact_scope)
    event = _event_type_phrase(event_type)
    if importance_score >= 85:
        return f"{topic} 的{event}直接影响{scope}，{reason}"
    if importance_score >= 70:
        return f"{topic} 的{event}已显示明确{scope}影响，{reason}"
    if importance_score >= 50:
        return f"{topic} 的{event}具备跟踪价值，但影响范围仍需验证"
    return f"当前证据有限，{topic} 相关影响仍不清晰"


def _risk_rationale(
    risk_level: RiskLevel,
    event_type: EventType,
    impact_scope: ImpactScope,
    evidence: list[str],
) -> str:
    reason = _evidence_hint(evidence)
    if risk_level == RiskLevel.HIGH:
        return f"{_event_type_phrase(event_type)}涉及{_impact_scope_phrase(impact_scope)}高风险，{reason}"
    if risk_level == RiskLevel.MEDIUM:
        return f"{_event_type_phrase(event_type)}仍有{_impact_scope_phrase(impact_scope)}不确定性，{reason}"
    if risk_level == RiskLevel.UNKNOWN:
        return "未知：来源未给出足够风险信息，需等待进一步披露"
    return f"低：材料未显示突出风险，主要证据集中在{_impact_scope_phrase(impact_scope)}进展"


def _opportunity_rationale(
    opportunity_level: OpportunityLevel,
    event_type: EventType,
    impact_scope: ImpactScope,
    evidence: list[str],
) -> str:
    reason = _evidence_hint(evidence)
    if opportunity_level == OpportunityLevel.HIGH:
        return f"{_event_type_phrase(event_type)}强化{_impact_scope_phrase(impact_scope)}机会，{reason}"
    if opportunity_level == OpportunityLevel.MEDIUM:
        return f"{_event_type_phrase(event_type)}可能带来{_impact_scope_phrase(impact_scope)}机会，但落地效果待观察"
    if opportunity_level == OpportunityLevel.UNKNOWN:
        return "未知：来源未给出足够机会信号，需等待更多采用或商业化证据"
    return f"低：新闻重点不在机会扩张，{_impact_scope_phrase(impact_scope)}增量有限"


def _event_type_phrase(event_type: EventType) -> str:
    return {
        EventType.PRODUCT_LAUNCH: "产品发布",
        EventType.FUNDING: "融资事件",
        EventType.POLICY: "政策事件",
        EventType.RESEARCH: "研究进展",
        EventType.CONTROVERSY: "争议事件",
        EventType.PARTNERSHIP: "合作事件",
        EventType.MARKET: "市场动态",
        EventType.SECURITY: "安全事件",
        EventType.MODEL_RELEASE: "模型发布",
        EventType.OTHER: "事件",
    }.get(event_type, "事件")


def _impact_scope_phrase(impact_scope: ImpactScope) -> str:
    return {
        ImpactScope.TECHNOLOGY: "技术能力",
        ImpactScope.INDUSTRY: "产业部署",
        ImpactScope.CAPITAL: "资本市场",
        ImpactScope.POLICY: "政策合规",
        ImpactScope.USER: "用户体验",
        ImpactScope.ECOSYSTEM: "生态协作",
        ImpactScope.SECURITY: "安全治理",
        ImpactScope.OTHER: "相关方向",
    }.get(impact_scope, "相关方向")


def _evidence_hint(evidence: list[str]) -> str:
    text = " ".join(evidence).lower()
    if not text.strip():
        return "可引用证据仍较有限"
    signals = [
        (("developer", "workflow", "coding", "api", "sdk"), "来源提到开发者工作流"),
        (("enterprise", "customer", "business", "deployment"), "来源提到企业部署或客户需求"),
        (("gpu", "chip", "infrastructure", "data center", "inference", "training"), "来源提到算力或基础设施"),
        (("model", "benchmark", "reasoning", "multimodal"), "来源提到模型能力或评测"),
        (("policy", "regulation", "law", "governance", "compliance"), "来源提到监管或合规要求"),
        (("risk", "safety", "security", "privacy", "misuse", "breach"), "来源提到安全或隐私风险"),
        (("partnership", "partner", "collaboration"), "来源提到合作部署"),
        (("open source", "open-source", "github", "framework"), "来源提到开源或开发者生态"),
        (("funding", "investment", "valuation", "acquisition"), "来源提到融资或资本动作"),
    ]
    for keywords, label in signals:
        if any(keyword in text for keyword in keywords):
            return label
    return "来源给出可追踪事件事实"


def _entities(text: str) -> list[str]:
    entities = [entity for entity in ENTITY_HINTS if re.search(rf"\b{re.escape(entity)}\b", text)]
    return entities or ["AI industry"]


def _key_points(item: CleanNewsItem) -> list[str]:
    candidates = [_sentence(item.summary), _sentence(item.content), item.title]
    points: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in points:
            points.append(candidate)
        if len(points) == 3:
            break
    return points or [item.title]


def _evidence(item: CleanNewsItem) -> list[str]:
    evidence = _sentence(item.content) or item.summary or item.title
    return [evidence]


def _evidence_sources(item: CleanNewsItem, claim: str) -> list[dict[str, str]]:
    for field in ("content", "summary", "title"):
        quote = _sentence(str(getattr(item, field, "") or ""))
        if quote:
            return [
                {
                    "source_item_id": item.id,
                    "evidence_field": field,
                    "evidence_quote": quote,
                    "claim": claim,
                }
            ]
    return []


def _sentence(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    parts = re.split(r"(?<=[.!?。！？])\s+", value)
    return parts[0].strip()
