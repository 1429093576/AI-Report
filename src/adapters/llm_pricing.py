"""Model pricing helpers for LLM cost estimates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelPricing:
    """Per-model token pricing for cost estimates."""

    provider: str
    model: str
    input_price: float
    output_price: float
    unit_tokens: int = 1000
    currency: str = "USD"

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_non_empty("provider", self.provider))
        object.__setattr__(self, "model", _require_non_empty("model", self.model))
        object.__setattr__(self, "currency", _require_non_empty("currency", self.currency))
        object.__setattr__(self, "input_price", float(self.input_price))
        object.__setattr__(self, "output_price", float(self.output_price))
        object.__setattr__(self, "unit_tokens", int(self.unit_tokens))
        if self.input_price < 0:
            raise ValueError("input_price must be non-negative")
        if self.output_price < 0:
            raise ValueError("output_price must be non-negative")
        if self.unit_tokens <= 0:
            raise ValueError("unit_tokens must be positive")

    def estimate_cost_usd(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate USD cost for prompt and completion tokens."""

        prompt_cost = max(0, int(prompt_tokens)) / self.unit_tokens * self.input_price
        completion_cost = (
            max(0, int(completion_tokens)) / self.unit_tokens * self.output_price
        )
        return round(prompt_cost + completion_cost, 10)


def estimate_llm_cost_usd(
    *,
    provider: str | None,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_table: Iterable[ModelPricing] | None = None,
) -> float:
    """Estimate model call cost, returning 0.0 when pricing is unknown."""

    pricing = find_model_pricing(
        provider=provider,
        model=model,
        pricing_table=pricing_table,
    )
    if pricing is None:
        return 0.0
    return pricing.estimate_cost_usd(prompt_tokens, completion_tokens)


def find_model_pricing(
    *,
    provider: str | None,
    model: str,
    pricing_table: Iterable[ModelPricing] | None = None,
) -> ModelPricing | None:
    """Find pricing by provider and model, falling back to model-only match."""

    normalized_model = _normalize_key(model)
    if not normalized_model:
        return None

    table = tuple(pricing_table) if pricing_table is not None else DEFAULT_LLM_PRICING
    normalized_provider = _normalize_key(provider or "")
    if normalized_provider:
        for pricing in table:
            if (
                _normalize_key(pricing.provider) == normalized_provider
                and _normalize_key(pricing.model) == normalized_model
            ):
                return pricing

    for pricing in table:
        if _normalize_key(pricing.model) == normalized_model:
            return pricing
    return None


def pricing_table_from_config(config: Any = None) -> tuple[ModelPricing, ...]:
    """Return default pricing merged with optional config pricing entries."""

    pricing_by_key = {
        (_normalize_key(pricing.provider), _normalize_key(pricing.model)): pricing
        for pricing in DEFAULT_LLM_PRICING
    }
    for pricing in _pricing_entries_from_config(config):
        pricing_by_key[(_normalize_key(pricing.provider), _normalize_key(pricing.model))] = (
            pricing
        )
    return tuple(pricing_by_key.values())


def _pricing_entries_from_config(config: Any) -> tuple[ModelPricing, ...]:
    if config is None:
        return ()

    entries: list[ModelPricing] = []
    if isinstance(config, Mapping):
        if _looks_like_pricing_entry(config):
            entries.append(_pricing_from_mapping(config, provider="OpenAI", model=None))
        else:
            for key, value in config.items():
                if not isinstance(value, Mapping):
                    continue
                if _looks_like_pricing_entry(value):
                    entries.append(
                        _pricing_from_mapping(value, provider="OpenAI", model=str(key))
                    )
                    continue
                provider = str(key)
                for model, payload in value.items():
                    if isinstance(payload, Mapping) and _looks_like_pricing_entry(payload):
                        entries.append(
                            _pricing_from_mapping(
                                payload,
                                provider=provider,
                                model=str(model),
                            )
                        )
    elif isinstance(config, Iterable) and not isinstance(config, (str, bytes)):
        for value in config:
            if isinstance(value, Mapping) and _looks_like_pricing_entry(value):
                entries.append(_pricing_from_mapping(value, provider="OpenAI", model=None))

    return tuple(entries)


def _pricing_from_mapping(
    payload: Mapping[str, Any],
    *,
    provider: str,
    model: str | None,
) -> ModelPricing:
    resolved_provider = str(payload.get("provider") or provider)
    resolved_model = str(payload.get("model") or model or "")
    input_price = _float_field(
        payload,
        (
            "input_price",
            "input_per_1k",
            "input_price_per_1000",
            "input_per_1000",
            "input_per_1m",
            "input_price_per_1m",
        ),
    )
    output_price = _float_field(
        payload,
        (
            "output_price",
            "output_per_1k",
            "output_price_per_1000",
            "output_per_1000",
            "output_per_1m",
            "output_price_per_1m",
        ),
    )
    return ModelPricing(
        provider=resolved_provider,
        model=resolved_model,
        input_price=input_price,
        output_price=output_price,
        unit_tokens=_unit_tokens(payload),
        currency=str(payload.get("currency") or "USD"),
    )


def _looks_like_pricing_entry(payload: Mapping[str, Any]) -> bool:
    return any(
        key in payload
        for key in (
            "input_price",
            "output_price",
            "input_per_1k",
            "output_per_1k",
            "input_price_per_1000",
            "output_price_per_1000",
            "input_per_1000",
            "output_per_1000",
            "input_per_1m",
            "output_per_1m",
            "input_price_per_1m",
            "output_price_per_1m",
        )
    )


def _float_field(payload: Mapping[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if payload.get(key) is not None:
            return float(payload[key])
    raise ValueError(f"pricing entry missing one of: {', '.join(keys)}")


def _unit_tokens(payload: Mapping[str, Any]) -> int:
    if payload.get("unit_tokens") is not None:
        return int(payload["unit_tokens"])
    if payload.get("tokens") is not None:
        return int(payload["tokens"])
    if any(key in payload for key in ("input_per_1m", "output_per_1m")):
        return 1_000_000
    if any(key in payload for key in ("input_price_per_1m", "output_price_per_1m")):
        return 1_000_000
    return 1000


def _normalize_key(value: str) -> str:
    return str(value).strip().lower()


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


DEFAULT_LLM_PRICING: tuple[ModelPricing, ...] = (
    ModelPricing(
        provider="OpenAI",
        model="gpt-5-mini",
        input_price=0.0005,
        output_price=0.004,
        unit_tokens=1000,
        currency="USD",
    ),
)
