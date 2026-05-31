"""AI news relevance gate for cleaned report-date items."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.harness import PipelineContext, SkillRunner, SkillSpec
from src.schemas import CleanNewsItem, RelevanceAssessment

from .utils import (
    active_llm_adapter,
    LLMBusinessError,
    llm_call_with_business_retries,
    llm_max_concurrency,
    model_list_payload,
    parallel_map_ordered,
    parse_llm_json,
    path_for,
    read_json,
    record_llm_business_error,
    record_llm_fallback,
    requires_real_llm,
    write_json,
)

RELEVANCE_PASS_THRESHOLD = 70
RELEVANCE_SKILL = SkillSpec(
    name="ai_news_relevance",
    references=(
        "references/output_schema.md",
        "references/relevance_guidelines.md",
        "references/examples.json",
    ),
    validator_script="scripts/validate_relevance_assessment.py",
    context_key="relevance_skill_validation",
)


@dataclass(frozen=True)
class _RelevanceItemResult:
    assessment: RelevanceAssessment
    calls: list[dict[str, object]]
    error: LLMBusinessError | None = None

AI_RELEVANCE_KEYWORDS: tuple[str, ...] = (
    "ai",
    "aigc",
    "artificial intelligence",
    "generative ai",
    "人工智能",
    "生成式ai",
    "生成式人工智能",
    "llm",
    "large language model",
    "大模型",
    "foundation model",
    "基座模型",
    "agent",
    "agents",
    "智能体",
    "openai",
    "anthropic",
    "deepmind",
    "deepseek",
    "深度求索",
    "gemini",
    "claude",
    "llama",
    "mistral",
    "hugging face",
    "昇腾",
    "chatgpt",
    "gpt",
    "gpu",
    "算力",
    "inference",
    "推理",
    "training",
    "训练",
    "model serving",
    "模型服务",
    "copilot",
    "multimodal",
    "多模态",
    "prompt",
    "reasoning",
    "推理模型",
    "transformer",
)

AI_ENTITY_KEYWORDS: tuple[str, ...] = (
    "openai",
    "anthropic",
    "google deepmind",
    "deepmind",
    "deepseek",
    "mistral",
    "cohere",
    "hugging face",
    "perplexity",
    "nvidia",
    "xai",
    "英伟达",
    "华为昇腾",
    "昇腾",
    "百度文心",
    "阿里通义",
    "通义千问",
    "腾讯混元",
    "字节豆包",
    "月之暗面",
    "智谱",
    "商汤",
    "百川智能",
    "零一万物",
)

AI_TECH_KEYWORDS: tuple[str, ...] = (
    "generative ai",
    "llm",
    "large language model",
    "foundation model",
    "chatgpt",
    "gpt",
    "claude",
    "gemini",
    "llama",
    "agent",
    "agents",
    "copilot",
    "multimodal",
    "reasoning model",
    "vision-language",
    "machine learning",
    "deep learning",
    "neural network",
    "transformer",
    "人工智能",
    "生成式ai",
    "生成式人工智能",
    "大模型",
    "语言模型",
    "基座模型",
    "基础模型",
    "多模态",
    "视觉语言",
    "智能体",
    "ai助手",
    "ai 助手",
    "推理模型",
    "机器学习",
    "深度学习",
    "神经网络",
    "模型推理",
    "模型训练",
)

DIRECT_AI_KEYWORDS: tuple[str, ...] = (
    "ai",
    "aigc",
    "artificial intelligence",
    "人工智能",
    "生成式ai",
    "生成式人工智能",
    *AI_ENTITY_KEYWORDS,
    *AI_TECH_KEYWORDS,
)

WEAK_AI_KEYWORDS: tuple[str, ...] = (
    "ai",
    "aigc",
    "artificial intelligence",
    "人工智能",
    "生成式ai",
    "生成式人工智能",
    "model",
    "models",
    "模型",
)

INFRASTRUCTURE_KEYWORDS: tuple[str, ...] = (
    "gpu",
    "tpu",
    "npu",
    "accelerator",
    "ai chip",
    "inference",
    "training",
    "model serving",
    "serving framework",
    "vllm",
    "transformers",
    "cuda",
    "cann",
    "rocm",
    "blackwell",
    "h100",
    "b200",
    "gb200",
    "ascend",
    "ai cloud",
    "compute cluster",
    "gpu cluster",
    "datacenter",
    "data center",
    "数据中心",
    "memory bandwidth",
    "hbm",
    "all-reduce",
    "ai芯片",
    "ai 芯片",
    "智算",
    "智算中心",
    "算力",
    "算力集群",
    "训练集群",
    "推理集群",
    "推理框架",
    "推理引擎",
    "模型服务",
    "模型部署",
    "显存",
    "高带宽内存",
    "昇腾",
    "寒武纪",
    "海光",
    "ai服务器",
    "ai 服务器",
    "云服务",
    "通用云服务",
)

AI_WORKLOAD_KEYWORDS: tuple[str, ...] = (
    "model training",
    "model inference",
    "model serving",
    "llm serving",
    "training cluster",
    "inference workload",
    "ai workload",
    "ai training",
    "ai inference",
    "gpu utilization",
    "llm deployment",
    "token",
    "tokens",
    "模型训练",
    "模型推理",
    "大模型训练",
    "大模型推理",
    "推理部署",
    "模型服务",
    "大模型服务",
    "llm服务",
    "llm 服务",
    "ai工作负载",
    "ai 工作负载",
    "训练任务",
    "推理任务",
    "token",
    "tokens",
)

POLICY_KEYWORDS: tuple[str, ...] = (
    "policy",
    "regulation",
    "regulatory",
    "governance",
    "law",
    "lawsuit",
    "copyright",
    "safety",
    "audit",
    "audits",
    "compliance",
    "evaluation",
    "eval",
    "content label",
    "content labeling",
    "watermark",
    "deepfake",
    "政策",
    "监管",
    "治理",
    "法规",
    "法律",
    "诉讼",
    "版权",
    "安全",
    "审计",
    "合规",
    "评测",
    "备案",
    "内容标识",
    "水印",
    "深度合成",
    "深度伪造",
)

AI_POLICY_TARGET_KEYWORDS: tuple[str, ...] = (
    "ai act",
    "ai model",
    "ai models",
    "ai system",
    "ai systems",
    "ai-generated",
    "ai generated",
    "generative ai",
    "synthetic media",
    "deepfake",
    "model safety",
    "model audit",
    "model evaluation",
    "training data",
    "generated content",
    "content label",
    "content labeling",
    "watermark",
    "人工智能法",
    "ai法案",
    "ai 法案",
    "ai模型",
    "ai 模型",
    "ai系统",
    "ai 系统",
    "人工智能系统",
    "生成式ai",
    "生成式 ai",
    "生成式人工智能",
    "生成式人工智能服务",
    "大模型",
    "模型安全",
    "模型评测",
    "算法备案",
    "模型备案",
    "训练数据",
    "生成内容",
    "内容标识",
    "深度合成",
    "深度伪造",
)

RESEARCH_KEYWORDS: tuple[str, ...] = (
    "paper",
    "research",
    "researchers",
    "benchmark",
    "dataset",
    "architecture",
    "method",
    "study",
    "arxiv",
    "experiment",
    "论文",
    "研究",
    "研究人员",
    "基准",
    "基准测试",
    "数据集",
    "架构",
    "方法",
    "实验",
    "评测",
)

TECHNICAL_RESEARCH_KEYWORDS: tuple[str, ...] = (
    *DIRECT_AI_KEYWORDS,
    "benchmark",
    "dataset",
    "architecture",
    "alignment",
    "rlhf",
    "fine-tuning",
    "pretraining",
    "post-training",
    "reasoning",
    "vision-language",
    "大模型",
    "多模态",
    "智能体",
    "推理",
    "推理模型",
    "对齐",
    "微调",
    "预训练",
    "后训练",
    "模型架构",
    "基准测试",
    "数据集",
    "模型评测",
)

PRODUCT_KEYWORDS: tuple[str, ...] = (
    "launch",
    "launches",
    "unveil",
    "unveils",
    "unveiled",
    "release",
    "released",
    "introduce",
    "introduced",
    "announce",
    "announced",
    "preview",
    "product",
    "platform",
    "api",
    "发布",
    "推出",
    "上线",
    "预览",
    "产品",
    "平台",
    "接口",
)

AI_PRODUCT_CAPABILITY_KEYWORDS: tuple[str, ...] = (
    "assistant",
    "copilot",
    "agent",
    "agents",
    "workflow",
    "workflows",
    "developer",
    "助手",
    "ai助手",
    "ai 助手",
    "智能体",
    "工作流",
    "开发者",
    "企业工作流",
    "模型api",
    "模型 api",
)

STRONG_PRODUCT_AI_KEYWORDS: tuple[str, ...] = (
    "agent",
    "agents",
    "copilot",
    "assistant",
    "api",
    "developer",
    "workflow",
    "enterprise",
    "llm",
    "large language model",
    "foundation model",
    "chatgpt",
    "claude",
    "gemini",
    "generative ai",
    "大模型",
    "语言模型",
    "基座模型",
    "智能体",
    "助手",
    "ai助手",
    "开发者",
    "工作流",
    "企业",
    "生成式ai",
    "生成式人工智能",
)

BUSINESS_KEYWORDS: tuple[str, ...] = (
    "funding",
    "valuation",
    "revenue",
    "ipo",
    "acquisition",
    "partnership",
    "investment",
    "compute deal",
    "cloud deal",
    "adoption",
    "enterprise deal",
    "融资",
    "估值",
    "营收",
    "收入",
    "上市",
    "ipo",
    "收购",
    "合作",
    "投资",
    "算力订单",
    "云服务订单",
    "商业化",
    "企业客户",
    "采用",
)

BUSINESS_AI_TARGET_KEYWORDS: tuple[str, ...] = (
    *AI_ENTITY_KEYWORDS,
    *AI_TECH_KEYWORDS,
    "ai company",
    "ai startup",
    "model company",
    "model lab",
    "ai lab",
    "ai platform",
    "compute spend",
    "training compute",
    "inference spend",
    "ai公司",
    "ai 公司",
    "ai创业公司",
    "ai 创业公司",
    "大模型公司",
    "模型公司",
    "模型实验室",
    "ai实验室",
    "ai 实验室",
    "ai平台",
    "ai 平台",
    "算力支出",
    "训练算力",
    "推理成本",
    "推理支出",
)

NEGATIVE_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "game",
    "gaming",
    "movie",
    "music",
    "sports",
    "fashion",
    "celebrity",
    "travel",
    "restaurant",
    "smartphone",
    "phone",
    "camera",
    "laptop",
    "pc",
    "tv",
    "appliance",
    "car",
    "ev",
    "游戏",
    "影视",
    "电影",
    "音乐",
    "体育",
    "时尚",
    "明星",
    "旅行",
    "餐厅",
    "手机",
    "相机",
    "拍照",
    "电脑",
    "笔记本",
    "电视",
    "家电",
    "汽车",
    "电动车",
    "座舱",
    "数据中心",
    "通用云服务",
    "数据库",
    "存储",
    "企业应用托管",
)

NEGATED_AI_CONTEXT_PATTERNS: tuple[str, ...] = (
    r"没有明确的?.{0,12}(大模型|模型|ai|人工智能).{0,12}(训练|推理|工作负载|任务|部署|服务)",
    r"(ai|人工智能|大模型).{0,12}(只是|仅是|仅作为|顺带|附带|下游用途|小功能)",
    r"(不是|并非).{0,12}(监管对象|核心产品能力|主要事件|核心能力)",
)


def run(context: PipelineContext) -> list[CleanNewsItem]:
    """Filter cleaned same-day items to only AI-relevant daily report items."""

    cleaned_items = _cleaned_items(context)
    adapter = active_llm_adapter(context)
    strict_llm = requires_real_llm(context)
    if adapter is None:
        assessments = [_rule_based_assessment(item) for item in cleaned_items]
        context.set("relevance_mode", "rule_based")
    else:
        assessments = _llm_assess(context, adapter, cleaned_items, strict_llm=strict_llm)
        context.set(
            "relevance_mode",
            "llm_fallback" if context.get("relevance_llm_fallbacks") else "llm",
        )

    accepted_ids = {
        assessment.item_id
        for assessment in assessments
        if assessment.is_ai_related and assessment.relevance_score >= RELEVANCE_PASS_THRESHOLD
    }
    relevant_items = [item for item in cleaned_items if item.id in accepted_ids]

    relevant_path = path_for(context, "relevant")
    report_path = path_for(context, "relevance_report")
    write_json(relevant_path, model_list_payload(relevant_items))
    write_json(report_path, model_list_payload(assessments))
    SkillRunner().validate(RELEVANCE_SKILL, report_path, context=context)

    context.add_artifact("relevant", relevant_path)
    context.add_artifact("relevance_report", report_path)
    context.set("relevance_assessments", assessments)
    context.set("relevant_items", relevant_items)
    context.set("relevant_count", len(relevant_items))

    if not relevant_items:
        raise ValueError("no AI-relevant report-date news items remain after relevance filtering")

    return relevant_items


def _cleaned_items(context: PipelineContext) -> list[CleanNewsItem]:
    items = context.get("cleaned_items")
    if items is None:
        payload = read_json(path_for(context, "cleaned"))
        if not isinstance(payload, list):
            raise ValueError(f"{path_for(context, 'cleaned')} must contain a JSON array")
        items = payload
    return [
        item if isinstance(item, CleanNewsItem) else CleanNewsItem.model_validate(item)
        for item in items
    ]


def _rule_based_assessment(item: CleanNewsItem) -> RelevanceAssessment:
    text = " ".join([item.title, item.summary, item.content]).lower()
    signal = _best_relevance_signal(text)
    matched = list(signal["terms"])
    score = int(signal["score"])
    is_ai_related = score >= RELEVANCE_PASS_THRESHOLD
    if is_ai_related:
        reason = str(signal["accept_reason"])
        evidence = _evidence_lines(item, matched)
    else:
        reason = str(signal["reject_reason"])
        evidence = _evidence_lines(item, matched)

    return RelevanceAssessment(
        item_id=item.id,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        content_hash=item.content_hash,
        is_ai_related=is_ai_related,
        relevance_score=score,
        relevance_reason=reason,
        relevance_evidence=evidence,
        decision_source="rule_based",
    )


def _best_relevance_signal(text: str) -> dict[str, object]:
    direct_ai = _matched_terms(text, DIRECT_AI_KEYWORDS)
    weak_ai = _matched_terms(text, WEAK_AI_KEYWORDS)
    negative_hits = _matched_terms(text, NEGATIVE_CONTEXT_KEYWORDS)
    negated_ai_context = _has_negated_ai_context(text)
    signals: list[dict[str, object]] = []

    infra_terms = _matched_terms(text, INFRASTRUCTURE_KEYWORDS)
    workload_terms = _matched_terms(text, AI_WORKLOAD_KEYWORDS)
    infra_ai_terms = _matched_terms(text, AI_TECH_KEYWORDS + AI_ENTITY_KEYWORDS)
    if infra_terms and workload_terms and not negated_ai_context:
        signals.append(
            _signal(
                "AI infrastructure",
                88,
                infra_terms + workload_terms + infra_ai_terms,
                "The item is centrally about AI infrastructure for training, inference, model serving, or AI compute workloads.",
                "The infrastructure signal is not explicit enough to prove the main event is about AI workloads.",
            )
        )
    elif infra_terms and (weak_ai or direct_ai or workload_terms):
        signals.append(
            _signal(
                "AI-adjacent infrastructure",
                58,
                infra_terms + weak_ai,
                "",
                "The item mentions AI near infrastructure or hardware, but does not show a concrete AI workload such as training, inference, or model serving.",
            )
        )

    policy_terms = _matched_terms(text, POLICY_KEYWORDS)
    ai_policy_target_terms = _matched_terms(text, AI_POLICY_TARGET_KEYWORDS)
    if policy_terms and ai_policy_target_terms and not negated_ai_context:
        signals.append(
            _signal(
                "AI policy and governance",
                86,
                policy_terms + ai_policy_target_terms + direct_ai,
                "The item is centrally about AI governance, safety, regulation, audits, model risk, generated content, or AI-related legal exposure.",
                "The policy signal is not specific enough to AI models, AI products, or generated content.",
            )
        )
    elif policy_terms:
        signals.append(
            _signal(
                "general policy",
                42,
                policy_terms + weak_ai,
                "",
                "The item is about general policy or compliance and does not clearly target AI systems, models, or generated content.",
            )
        )

    research_terms = _matched_terms(text, RESEARCH_KEYWORDS)
    technical_terms = _matched_terms(text, TECHNICAL_RESEARCH_KEYWORDS)
    if research_terms and technical_terms:
        signals.append(
            _signal(
                "AI research",
                87,
                research_terms + technical_terms,
                "The item is centrally about AI research, a model method, benchmark, dataset, architecture, or technical evaluation.",
                "The research signal is not specific enough to an AI method, model, benchmark, or dataset.",
            )
        )
    elif research_terms and weak_ai:
        signals.append(
            _signal(
                "AI-used research",
                56,
                research_terms + weak_ai,
                "",
                "The item appears to use AI as a tool or side reference rather than reporting an AI research contribution.",
            )
        )

    product_terms = _matched_terms(text, PRODUCT_KEYWORDS)
    product_capability_terms = _matched_terms(text, AI_PRODUCT_CAPABILITY_KEYWORDS)
    strong_product_terms = _matched_terms(text, STRONG_PRODUCT_AI_KEYWORDS)
    product_ai_terms = _matched_terms(text, AI_TECH_KEYWORDS + AI_ENTITY_KEYWORDS)
    if product_terms and (product_ai_terms or strong_product_terms):
        consumer_only = negative_hits and not strong_product_terms
        signals.append(
            _signal(
                "AI product",
                62 if consumer_only else 86,
                product_terms
                + product_ai_terms
                + product_capability_terms
                + strong_product_terms
                + negative_hits,
                "The item is centrally about an AI product, model API, agent, copilot, developer workflow, or enterprise AI capability.",
                "The item looks like a consumer or general product story where AI is a minor feature, marketing phrase, or side capability.",
            )
        )
    elif product_terms and (weak_ai or direct_ai):
        signals.append(
            _signal(
                "AI-adjacent product",
                55,
                product_terms + weak_ai + direct_ai + negative_hits,
                "",
                "The item is a product or platform story, but the AI signal is too generic to show a core AI capability.",
            )
        )

    business_terms = _matched_terms(text, BUSINESS_KEYWORDS)
    business_ai_terms = _matched_terms(text, BUSINESS_AI_TARGET_KEYWORDS)
    if business_terms and business_ai_terms:
        signals.append(
            _signal(
                "AI business",
                80,
                business_terms + business_ai_terms,
                "The item is centrally about AI industry commercialization, financing, adoption, compute spend, or business momentum.",
                "The business signal does not show that AI is the main event or primary impact.",
            )
        )

    strong_direct_ai = _matched_terms(text, AI_TECH_KEYWORDS + AI_ENTITY_KEYWORDS)
    if not signals and strong_direct_ai:
        signals.append(
            _signal(
                "direct AI mention",
                58 if negative_hits or negated_ai_context else 72,
                strong_direct_ai + negative_hits,
                "The item directly discusses AI technology or a named AI lab/product.",
                "The item has an AI mention, but the available evidence is too broad or incidental for the daily report.",
            )
        )

    if not signals and weak_ai:
        signals.append(
            _signal(
                "weak AI mention",
                45,
                weak_ai + negative_hits,
                "",
                "The item uses broad AI or model language without enough evidence that AI is the central event.",
            )
        )

    if not signals:
        return _signal(
            "out of scope",
            20 if negative_hits else 30,
            negative_hits,
            "",
            "The item does not provide enough direct AI-tech evidence for the daily report.",
        )

    return max(signals, key=lambda item: int(item["score"]))


def _signal(
    category: str,
    score: int,
    terms: Iterable[str],
    accept_reason: str,
    reject_reason: str,
) -> dict[str, object]:
    unique_terms = _unique_terms(terms)
    return {
        "category": category,
        "score": score,
        "terms": unique_terms,
        "accept_reason": accept_reason,
        "reject_reason": reject_reason,
    }


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    return [term for term in terms if term and _term_in_text(text, term)]


def _term_in_text(text: str, term: str) -> bool:
    escaped = re.escape(term.strip().lower())
    if not escaped:
        return False
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None


def _has_negated_ai_context(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in NEGATED_AI_CONTEXT_PATTERNS)


def _unique_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        normalized = term.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(term)
    return unique


def _llm_assess(
    context: PipelineContext,
    adapter: object,
    cleaned_items: list[CleanNewsItem],
    *,
    strict_llm: bool = False,
) -> list[RelevanceAssessment]:
    def assess_item(item: CleanNewsItem) -> RelevanceAssessment:
        item_calls: list[dict[str, object]] = []
        prompt = _llm_prompt([item])
        try:
            assessment, _ = llm_call_with_business_retries(
                context,
                adapter,
                prompt,
                operation="relevance item",
                call_metadata={"item_id": item.id, "scope": "single"},
                parse_result=lambda content: _parse_single_relevance(content, item),
                calls=item_calls,
            )
            return _RelevanceItemResult(assessment=assessment, calls=item_calls)
        except LLMBusinessError as error:
            if strict_llm:
                raise error
            fallback = _rule_based_assessment(item)
            return _RelevanceItemResult(
                assessment=fallback,
                calls=item_calls,
                error=error,
            )

    results = parallel_map_ordered(
        cleaned_items,
        assess_item,
        max_workers=llm_max_concurrency(context, len(cleaned_items)),
    )
    assessments: list[RelevanceAssessment] = []
    calls: list[dict[str, object]] = []
    for result in results:
        assessments.append(result.assessment)
        calls.extend(result.calls)
        if result.error is not None:
            record_llm_business_error(context, "relevance", result.error)
            record_llm_fallback(
                context,
                "relevance",
                reason=str(result.error),
                error_type=result.error.error_type,
                item_id=result.assessment.item_id,
            )

    if calls:
        context.set("relevance_llm_calls", calls)
        context.set("relevance_llm_call", _combined_llm_call(calls))
    return assessments


def _parse_single_relevance(
    content: str,
    item: CleanNewsItem,
) -> RelevanceAssessment:
    try:
        parsed = parse_llm_json(content, f"relevance item {item.id}")
    except ValueError as exc:
        raise LLMBusinessError("invalid_json", str(exc)) from exc

    if isinstance(parsed, list):
        if len(parsed) != 1:
            raise LLMBusinessError(
                "item_count_mismatch",
                f"relevance item {item.id} returned {len(parsed)} items for 1 input",
                details={"item_id": item.id, "returned_count": len(parsed), "expected_count": 1},
            )
        parsed = parsed[0]
    elif not isinstance(parsed, dict):
        raise LLMBusinessError(
            "schema_error",
            f"relevance item {item.id} must return an object or one-item JSON array",
            details={"item_id": item.id},
        )

    try:
        return RelevanceAssessment.model_validate(parsed)
    except Exception as exc:
        raise LLMBusinessError(
            "schema_error",
            f"relevance item {item.id} failed schema validation: {exc}",
            details={"item_id": item.id},
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


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _llm_prompt(cleaned_items: list[CleanNewsItem]) -> str:
    payload = model_list_payload(cleaned_items)
    skill_context = SkillRunner().load_context(RELEVANCE_SKILL)
    instructions = {
        "task": "Decide whether each cleaned news item belongs in an AI technology daily report.",
        "pass_rule": (
            "Set is_ai_related=true only when the item is clearly about AI technology, AI products, "
            "AI labs, AI models, AI infrastructure, AI governance, or AI industry developments."
        ),
        "fail_rule": (
            "Fail closed. Reject borderline items whose AI relevance is vague, incidental, or only "
            "mentioned in passing."
        ),
        "threshold": RELEVANCE_PASS_THRESHOLD,
        "required_fields": [
            "item_id",
            "title",
            "url",
            "published_at",
            "content_hash",
            "is_ai_related",
            "relevance_score",
            "relevance_reason",
            "relevance_evidence",
            "decision_source",
        ],
        "output": "Return a JSON array with one assessment object per input item.",
    }
    return json.dumps(
        {
            "instructions": instructions,
            "skill_context": skill_context,
            "items": payload,
        },
        ensure_ascii=False,
        indent=2,
    )


def _evidence_lines(item: CleanNewsItem, terms: Iterable[str]) -> list[str]:
    candidates = [item.title, item.summary, item.content]
    lowered_terms = [term.lower() for term in terms if str(term).strip()]
    evidence: list[str] = []
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        if lowered_terms and any(term in text.lower() for term in lowered_terms):
            evidence.append(_trim(text))
        elif not lowered_terms and len(evidence) < 2:
            evidence.append(_trim(text))
        if len(evidence) >= 2:
            break
    if evidence:
        return evidence
    fallback = item.summary.strip() or item.title.strip()
    return [_trim(fallback)] if fallback else []


def _trim(value: str, max_len: int = 220) -> str:
    return value if len(value) <= max_len else f"{value[: max_len - 3]}..."
