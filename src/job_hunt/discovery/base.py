"""Abstract base adapter for job source discovery."""

from __future__ import annotations

import html as _html
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import httpx

from job_hunt.models import JobPosting

# Single shared browser UA for direct-fetch discovery adapters (and the
# robots gate). One constant so header updates happen in exactly one place.
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_TAG_RE = re.compile(r"<[^>]+>")


def clean_description(raw: str, limit: int = 4000) -> str:
    """Uniform description pipeline: unescape, strip HTML tags, collapse
    whitespace, truncate to `limit` chars. Hash/store the result of this."""
    text = _html.unescape(raw or "")
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


class DiscoveryAdapter(ABC):
    """Base class for all ATS and job board discovery adapters."""

    adapter_id: str = "base"

    @abstractmethod
    def matches_url(self, url: str) -> bool:
        """Return True if this adapter handles the given URL pattern."""
        pass

    @abstractmethod
    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        """Fetch and normalize job postings from the configured entry."""
        pass

    def gate_url(self, entry: Dict[str, Any]) -> str:
        """URL the robots gate evaluates: the ADAPTER's actual fetch/feed URL.

        Tenants derive their fetch URL from the entry (jobs.rss, /api/offers/,
        /xml), so gating the entry's bare host would check the wrong path.
        Defaults to the entry URL; adapters with derived fetch URLs override.
        """
        return (entry.get("url") or entry.get("careers_url") or entry.get("api") or "").strip()
