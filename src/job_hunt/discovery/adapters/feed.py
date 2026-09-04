"""Generic JSON/RSS Feed and RemoteOK discovery adapter."""

from __future__ import annotations

import re
from typing import Any, Dict, List
import httpx

from job_hunt.dedup import (
    canonical_url_hash,
    compute_role_fingerprint,
    content_hash,
    normalize_url,
)
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.models import JobPosting


class FeedAdapter(DiscoveryAdapter):
    """Fetches job postings from standard JSON feeds (such as RemoteOK, Himalayas, Remotive, etc.)."""

    adapter_id = "feed"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"(remoteok\.com|remotive\.com|jobicy\.com|arbeitnow\.com)", url, re.IGNORECASE)) or url.endswith(".json")

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("api") or entry.get("url") or ""
        if not url:
            return []

        response = await client.get(url, timeout=15.0, headers={"User-Agent": "JobHuntAgent/1.0"})
        response.raise_for_status()
        data = response.json()

        # Handle RemoteOK or list of jobs
        if isinstance(data, list):
            jobs_raw = [j for j in data if isinstance(j, dict) and "legal" not in j]
        elif isinstance(data, dict):
            jobs_raw = data.get("jobs") or data.get("data") or data.get("postings") or []
        else:
            return []

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = (j.get("position") or j.get("title") or "").strip()
            company = (j.get("company") or j.get("company_name") or entry.get("name") or "").strip()
            raw_url = j.get("url") or j.get("apply_url") or ""
            if not title or not raw_url or not company:
                continue

            canon_url = normalize_url(raw_url)
            location_str = j.get("location") or ("Remote" if j.get("remote") else "")
            description = j.get("description") or ""

            posting = JobPosting(
                external_id=str(j.get("id", "")),
                source=entry.get("name", "feed").lower(),
                source_name=company,
                title=title,
                company=company,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company, title, location_str),
                content_hash=content_hash(description),
                location=location_str,
                description=description,
                posted_at=str(j.get("date") or j.get("epoch") or ""),
                metadata={"tags": j.get("tags", [])},
            )
            postings.append(posting)

        return postings
