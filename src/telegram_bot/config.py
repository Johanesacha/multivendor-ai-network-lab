"""
config.py — immutable, env-driven configuration for the ChatOps bot.

Loaded once at startup. Missing token is fatal; the chat-ID allowlist is parsed
here so a bad value fails loudly before the bot ever goes online. Numeric vars go
through ``_num_env`` so a misconfigured value names the offending variable rather
than raising an opaque ``invalid literal for int()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping, TypeVar

from .auth import parse_chat_ids

DEFAULT_DCN_API_URL = "http://localhost:5757"

_Num = TypeVar("_Num", int, float)


def _num_env(
    env: Mapping[str, str], key: str, default: str, conv: Callable[[str], _Num]
) -> _Num:
    """Read a numeric env var, failing loudly with the variable name on bad input.

    Mirrors ``parse_chat_ids``' convention: a misconfigured numeric setting names
    the offending variable (and value) instead of an opaque stdlib ValueError that
    hides which of the four numeric vars is actually wrong.
    """
    raw = env.get(key, default)
    try:
        return conv(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{key} must be a valid {conv.__name__}, got {raw!r}"
        ) from exc


@dataclass(frozen=True)
class BotConfig:
    # token and dcn_api_key are secrets: repr=False keeps them out of tracebacks
    # and debug output (e.g. a locals-dumping error reporter) regardless of level.
    token: str = field(repr=False)
    allowed_chat_ids: frozenset[int]
    admin_chat_ids: frozenset[int]
    dcn_api_url: str = DEFAULT_DCN_API_URL
    dcn_api_key: str = field(default="", repr=False)  # X-API-Key for mutating endpoints
    rate_limit_per_min: int = 20
    request_timeout: float = 30.0
    ask_timeout: float = 120.0
    report_timeout: float = 300.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BotConfig":
        env = env if env is not None else os.environ
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN is required (create a bot via @BotFather)"
            )
        return cls(
            token=token,
            allowed_chat_ids=parse_chat_ids(env.get("TELEGRAM_ALLOWED_CHAT_IDS")),
            admin_chat_ids=parse_chat_ids(env.get("TELEGRAM_ADMIN_CHAT_IDS")),
            dcn_api_url=(env.get("DCN_API_URL") or DEFAULT_DCN_API_URL).rstrip("/"),
            dcn_api_key=(env.get("MVLAB_API_KEY") or "").strip(),
            rate_limit_per_min=_num_env(env, "TELEGRAM_RATE_LIMIT_PER_MIN", "20", int),
            request_timeout=_num_env(env, "TELEGRAM_REQUEST_TIMEOUT", "30", float),
            ask_timeout=_num_env(env, "TELEGRAM_ASK_TIMEOUT", "120", float),
            report_timeout=_num_env(env, "TELEGRAM_REPORT_TIMEOUT", "300", float),
        )
