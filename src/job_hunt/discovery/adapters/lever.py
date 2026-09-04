"""Lever ATS discovery adapter."""

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


class LeverAdapter(DiscoveryAdapter):
    """Fetches job postings from Lever public postings API."""

    adapter_id = "lever"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"lever\.co", url, re.IGNORECASE))

    def _extract_site(self, entry: Dict[str, Any]) -> tuple[str, str]:
        if "site" in entry:
            return entry["site"], "api.lever.co"
        url = entry.get("url") or entry.get("careers_url") or ""
        match = re.search(r"jobs\.((?:eu\.)?lever\.co)/([^/?#]+)", url)
        if match:
            host = f"api.{match.group(1)}"
            slug = match.group(2)
            return slug, host
        return "", "api.lever.co"

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        slug, host = self._extract_site(entry)
        if not slug:
            return []

        company_name = entry.get("name", slug.capitalize())
        api_url = f"https://{host}/v0/postings/{slug}?mode=json"

        response = await client.get(api_url, timeout=15.0)
        response.raise_for_status()
        jobs_raw = response.json()
        if not isinstance(jobs_raw, list):
            return []

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = j.get("text", "").strip()
            if not title:
                continue

            raw_url = j.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{j.get('id')}"
            canon_url = normalize_url(raw_url)

            categories = j.get("categories", {})
            primary_loc = categories.get("location", "") if isinstance(categories, dict) else ""
            all_locs = categories.get("allLocations", []) if isinstance(categories, dict) else []
            locs = [primary_loc] + [l for l in all_locs if l and l != primary_loc]
            location_str = "; ".join([l for l in locs if l])

            description = j.get("descriptionPlain", "") or j.get("description", "") or ""
            posted_at = str(j.get("createdAt")) if j.get("createdAt") else None

            posting = JobPosting(
                external_id=str(j.get("id")),
                source="lever",
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
                metadata={"lever_id": j.get("id"), "workplace_type": categories.get("workplaceType")},
            )
            postings.append(posting)

        return postings
