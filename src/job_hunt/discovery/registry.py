"""Discovery registry: resolves adapters and orchestrates multi-source scraping."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx
import yaml

from job_hunt.discovery.adapters.ashby import AshbyAdapter
from job_hunt.discovery.adapters.bamboohr import BambooHRAdapter
from job_hunt.discovery.adapters.feed import FeedAdapter
from job_hunt.discovery.adapters.greenhouse import GreenhouseAdapter
from job_hunt.discovery.adapters.lever import LeverAdapter
from job_hunt.discovery.adapters.linkedin import LinkedInAdapter
from job_hunt.discovery.adapters.smartrecruiters import SmartRecruitersAdapter
from job_hunt.discovery.adapters.workable import WorkableAdapter
from job_hunt.discovery.adapters.workday import WorkdayAdapter
from job_hunt.discovery.adapters.web import UniversalWebAdapter
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)


class SourceRegistry:
    """Registry of ATS and job board discovery adapters."""

    def __init__(self):
        self.adapters: List[DiscoveryAdapter] = [
            GreenhouseAdapter(),
            LeverAdapter(),
            AshbyAdapter(),
            SmartRecruitersAdapter(),
            WorkdayAdapter(),
            WorkableAdapter(),
            BambooHRAdapter(),
            FeedAdapter(),
            LinkedInAdapter(),
            UniversalWebAdapter(),
        ]
        self._adapter_map = {a.adapter_id: a for a in self.adapters}

    def resolve_adapter(self, entry: Dict[str, Any]) -> Optional[DiscoveryAdapter]:
        """Resolve adapter for a given configuration entry."""
        explicit = entry.get("adapter")
        if explicit and explicit in self._adapter_map:
            return self._adapter_map[explicit]

        url = entry.get("url") or entry.get("careers_url") or entry.get("api") or ""
        for adapter in self.adapters:
            if adapter.matches_url(url):
                return adapter
        return None

    def load_sources_file(self, path: str | Path = "config/sources.yaml") -> List[Dict[str, Any]]:
        """Load source configuration list from YAML."""
        p = Path(path)
        if not p.exists():
            return []
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("sources", [])
        return []

    async def scan_source(
        self,
        entry: Dict[str, Any],
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
    ) -> List[JobPosting]:
        """Safely scan a single source with concurrency limiting and error isolation."""
        adapter = self.resolve_adapter(entry)
        name = entry.get("name", "unknown")
        if not adapter:
            logger.warning("No adapter found for source: %s", name)
            return []

        async with semaphore:
            try:
                logger.info("Scanning source [%s] with adapter [%s]", name, adapter.adapter_id)
                postings = await adapter.fetch(entry, client)
                logger.info("Source [%s] yielded %d postings", name, len(postings))
                return postings
            except Exception as exc:
                logger.error("Failed to scan source [%s]: %s", name, exc)
                return []

    async def discover_all(
        self,
        sources: Optional[List[Dict[str, Any]]] = None,
        max_concurrency: int = 5,
        sources_path: str | Path = "config/sources.yaml",
    ) -> List[JobPosting]:
        """Scan all configured sources concurrently, returning all discovered jobs."""
        if sources is None:
            sources = self.load_sources_file(sources_path)

        semaphore = asyncio.Semaphore(max_concurrency)
        async with httpx.AsyncClient(headers={"User-Agent": "JobHuntAgent/1.0"}) as client:
            tasks = [self.scan_source(entry, client, semaphore) for entry in sources]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_postings: List[JobPosting] = []
        for res in results:
            if isinstance(res, list):
                all_postings.extend(res)
        return all_postings
