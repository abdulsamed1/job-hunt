"""Workable public ATS widget API discovery adapter.

Extracts job postings from Workable's public widget API without authentication:
GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true
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

SLUG_PATTERN = re.compile(r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([a-zA-Z0-9_-]+)", re.IGNORECASE)


class WorkableAdapter(DiscoveryAdapter):
    """Fetches job postings from Workable's public widget API."""

    adapter_id = "workable"

    def matches_url(self, url: str) -> bool:
        return "workable.com" in url.lower()

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("url") or entry.get("careers_url") or ""
        match = SLUG_PATTERN.search(url)
        if not match:
            slug = entry.get("name", "").lower().replace(" ", "-")
        else:
            slug = match.group(1)

        api_url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://apply.workable.com",
        }

        try:
            resp = await client.get(api_url, headers=headers, timeout=12.0, follow_redirects=True)
            if resp.status_code != 200:
                logger.debug("Workable API for %s returned status %d", slug, resp.status_code)
                return []

            data = resp.json()
        except Exception as exc:
            logger.debug("Workable fetch failed for %s: %s", slug, exc)
            return []

        company = data.get("name") or entry.get("name") or slug.title()
        raw_jobs = data.get("jobs") or []
        postings: List[JobPosting] = []

        for j in raw_jobs:
            title = (j.get("title") or "").strip()
            raw_url = j.get("url") or j.get("shortlink") or ""
            if not title or not raw_url:
                continue

            canon_url = normalize_url(raw_url)
            loc_parts = [j.get("city"), j.get("state"), j.get("country")]
            loc_str = ", ".join([p for p in loc_parts if p])
            if j.get("telecommuting"):
                loc_str = f"{loc_str} (Remote)" if loc_str else "Remote"

            desc = j.get("description") or f"{title} at {company}"

            posting = JobPosting(
                external_id=j.get("shortcode") or None,
                source="workable",
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
                posted_at=j.get("published_on"),
                metadata={"department": j.get("department")},
            )
            postings.append(posting)

        return postings
