"""Exact-apex host classification. Substring matching is spoofable; this is not."""
from __future__ import annotations
from urllib.parse import urlsplit

ATS_APEXES = (
    "boards.greenhouse.io", "boards-api.greenhouse.io",
    "jobs.lever.co", "api.lever.co",
    "jobs.ashbyhq.com", "api.ashbyhq.com",
    "jobs.smartrecruiters.com", "api.smartrecruiters.com",
    "myworkdayjobs.com",
    "workable.com",
    "bamboohr.com",
)

def classify_host(url: str) -> str:
    try:
        parts = urlsplit(url)
    except Exception:
        return "unverified"
    if parts.scheme not in ("http", "https"):
        return "unverified"
    host = (parts.hostname or "").lower()
    if not host:
        return "unverified"
    for apex in ATS_APEXES:
        if host == apex or host.endswith("." + apex):
            return "ats"
    return "unverified"


SPOOF_TOKENS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday", "workable", "bamboohr")

def is_spoof_like(url: str) -> bool:
    """True when the host mentions a known ATS token yet fails exact-apex verification."""
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        return False
    if not host or classify_host(url) != "unverified":
        return False
    return any(t in host for t in SPOOF_TOKENS)
