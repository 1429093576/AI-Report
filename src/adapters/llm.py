"""LLM adapter interfaces and provider implementations."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

import requests

from src.schemas import SchemaBase

from .llm_pricing import ModelPricing, estimate_llm_cost_usd, pricing_table_from_config

DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://api.openai.com/v1"
DEFAULT_LLM_MODEL = "gpt-5-mini"
REQUIRED_LLM_MODES = {"llm", "openai_compatible", "real"}


@dataclass(frozen=True)
class LLMResult:
    """Provider-neutral model response metadata."""

    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_ms: int = 0
    cost_usd: float = 0.0
    error: str | None = None
    parsed: Any = None
    raw_response: Any = None

    @property
    def total_tokens(self) -> int:
        """Return prompt and completion tokens together."""

        return self.prompt_tokens + self.completion_tokens

    @property
    def success(self) -> bool:
        """Return whether the adapter call completed without an error."""

        return self.error is None


class LLMAdapter(ABC):
    """Base interface for model providers."""

    @abstractmethod
    def generate(self, prompt: str, schema: type | None = None) -> LLMResult:
        """Generate a model response for a prompt."""


MockResponseResolver = Callable[[str, type | None], Any]


class MockLLMAdapter(LLMAdapter):
    """Deterministic LLM adapter for offline tests and local MVP runs."""

    def __init__(
        self,
        responses: Iterable[Any] | Mapping[str, Any] | MockResponseResolver | None = None,
        *,
        default_response: Any = "mock response",
        model: str = "mock-llm",
    ) -> None:
        self.model = model
        self.default_response = default_response
        self.prompts: list[str] = []
        self._cursor = 0
        self._resolver: MockResponseResolver | None = None
        self._responses_by_prompt: dict[str, Any] | None = None
        self._responses: list[Any] | None = None

        if callable(responses):
            self._resolver = responses
        elif isinstance(responses, Mapping):
            self._responses_by_prompt = dict(responses)
        elif responses is not None:
            self._responses = list(responses)

    def generate(self, prompt: str, schema: type | None = None) -> LLMResult:
        """Return a deterministic response and optional schema-validated payload."""

        start = perf_counter()
        self.prompts.append(prompt)
        raw_response: Any = None
        content = ""
        parsed: Any = None
        error: str | None = None

        try:
            raw_response = self._next_response(prompt, schema)
            if schema is None:
                content = _stringify(raw_response)
            else:
                parsed = _validate_with_schema(raw_response, schema)
                content = _stringify(parsed)
        except Exception as exc:
            error = f"{exc.__class__.__name__}: {exc}"
            content = _stringify(raw_response) if raw_response is not None else ""

        elapsed_ms = max(0, round((perf_counter() - start) * 1000))
        return LLMResult(
            content=content,
            model=self.model,
            prompt_tokens=_estimate_tokens(prompt),
            completion_tokens=_estimate_tokens(content),
            elapsed_ms=elapsed_ms,
            cost_usd=0.0,
            error=error,
            parsed=parsed,
            raw_response=raw_response,
        )

    def _next_response(self, prompt: str, schema: type | None) -> Any:
        if self._resolver is not None:
            return self._resolver(prompt, schema)
        if self._responses_by_prompt is not None:
            return self._responses_by_prompt.get(prompt, self.default_response)
        if self._responses is not None and self._cursor < len(self._responses):
            response = self._responses[self._cursor]
            self._cursor += 1
            return response
        return self.default_response


class OpenAICompatibleLLMAdapter(LLMAdapter):
    """LLM adapter for OpenAI-compatible Chat Completions APIs."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_LLM_MODEL,
        base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        temperature: float = 0.2,
        timeout_seconds: float = 30.0,
        max_retries: int = 0,
        provider: str = "OpenAI",
        pricing_table: Iterable[ModelPricing] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = _require_non_empty("api_key", api_key)
        self.model = _require_non_empty("model", model)
        self.provider = _require_non_empty("provider", provider)
        self.base_url = _require_non_empty("base_url", base_url)
        self.chat_completions_url = _chat_completions_url(self.base_url)
        self.temperature = float(temperature)
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.pricing_table = (
            tuple(pricing_table)
            if pricing_table is not None
            else pricing_table_from_config()
        )
        self.session = session if session is not None else requests.Session()

    def generate(self, prompt: str, schema: type | None = None) -> LLMResult:
        """Generate a response with a Chat Completions-compatible endpoint."""

        start = perf_counter()
        raw_response: Any = None
        content = ""
        parsed: Any = None
        error: str | None = None
        prompt_tokens = 0
        completion_tokens = 0
        cost_usd = 0.0

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    self.chat_completions_url,
                    headers=self._headers(),
                    json=self._payload(prompt),
                    timeout=self.timeout_seconds,
                )
                raw_response = self._response_payload(response)
                if not response.ok:
                    error = self._http_error(response, raw_response)
                    if attempt < self.max_retries and response.status_code >= 500:
                        continue
                    break

                content = _extract_chat_completion_content(raw_response)
                usage = raw_response.get("usage", {}) if isinstance(raw_response, dict) else {}
                prompt_tokens = _int_value(usage.get("prompt_tokens"))
                completion_tokens = _int_value(usage.get("completion_tokens"))
                cost_usd = estimate_llm_cost_usd(
                    provider=self.provider,
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    pricing_table=self.pricing_table,
                )

                if schema is not None:
                    parsed = _validate_with_schema(content, schema)
                    content = _stringify(parsed)
                error = None
                break
            except Exception as exc:
                error = f"{exc.__class__.__name__}: {exc}"
                if attempt >= self.max_retries:
                    break

        elapsed_ms = max(0, round((perf_counter() - start) * 1000))
        return LLMResult(
            content=content,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            elapsed_ms=elapsed_ms,
            cost_usd=cost_usd,
            error=error,
            parsed=parsed,
            raw_response=raw_response,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }

    @staticmethod
    def _response_payload(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return {"text": response.text}

    @staticmethod
    def _http_error(response: requests.Response, raw_response: Any) -> str:
        message = ""
        if isinstance(raw_response, dict):
            error_payload = raw_response.get("error")
            if isinstance(error_payload, dict):
                message = str(error_payload.get("message") or "")
            elif error_payload:
                message = str(error_payload)
            elif raw_response.get("text"):
                message = str(raw_response["text"])
        message = message.strip() or response.reason or "HTTP request failed"
        return f"HTTP {response.status_code}: {message}"


def create_llm_adapter(config: Mapping[str, Any] | None = None) -> LLMAdapter:
    """Create the configured LLM adapter.

    Explicit real-LLM modes fail fast when credentials are missing. Local and
    legacy auto/offline runs can still fall back to the deterministic adapter.
    """

    llm_config = _llm_config(config)
    api_key = _config_or_env(llm_config, "api_key", "LLM_API_KEY")
    if not api_key:
        mode = _llm_mode(config)
        if mode in REQUIRED_LLM_MODES:
            raise ValueError(
                f"LLM_API_KEY is required when LLM mode is '{mode}'"
            )
        return MockLLMAdapter()

    return OpenAICompatibleLLMAdapter(
        api_key=api_key,
        model=_config_or_env(llm_config, "model", "LLM_MODEL", DEFAULT_LLM_MODEL),
        base_url=_config_or_env(
            llm_config,
            "base_url",
            "LLM_BASE_URL",
            DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
        ),
        temperature=_float_config(llm_config, "temperature", 0.2),
        timeout_seconds=_float_config(llm_config, "timeout_seconds", 30.0),
        max_retries=_int_config(llm_config, "max_retries", 0),
        provider=_config_or_env(llm_config, "provider", "LLM_PROVIDER", "OpenAI"),
        pricing_table=pricing_table_from_config(llm_config.get("pricing")),
    )


def _validate_with_schema(value: Any, schema: type) -> Any:
    payload = _loads_json_if_possible(value)
    if not issubclass(schema, SchemaBase):
        raise TypeError("schema must be a SchemaBase subclass")
    return schema.model_validate(payload)


def _loads_json_if_possible(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str)


def _estimate_tokens(value: str) -> int:
    stripped = value.strip()
    if not stripped:
        return 0
    return max(1, len(stripped.split()))


def _extract_chat_completion_content(raw_response: Any) -> str:
    if not isinstance(raw_response, dict):
        raise ValueError("LLM response must be a JSON object")
    choices = raw_response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("LLM response missing choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("LLM response choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("LLM response choice missing message")
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("LLM response message content must be a string")
    return content


def _llm_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if config is None:
        return {}
    llm_config = config.get("llm")
    if isinstance(llm_config, Mapping):
        return llm_config
    return config


def _llm_mode(config: Mapping[str, Any] | None) -> str:
    if config is not None:
        mode_config = config.get("mode")
        if isinstance(mode_config, Mapping) and mode_config.get("llm") is not None:
            return str(mode_config["llm"]).strip().lower()
        if isinstance(mode_config, str):
            return mode_config.strip().lower()

        llm_config = config.get("llm")
        if isinstance(llm_config, Mapping) and llm_config.get("mode") is not None:
            return str(llm_config["mode"]).strip().lower()

    return os.getenv("LLM_MODE", "").strip().lower()


def _config_or_env(
    config: Mapping[str, Any],
    config_key: str,
    env_key: str,
    default: str = "",
) -> str:
    value = config.get(config_key)
    if value is None:
        value = os.getenv(env_key)
    if value is None:
        value = default
    return str(value).strip()


def _float_config(config: Mapping[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    return float(value)


def _int_config(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    return int(value)


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must not be empty")
    return normalized


def _chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"
