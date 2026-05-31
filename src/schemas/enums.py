"""schema 模型共享的枚举定义。

枚举用于约束下游模块可见的标签集合，避免自由文本在流程中逐渐漂移。
当来源数据不足时，优先使用 ``unknown`` 或 ``other``，不要在业务代码里
临时发明新值。
"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """新闻条目的来源类型。"""

    NEWS = "news"
    BLOG = "blog"
    RESEARCH = "research"
    SOCIAL = "social"
    RELEASE = "release"
    FORUM = "forum"
    UNKNOWN = "unknown"


class Language(StrEnum):
    """用于过滤、聚合和 Prompt 路由的语言分类。"""

    ZH = "zh"
    EN = "en"
    OTHER = "other"
    UNKNOWN = "unknown"


class EventType(StrEnum):
    """从清洗后新闻中抽取出的主要事件类型。"""

    PRODUCT_LAUNCH = "product_launch"
    FUNDING = "funding"
    POLICY = "policy"
    RESEARCH = "research"
    CONTROVERSY = "controversy"
    PARTNERSHIP = "partnership"
    MARKET = "market"
    SECURITY = "security"
    MODEL_RELEASE = "model_release"
    OTHER = "other"


class Sentiment(StrEnum):
    """结构化新闻对应的整体舆情倾向。"""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ImpactScope(StrEnum):
    """事件主要影响的范围。"""

    TECHNOLOGY = "technology"
    INDUSTRY = "industry"
    CAPITAL = "capital"
    POLICY = "policy"
    USER = "user"
    ECOSYSTEM = "ecosystem"
    SECURITY = "security"
    OTHER = "other"


class RiskLevel(StrEnum):
    """用于排序和报告生成的标准化风险等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class OpportunityLevel(StrEnum):
    """用于排序和报告生成的标准化机会等级。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class TrendState(StrEnum):
    """Memory-aware trend state used by daily trend analysis."""

    NEW = "new"
    CONTINUING = "continuing"
    HEATING_UP = "heating_up"
    COOLING_DOWN = "cooling_down"
    REVERSING = "reversing"


class ValidationSeverity(StrEnum):
    """schema 或业务规则校验问题的严重程度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ChartType(StrEnum):
    """可视化描述中支持的图表类型。"""

    BAR = "bar"
    LINE = "line"
    PIE = "pie"
    SCATTER = "scatter"
    TABLE = "table"
