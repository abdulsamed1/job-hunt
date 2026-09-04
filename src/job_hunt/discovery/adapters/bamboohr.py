"""BambooHR public ATS careers API discovery adapter.

Fetches job listings from BambooHR's public per-tenant careers endpoint:
GET https://{tenant}.bamboohr.com/careers/list
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

TENANT_PATTERN = re.compile(r"([a-zA-Z0-9_-]+)\.bamboohr\.com", re.IGNORECASE)


class BambooHRAdapter(DiscoveryAdapter):
    """Fetches job postings from BambooHR public careers list API."""

    adapter_id = "bamboohr"

    def matches_url(self, url: str) -> bool:
        return "bamboohr.com" in url.lower()

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("url") or entry.get("careers_url") or entry.get("api") or ""
        match = TENANT_PATTERN.search(url)
        if not match:
            tenant = entry.get("name", "").lower().replace(" ", "")
        else:
            tenant = match.group(1)

        api_url = f"https://{tenant}.bamboohr.com/careers/list"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
        }

        try:
            resp = await client.get(api_url, headers=headers, timeout=12.0, follow_redirects=True)
            if resp.status_code != 200:
                logger.debug("BambooHR API for %s returned status %d", tenant, resp.status_code)
                return []

            data = resp.json()
        except Exception as exc:
            logger.debug("BambooHR fetch failed for %s: %s", tenant, exc)
            return []

        raw_jobs = data.get("result") or []
        company = entry.get("name") or tenant.title()
        postings: List[JobPosting] = []

        for j in raw_jobs:
            title = (j.get("jobOpeningName") or "").strip()
            job_id = str(j.get("id", ""))
            if not title or not job_id:
                continue

            raw_url = f"https://{tenant}.bamboohr.com/careers/{job_id}"
            canon_url = normalize_url(raw_url)

            loc_obj = j.get("location") or {}
            city = loc_obj.get("city", "")
            state = loc_obj.get("state", "")
            loc_str = f"{city}, {state}".strip(", ")
            if j.get("isRemote"):
                loc_str = f"{loc_str} (Remote)" if loc_str else "Remote"

            desc = f"{title} at {company}. Location: {loc_str or 'Remote'}."

            posting = JobPosting(
                external_id=job_id,
                source="bamboohr",
                source_name=company,
                title=title,
                company=company,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company, title, loc_str),
                content_hash=content_hash(desc),
                location=loc_str,
                description=desc,
                metadata={"is_remote": j.get("isRemote", False)},
            )
            postings.append(posting)

        return postings
