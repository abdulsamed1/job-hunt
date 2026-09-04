"""Abstract base adapter for job source discovery."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import httpx

from job_hunt.models import JobPosting


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
