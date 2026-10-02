"""Scrub secret values (not just names) from child-process environments."""
from __future__ import annotations
import os

SECRET_NAME_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD", "CREDENTIALS")

def scrubbed_env(extra: dict | None = None) -> dict:
    env = dict(os.environ)
    if extra:
        env.update(extra)
    values = {v for k, v in env.items() if v and (k in ("REZI_MCP_TOKEN", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN") or any(h in k.upper() for h in SECRET_NAME_HINTS))}
    values.discard("")
    return {k: v for k, v in env.items() if v not in values}
