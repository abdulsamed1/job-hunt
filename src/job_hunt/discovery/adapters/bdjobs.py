"""BDJobs (bdjobs.com, Bangladesh) discovery adapter.

Queries the public job-search API per query/location. When a city-level
search yields nothing, widens once to all-Bangladesh before moving on.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List
from urllib.parse import urlsplit

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

WIDENED_LOCATION = "Bangladesh"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


async def _response_data(response: Any) -> Any:
    """Return response.json(), tolerating both sync (httpx) and async (mock) variants."""
    data = response.json()
    if inspect.isawaitable(data):
        data = await data
    return data


def _ok_status(response: Any) -> bool:
    status = getattr(response, "status_code", 200)
    return status == 200 if isinstance(status, int) else True


class BdJobsAdapter(DiscoveryAdapter):
    """Discovers Bangladesh tech jobs from BDJobs' public search API."""

    adapter_id = "bdjobs"

    def matches_url(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return host == "bdjobs.com" or host.endswith(".bdjobs.com")

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("api") or entry.get("url") or ""
        if not url:
            return []
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Dhaka, Bangladesh"]
        max_pages = entry.get("max_pages_per_query", 2)
        target = entry.get("target_jobs_count", 40)

        postings: List[JobPosting] = []
        seen: set[str] = set()
        for query in queries:
            if len(postings) >= target:
                break
            for location in locations:
                if len(postings) >= target:
                    break
                batch = await self._search_location(client, url, query, location, max_pages)
                for job in batch:
                    key = str(job.get("id") or job.get("url") or job.get("title"))
                    if key in seen:
                        continue
                    seen.add(key)
                    posting = self._to_posting(entry, job)
                    if posting is not None:
                        postings.append(posting)
                    if len(postings) >= target:
                        break
        return postings[:target]

    async def _search_location(
        self, client: httpx.AsyncClient, url: str, query: str, location: str, max_pages: int
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = await self._search_page(client, url, query, location, page)
            if not batch:
                break
            collected.extend(batch)
        if not collected and location.strip().lower() != WIDENED_LOCATION.lower():
            logger.info("BDJobs no hits for %s, widening to %s", location, WIDENED_LOCATION)
            for page in range(1, max_pages + 1):
                batch = await self._search_page(client, url, query, WIDENED_LOCATION, page)
                if not batch:
                    break
                collected.extend(batch)
        return collected

    async def _search_page(
        self, client: httpx.AsyncClient, url: str, query: str, location: str, page: int
    ) -> List[Dict[str, Any]]:
        try:
            response = await client.get(
                url,
                params={"q": query, "location": location, "page": page},
                headers=HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
        except Exception as exc:
            logger.debug("BDJobs search failed: %s", exc)
            return []
        if not _ok_status(response):
            return []
        try:
            data = await _response_data(response)
        except Exception as exc:
            logger.debug("BDJobs bad JSON: %s", exc)
            return []
        return self._jobs_list(data)

    def _jobs_list(self, data: Any) -> List[Dict[str, Any]]:
        if not data:
            return []
        if isinstance(data, list):
            raw = data
        elif isinstance(data, dict):
            if "jobs" in data:
                raw = data["jobs"]
            else:
                raw = data.get("data") or data.get("results") or data.get("postings") or []
            if raw is None:
                return []
            if not isinstance(raw, list):
                raise ValueError(f"BDJobs: unexpected jobs payload shape: {type(raw).__name__}")
        else:
            raise ValueError(f"BDJobs: unexpected payload shape: {type(data).__name__}")
        return [j for j in raw if isinstance(j, dict)]

    def _to_posting(self, entry: Dict[str, Any], job: Dict[str, Any]) -> JobPosting | None:
        title = str(
            job.get("title") or job.get("jobTitle") or job.get("position") or job.get("name") or ""
        ).strip()
        raw_url = str(
            job.get("url") or job.get("apply_url") or job.get("applyUrl") or job.get("link") or ""
        ).strip()
        if not title or not raw_url:
            return None
        company = str(
            job.get("company")
            or job.get("companyName")
            or job.get("company_name")
            or job.get("employer")
            or "Unknown"
        ).strip()
        location = str(
            job.get("location") or job.get("jobLocation") or job.get("city") or "Bangladesh"
        )
        description = job.get("description") or job.get("jobDescription") or title
        description = str(description).strip()
        canon_url = normalize_url(raw_url)
        return JobPosting(
            external_id=f"bdjobs-{job.get('id')}" if job.get("id") else None,
            source="bdjobs",
            source_name=company,
            title=title,
            company=company,
            raw_url=raw_url,
            canonical_url=canon_url,
            canonical_url_hash=canonical_url_hash(canon_url),
            role_fingerprint=compute_role_fingerprint(company, title, location),
            content_hash=content_hash(description),
            location=location,
            description=description,
            posted_at=str(
                job.get("posted_at")
                or job.get("publishedDate")
                or job.get("date")
                or job.get("created_at")
                or ""
            ),
            metadata={"source_platform": "bdjobs"},
        )
