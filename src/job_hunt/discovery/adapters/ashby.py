"""Ashby ATS discovery adapter."""

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


class AshbyAdapter(DiscoveryAdapter):
    """Fetches job postings from Ashby public posting API."""

    adapter_id = "ashby"

    def matches_url(self, url: str) -> bool:
        return bool(re.search(r"ashbyhq\.com", url, re.IGNORECASE))

    def _extract_organization(self, entry: Dict[str, Any]) -> str:
        if "org" in entry:
            return entry["org"]
        url = entry.get("url") or entry.get("careers_url") or ""
        match = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)", url)
        if match:
            return match.group(1)
        return ""

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        org = self._extract_organization(entry)
        if not org:
            return []

        company_name = entry.get("name", org.capitalize())
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"

        response = await client.get(api_url, timeout=20.0)
        response.raise_for_status()
        data = response.json()
        jobs_raw = data.get("jobs", [])

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = j.get("title", "").strip()
            if not title:
                continue

            raw_url = j.get("jobUrl") or f"https://jobs.ashbyhq.com/{org}/{j.get('id')}"
            canon_url = normalize_url(raw_url)

            primary_loc = j.get("location", "")
            secondary_locs = j.get("secondaryLocations", []) or []
            loc_list = [primary_loc] + [l.get("location", "") if isinstance(l, dict) else str(l) for l in secondary_locs]
            location_str = "; ".join([l for l in loc_list if l])

            description = j.get("descriptionPlain", "") or j.get("descriptionHtml", "") or ""
            posted_at = j.get("publishedAt")

            comp = j.get("compensation", {}) or {}
            comp_tier = comp.get("compensationTierSummary") or {}
            salary_min = comp_tier.get("min") or comp.get("minSalary")
            salary_max = comp_tier.get("max") or comp.get("maxSalary")
            salary_curr = comp_tier.get("currency") or comp.get("currency")

            posting = JobPosting(
                external_id=str(j.get("id")),
                source="ashby",
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
                salary_min=float(salary_min) if salary_min else None,
                salary_max=float(salary_max) if salary_max else None,
                salary_currency=salary_curr,
                posted_at=posted_at,
                metadata={"ashby_id": j.get("id"), "is_remote": j.get("isRemote")},
            )
            postings.append(posting)

        return postings
