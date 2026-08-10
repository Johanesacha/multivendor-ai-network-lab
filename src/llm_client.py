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
from typing import Any

import requests

logger = logging.getLogger(__name__)

_EMPTY_USAGE = {"input": 0, "output": 0}


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
