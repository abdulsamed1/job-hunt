"""SmartRecruiters ATS discovery adapter."""

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


class SmartRecruitersAdapter(DiscoveryAdapter):
    """Fetches job postings from SmartRecruiters public API."""

    adapter_id = "smartrecruiters"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"smartrecruiters\.com", url, re.IGNORECASE))

    def _extract_company_id(self, entry: Dict[str, Any]) -> str:
        if "company_id" in entry:
            return entry["company_id"]
        url = entry.get("url") or entry.get("careers_url") or ""
        match = re.search(r"jobs\.smartrecruiters\.com/([^/?#]+)", url)
        if match:
            return match.group(1)
        return ""

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        cid = self._extract_company_id(entry)
        if not cid:
            return []

        company_name = entry.get("name", cid.capitalize())
        api_url = f"https://api.smartrecruiters.com/v1/companies/{cid}/postings"

        response = await client.get(api_url, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        jobs_raw = data.get("content", [])

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = j.get("name", "").strip()
            if not title:
                continue

            raw_url = f"https://jobs.smartrecruiters.com/{cid}/{j.get('id')}"
            canon_url = normalize_url(raw_url)

            loc = j.get("location", {})
            city = loc.get("city", "")
            region = loc.get("region", "")
            country = loc.get("country", "")
            loc_parts = [p for p in (city, region, country) if p]
            if loc.get("remote"):
                loc_parts.append("Remote")
            location_str = ", ".join(loc_parts)

            posting = JobPosting(
                external_id=str(j.get("id")),
                source="smartrecruiters",
                source_name=company_name,
                title=title,
                company=company_name,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company_name, title, location_str),
                content_hash=content_hash(title),
                location=location_str,
                description=title,
                posted_at=j.get("releasedDate"),
                metadata={"smartrecruiters_id": j.get("id")},
            )
            postings.append(posting)

        return postings
