"""Exact-apex host classification. Substring matching is spoofable; this is not."""
from __future__ import annotations
from urllib.parse import urlsplit

ATS_APEXES = (
    "boards.greenhouse.io", "boards-api.greenhouse.io",
    "jobs.lever.co", "api.lever.co",
    "jobs.ashbyhq.com", "api.ashbyhq.com",
    "jobs.smartrecruiters.com", "api.smartrecruiters.com",
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
