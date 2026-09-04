"""Workday CXS API discovery adapter."""

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


class WorkdayAdapter(DiscoveryAdapter):
    """Fetches job postings from Workday CXS (Candidate Experience Services) public API."""

    adapter_id = "workday"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"myworkdayjobs\.com", url, re.IGNORECASE))

    def _extract_cxs_endpoint(self, entry: Dict[str, Any]) -> tuple[str, str, str]:
        url = entry.get("url") or entry.get("careers_url") or ""
        match = re.search(r"https?://([^/]+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url)
        if match:
            host_sub = match.group(1)
            site = match.group(2)
            # host_sub can be e.g. "nvidia.wd5" or "adobe"
            parts = host_sub.split(".")
            tenant = parts[0]
            domain = f"{host_sub}.myworkdayjobs.com"
            return domain, tenant, site
        return "", "", ""

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        domain, tenant, site = self._extract_cxs_endpoint(entry)
        if not domain:
            return []

        company_name = entry.get("name", tenant.capitalize())
        api_url = f"https://{domain}/wday/cxs/{tenant}/{site}/jobs"

        payload = {
            "appliedFacets": {},
            "limit": 50,
            "offset": 0,
            "searchText": "Software Engineer",
        }

        try:
            response = await client.post(api_url, json=payload, timeout=15.0)
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []

        jobs_raw = data.get("jobPostings", [])
        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = j.get("title", "").strip()
            if not title:
                continue

            external_path = j.get("externalPath", "")
            raw_url = f"https://{domain}/{site}{external_path}"
            canon_url = normalize_url(raw_url)
            location_str = j.get("locationsText", "")

            posting = JobPosting(
                external_id=j.get("bulletFields", [None])[0] if j.get("bulletFields") else None,
                source="workday",
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
                posted_at=j.get("postedOn"),
                metadata={"workday_external_path": external_path},
            )
            postings.append(posting)

        return postings
