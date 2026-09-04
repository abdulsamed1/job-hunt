"""Greenhouse ATS discovery adapter."""

from __future__ import annotations

import re
import urllib.parse
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


class GreenhouseAdapter(DiscoveryAdapter):
    """Fetches job postings from Greenhouse public boards API."""

    adapter_id = "greenhouse"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"greenhouse\.io", url, re.IGNORECASE))

    def _extract_board_token(self, entry: Dict[str, Any]) -> str:
        if "token" in entry:
            return entry["token"]
        url = entry.get("url") or entry.get("careers_url") or ""
        match = re.search(r"boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([^/?#]+)", url)
        if match:
            return match.group(1)
        match2 = re.search(r"job-boards(?:\.eu)?\.greenhouse\.io/([^/?#]+)", url)
        if match2:
            return match2.group(1)
        return ""

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        token = self._extract_board_token(entry)
        if not token:
            return []

        company_name = entry.get("name", token.capitalize())
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"

        response = await client.get(api_url, timeout=15.0)
        response.raise_for_status()
        data = response.json()
        jobs_raw = data.get("jobs", [])

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = j.get("title", "").strip()
            if not title:
                continue

            raw_url = j.get("absolute_url") or f"https://boards.greenhouse.io/{token}/jobs/{j.get('id')}"
            canon_url = normalize_url(raw_url)
            location_data = j.get("location", {})
            location_str = location_data.get("name", "") if isinstance(location_data, dict) else str(location_data)

            # Office enrichment
            offices = j.get("offices", [])
            if offices and isinstance(offices, list):
                office_names = [o.get("name") for o in offices if isinstance(o, dict) and o.get("name")]
                if office_names:
                    location_str = f"{location_str}; {', '.join(office_names)}".strip("; ")

            description = j.get("content", "") or ""
            posted_at = j.get("updated_at")

            posting = JobPosting(
                external_id=str(j.get("id")),
                source="greenhouse",
                source_name=company_name,
                title=title,
                company=company_name,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company_name, title, location_str),
                content_hash=content_hash(description),
                location=location_str,
                description=description,
                posted_at=posted_at,
                metadata={"greenhouse_id": j.get("id"), "requisition_id": j.get("requisition_id")},
            )
            postings.append(posting)

        return postings
