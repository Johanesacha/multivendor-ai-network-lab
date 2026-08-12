"""
llm_client.py — Shared Anthropic Claude client.

Single source of truth for "call Claude and get text back", used by app.py
(local-LLM cascade's final fallback), pydantic_ai_orchestrator.py, and
eval_harness.py. Previously each of these three modules had its own copy of
this logic (construct an anthropic.Anthropic client, call messages.create,
extract text + usage). No Flask dependency — safe to import from anywhere.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

_EMPTY_USAGE = {"input": 0, "output": 0}

# Model registry — single source of truth for "which provider serves this
# model_id, under what API model name, at what price". Used by query() below
# to compare models (Claude vs local Ollama) on the same eval scenarios.
#
# Prices are $/million tokens; Ollama models run locally so cost is a known
# 0.0, not an absence of data (hence float, not None).
MODEL_REGISTRY: dict[str, dict[str, Any]] = {
    "claude-haiku-4-5": {
        "provider": "anthropic",
        "api_model": "claude-haiku-4-5-20251001",
        "price_in_per_mtok": 1.00,
        "price_out_per_mtok": 5.00,
    },
    "qwen2.5:3b": {
        "provider": "ollama",
        "api_model": "qwen2.5:3b",
        "price_in_per_mtok": 0.0,
        "price_out_per_mtok": 0.0,
    },
    "llama3.2:3b": {
        "provider": "ollama",
        "api_model": "llama3.2:3b",
        "price_in_per_mtok": 0.0,
        "price_out_per_mtok": 0.0,
    },
    "phi3.5:3.8b": {
        "provider": "ollama",
        "api_model": "phi3.5:3.8b",
        "price_in_per_mtok": 0.0,
        "price_out_per_mtok": 0.0,
    },
}

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


@dataclass
class LLMResult:
    text: str | None
    tokens: dict[str, int]
    latency_ms: int
    cost_usd: float | None
    model_id: str
    provider: str
    error: str | None = None


def clean_llm_response(text: str) -> str:
    """Strip chain-of-thought preamble that some local models (qwen3, etc.)
    leak despite enable_thinking=false. Removes lines like 'Okay, the user
    wants...', 'I need to...', 'Let me...', etc."""
    if not text:
        return text
    lines = text.split("\n")
    cleaned = []
    reasoning_patterns = re.compile(
        r"^(okay|ok|alright|so|let me|i need to|i should|i\'ll|first|the user|hmm|now|thinking|wait)\b",
        re.IGNORECASE,
    )
    started = False
    for line in lines:
        stripped = line.strip()
        if not started:
            if not stripped:
                continue
            if reasoning_patterns.match(stripped):
                continue
            started = True
        cleaned.append(line)
    result = "\n".join(cleaned).strip()
    return result if result else text.strip()


def query_claude(
    system_prompt: str,
    user_prompt: str,
    *,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 500,
    api_key: str | None = None,
) -> tuple[str | None, dict[str, int]]:
    """Call Claude via the official anthropic SDK, falling back to a raw HTTP
    POST if the SDK isn't installed or the call fails.

    Returns (text, usage) where usage is {"input": int, "output": int}.
    text is None if there's no API key or every attempt failed.
    """
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None, dict(_EMPTY_USAGE)

    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=key)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        usage_obj = getattr(resp, "usage", None)
        usage = {
            "input": int(getattr(usage_obj, "input_tokens", 0) or 0) if usage_obj else 0,
            "output": int(getattr(usage_obj, "output_tokens", 0) or 0) if usage_obj else 0,
        }
        if text:
            return clean_llm_response(text), usage
        return None, usage
    except ImportError:
        logger.warning("anthropic SDK not installed — falling back to raw HTTP")
    except Exception as e:
        logger.warning("anthropic SDK call failed: %s — falling back to raw HTTP", e)

    # Fallback path: raw HTTP (no SDK dependency).
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            },
            timeout=30,
        )
        if r.status_code == 200:
            body: dict[str, Any] = r.json()
            text = (body.get("content") or [{}])[0].get("text", "").strip()
            usage_obj = body.get("usage") or {}
            usage = {
                "input": int(usage_obj.get("input_tokens", 0) or 0),
                "output": int(usage_obj.get("output_tokens", 0) or 0),
            }
            if text:
                return clean_llm_response(text), usage
            return None, usage
    except Exception as e:
        logger.warning("anthropic raw HTTP call failed: %s", e)

    return None, dict(_EMPTY_USAGE)


def _anthropic_cost_usd(entry: dict[str, Any], tokens: dict[str, int]) -> float:
    price_in = entry.get("price_in_per_mtok", 0.0)
    price_out = entry.get("price_out_per_mtok", 0.0)
    return round(
        (tokens.get("input", 0) / 1_000_000) * price_in
        + (tokens.get("output", 0) / 1_000_000) * price_out,
        6,
    )


def _query_anthropic(
    api_model: str, prompt: str, system: str | None, max_tokens: int
) -> tuple[str | None, dict[str, int], str | None]:
    """Returns (text, tokens, error). Never raises — SDK/network failures land in error."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, dict(_EMPTY_USAGE), "ANTHROPIC_API_KEY not set"
    try:
        import anthropic  # type: ignore

        client = anthropic.Anthropic(api_key=api_key)
        kwargs: dict[str, Any] = {
            "model": api_model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in resp.content).strip()
        usage_obj = getattr(resp, "usage", None)
        tokens = {
            "input": int(getattr(usage_obj, "input_tokens", 0) or 0) if usage_obj else 0,
            "output": int(getattr(usage_obj, "output_tokens", 0) or 0) if usage_obj else 0,
        }
        text = clean_llm_response(text) if text else text
        return (text or None), tokens, None
    except Exception as e:
        logger.warning("query(): anthropic call failed for %s: %s", api_model, e)
        return None, dict(_EMPTY_USAGE), str(e)


def _query_ollama(
    api_model: str, prompt: str, system: str | None, timeout_s: int
) -> tuple[str | None, dict[str, int], str | None]:
    """Returns (text, tokens, error). Never raises — connection/HTTP failures land in error."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        r = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={"model": api_model, "messages": messages, "stream": False, "think": False},
            timeout=timeout_s,
        )
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        text = ((body.get("message") or {}).get("content") or "").strip()
        tokens = {
            "input": int(body.get("prompt_eval_count", 0) or 0),
            "output": int(body.get("eval_count", 0) or 0),
        }
        text = clean_llm_response(text) if text else text
        return (text or None), tokens, None
    except Exception as e:
        logger.warning("query(): ollama call failed for %s: %s", api_model, e)
        return None, dict(_EMPTY_USAGE), str(e)


def query(
    model_id: str,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 600,
    timeout_s: int = 30,
) -> LLMResult:
    """Query a model from MODEL_REGISTRY (Claude or a local Ollama model).

    Never raises a provider/network exception — failures are reported via
    LLMResult.error instead, so campaign loops (run_campaign) can keep going
    across a bad model or a down Ollama server. An unknown model_id is a
    programming error, not a runtime one, and raises KeyError immediately.
    """
    entry = MODEL_REGISTRY[model_id]  # KeyError intentional for unknown model_id
    provider = entry["provider"]
    t0 = time.time()

    if provider == "anthropic":
        text, tokens, error = _query_anthropic(entry["api_model"], prompt, system, max_tokens)
        cost_usd = _anthropic_cost_usd(entry, tokens) if error is None else None
    elif provider == "ollama":
        text, tokens, error = _query_ollama(entry["api_model"], prompt, system, timeout_s)
        cost_usd = 0.0  # local inference — cost is known-free, not unknown
    else:
        text, tokens, error = None, dict(_EMPTY_USAGE), f"unknown provider: {provider!r}"
        cost_usd = None

    return LLMResult(
        text=text,
        tokens=tokens,
        latency_ms=int((time.time() - t0) * 1000),
        cost_usd=cost_usd,
        model_id=model_id,
        provider=provider,
        error=error,
    )
