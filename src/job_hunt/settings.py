"""Runtime feature switches for optional job-board integrations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

LINKEDIN_ENABLED_ENV = "LINKEDIN_ENABLED"

_TRUTHY = {"1", "true", "yes", "on"}


def linkedin_enabled(sources_path: str | Path = "config/sources.yaml") -> bool:
    """Whether LinkedIn may enter any pipeline stage.

    Precedence: LINKEDIN_ENABLED env var, then sources.yaml top-level
    ``linkedin_enabled`` key, then default False. LinkedIn is off by default
    after the account ban; set the env var or config key to re-enable.
    """
    env = os.environ.get(LINKEDIN_ENABLED_ENV)
    if env is not None:
        return env.strip().lower() in _TRUTHY
    try:
        p = Path(sources_path)
        if p.exists():
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "linkedin_enabled" in data:
                return bool(data["linkedin_enabled"])
    except Exception:
        pass
    return False


def is_linkedin_entry(entry: Dict[str, Any]) -> bool:
    return isinstance(entry, dict) and entry.get("adapter") == "linkedin"


def is_linkedin_job(job: Any) -> bool:
    source = getattr(job, "source", "") or ""
    raw_url = getattr(job, "raw_url", "") or ""
    if source.strip().lower() == "linkedin":
        return True
    return "linkedin.com" in raw_url.lower()
