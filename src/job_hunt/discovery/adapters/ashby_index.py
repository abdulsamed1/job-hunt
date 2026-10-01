"""Ashby-index adapter: fan out from an Ashby-powered jobs aggregator page.

Some ecosystems (e.g. jobs.solana.com) aggregate per-company Ashby boards.
This adapter extracts organization slugs from the index page and runs the
standard AshbyAdapter per org (capped). No browser, no login.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Dict, List
from urllib.parse import urljoin

import httpx

from job_hunt.discovery.adapters.ashby import AshbyAdapter
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)


class AshbyIndexAdapter(DiscoveryAdapter):
    """Discovers jobs via an Ashby-aggregator index page."""

    adapter_id = "ashby-index"

    def matches_url(self, url: str) -> bool:
        lowered = url.lower()
        return "jobs.solana.com" in lowered

    @staticmethod
    def extract_orgs(html: str, base_url: str = "") -> List[str]:
        """Unique Ashby org slugs from board links, in first-seen order."""
        seen: List[str] = []
        for match in re.finditer(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_-]+)", html):
            slug = match.group(1).lower()
            if slug not in seen:
                seen.append(slug)
        return seen

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        url = entry.get("url") or ""
        if not url:
            return []
        max_orgs = int(entry.get("max_orgs", 8))
        try:
            resp = await client.get(
                url,
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            )
        except Exception as exc:
            logger.debug("Ashby index fetch failed for %s: %s", url, exc)
            return []
        if resp.status_code != 200:
            return []

        orgs = self.extract_orgs(resp.text, url)[:max_orgs]
        logger.info("Ashby index %s: %d org boards", url, len(orgs))
        board = AshbyAdapter()
        postings: List[JobPosting] = []
        for org in orgs:
            try:
                batch = await board.fetch(
                    {"org": org, "url": f"https://jobs.ashbyhq.com/{org}",
                     "name": entry.get("name", org)},
                    client,
                )
                postings.extend(batch)
            except Exception as exc:
                logger.debug("Ashby org %s failed: %s", org, exc)
            await asyncio.sleep(random.uniform(0.5, 1.5))
        return postings
