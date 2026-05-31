"""Debug an OpenAI-compatible LLM API using local environment settings.

This script intentionally avoids printing secrets. It checks the configured
chat completions endpoint directly, then optionally probes the models endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

import requests
import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-5-mini"


def main() -> int:
    args = _parse_args()
    _load_env(args.env_file)
    config = _load_config(PROJECT_ROOT / "config" / "pipeline.yaml")
    settings = _settings(config, args)

    _print_settings(settings)
    if not settings["api_key"]:
        print("ERROR: LLM_API_KEY is empty or missing after loading environment.")
        return 2

    chat_result = _request_chat(settings)
    _print_chat_result(chat_result)

    if args.list_models:
        models_result = _request_models(settings)
        _print_models_result(models_result)

    return 0 if chat_result["ok"] else 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Debug OpenAI-compatible LLM API connectivity and auth.",
    )
    parser.add_argument("--env-file", default=".env", help="Env file to load.")
    parser.add_argument("--base-url", help="Override LLM_BASE_URL.")
    parser.add_argument("--model", help="Override LLM_MODEL.")
    parser.add_argument("--provider", help="Override LLM_PROVIDER.")
    parser.add_argument("--timeout", type=float, default=None, help="Request timeout seconds.")
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Also call GET /models when supported by the provider.",
    )
    return parser.parse_args()


def _load_env(path_value: str) -> None:
    env_path = Path(path_value)
    if not env_path.is_absolute():
        env_path = PROJECT_ROOT / env_path
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"{path.as_posix()} must contain a mapping")
    return payload


def _settings(config: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    llm_config = config.get("llm", {})
    if not isinstance(llm_config, dict):
        llm_config = {}

    api_key = _env_or_config("LLM_API_KEY", llm_config, "api_key", "")
    base_url = args.base_url or _env_or_config(
        "LLM_BASE_URL",
        llm_config,
        "base_url",
        DEFAULT_BASE_URL,
    )
    model = args.model or _env_or_config("LLM_MODEL", llm_config, "model", DEFAULT_MODEL)
    provider = args.provider or _env_or_config("LLM_PROVIDER", llm_config, "provider", "OpenAI")
    timeout = args.timeout
    if timeout is None:
        timeout = float(llm_config.get("timeout_seconds", 30.0))

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "timeout_seconds": timeout,
        "chat_url": _endpoint_url(base_url, "chat/completions"),
        "models_url": _endpoint_url(base_url, "models"),
    }


def _env_or_config(
    env_key: str,
    config: dict[str, Any],
    config_key: str,
    default: str,
) -> str:
    value = os.getenv(env_key)
    if value is None:
        value = config.get(config_key, default)
    return str(value or "").strip()


def _endpoint_url(base_url: str, suffix: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith(f"/{suffix}"):
        return normalized
    if suffix == "models" and normalized.endswith("/chat/completions"):
        normalized = normalized[: -len("/chat/completions")]
    return f"{normalized}/{suffix}"


def _print_settings(settings: dict[str, Any]) -> None:
    print("LLM API debug settings")
    print(f"provider: {settings['provider']}")
    print(f"model: {settings['model']}")
    print(f"base_url: {settings['base_url']}")
    print(f"chat_url: {settings['chat_url']}")
    print(f"api_key: {_redact_key(settings['api_key'])}")
    print(f"timeout_seconds: {settings['timeout_seconds']}")


def _request_chat(settings: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": settings["model"],
        "messages": [
            {
                "role": "user",
                "content": "Reply with exactly: pong",
            }
        ],
        "temperature": 0,
    }
    return _request(
        "POST",
        settings["chat_url"],
        settings,
        json_payload=payload,
    )


def _request_models(settings: dict[str, Any]) -> dict[str, Any]:
    return _request("GET", settings["models_url"], settings)


def _request(
    method: str,
    url: str,
    settings: dict[str, Any],
    *,
    json_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {settings['api_key']}",
                "Content-Type": "application/json",
            },
            json=json_payload,
            timeout=float(settings["timeout_seconds"]),
        )
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        body = _response_body(response)
        return {
            "ok": response.ok,
            "status_code": response.status_code,
            "reason": response.reason,
            "elapsed_ms": elapsed_ms,
            "headers": _safe_headers(response.headers),
            "body": body,
            "error_summary": _error_summary(body),
        }
    except Exception as exc:
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        return {
            "ok": False,
            "status_code": None,
            "reason": None,
            "elapsed_ms": elapsed_ms,
            "headers": {},
            "body": None,
            "error_summary": f"{exc.__class__.__name__}: {exc}",
        }


def _response_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _safe_headers(headers: requests.structures.CaseInsensitiveDict[str]) -> dict[str, str]:
    allowed = {
        "content-type",
        "date",
        "server",
        "x-request-id",
        "openai-processing-ms",
        "ratelimit-limit-requests",
        "ratelimit-remaining-requests",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
    }
    safe: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in allowed:
            safe[key] = value
    return safe


def _error_summary(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            error_type = str(error.get("type") or "").strip()
            code = str(error.get("code") or "").strip()
            parts = [part for part in (error_type, code, message) if part]
            return " | ".join(parts)
        if error:
            return str(error)
    if isinstance(body, str):
        return body[:500].replace("\n", " ").strip()
    return ""


def _print_chat_result(result: dict[str, Any]) -> None:
    print("")
    print("Chat completions result")
    print(f"ok: {result['ok']}")
    print(f"status_code: {result['status_code']}")
    print(f"reason: {result['reason']}")
    print(f"elapsed_ms: {result['elapsed_ms']}")
    if result["headers"]:
        print(f"headers: {json.dumps(result['headers'], ensure_ascii=False)}")
    if result["error_summary"]:
        print(f"error_summary: {result['error_summary']}")

    body = result["body"]
    if isinstance(body, dict) and result["ok"]:
        print(f"response_preview: {_chat_content_preview(body)}")
        print(f"usage: {json.dumps(body.get('usage', {}), ensure_ascii=False)}")
    elif body is not None:
        print(f"body_preview: {_preview(body)}")


def _print_models_result(result: dict[str, Any]) -> None:
    print("")
    print("Models result")
    print(f"ok: {result['ok']}")
    print(f"status_code: {result['status_code']}")
    print(f"reason: {result['reason']}")
    print(f"elapsed_ms: {result['elapsed_ms']}")
    if result["error_summary"]:
        print(f"error_summary: {result['error_summary']}")

    body = result["body"]
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        ids = [str(item.get("id")) for item in body["data"][:20] if isinstance(item, dict)]
        print(f"model_count_seen: {len(body['data'])}")
        print(f"model_ids_preview: {', '.join(ids)}")
    elif body is not None:
        print(f"body_preview: {_preview(body)}")


def _chat_content_preview(body: dict[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return "<missing choices>"
    first = choices[0]
    if not isinstance(first, dict):
        return "<invalid first choice>"
    message = first.get("message")
    if not isinstance(message, dict):
        return "<missing message>"
    return _preview(message.get("content", ""))


def _preview(value: Any, max_chars: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    text = text.replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _redact_key(value: str) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 8:
        return "<set-but-too-short>"
    return f"{value[:4]}...{value[-4:]} (len={len(value)})"


if __name__ == "__main__":
    raise SystemExit(main())
