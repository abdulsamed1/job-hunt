"""Freehire (freehire.me) discovery adapter.

Queries the public agent job-search API, which returns full descriptions
inline. When a listing comes back truncated, falls back to a bounded
per-job detail fetch (max 8) before giving up and keeping the short text.
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

SEARCH_PATH = "https://freehire.me/api/v1/agent/jobs/search"
FULL_DESCRIPTION_MIN = 400
MAX_DETAIL_FETCHES = 8

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


class FreehireAdapter(DiscoveryAdapter):
    """Discovers remote jobs from Freehire's public search API."""

    adapter_id = "freehire"

    def matches_url(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return host == "freehire.me" or host.endswith(".freehire.me")

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("api") or entry.get("url") or ""
        if not url:
            return []
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Remote"]
        max_pages = entry.get("max_pages_per_query", 2)
        target = entry.get("target_jobs_count", 40)

        postings: List[JobPosting] = []
        seen: set[str] = set()
        detail_budget = MAX_DETAIL_FETCHES
        for query in queries:
            if len(postings) >= target:
                break
            for location in locations:
                if len(postings) >= target:
                    break
                for page in range(1, max_pages + 1):
                    if len(postings) >= target:
                        break
                    batch = await self._search_page(client, url, query, location, page)
                    if not batch:
                        break
                    for job in batch:
                        key = str(job.get("id") or job.get("url") or job.get("title"))
                        if key in seen:
                            continue
                        seen.add(key)
                        description = self._description(job)
                        if len(description) < FULL_DESCRIPTION_MIN and detail_budget > 0:
                            detail_budget -= 1
                            description = await self._enrich_description(client, url, job, description)
                        posting = self._to_posting(entry, job, description)
                        if posting is not None:
                            postings.append(posting)
        return postings[:target]

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
            logger.debug("Freehire search failed: %s", exc)
            return []
        if not _ok_status(response):
            return []
        try:
            data = await _response_data(response)
        except Exception as exc:
            logger.debug("Freehire bad JSON: %s", exc)
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
                raise ValueError(f"Freehire: unexpected jobs payload shape: {type(raw).__name__}")
        else:
            raise ValueError(f"Freehire: unexpected payload shape: {type(data).__name__}")
        return [j for j in raw if isinstance(j, dict)]

    def _description(self, job: Dict[str, Any]) -> str:
        for key in ("description", "content", "summary", "job_description"):
            text = job.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return str(job.get("title") or "")

    async def _enrich_description(
        self, client: httpx.AsyncClient, search_url: str, job: Dict[str, Any], fallback: str
    ) -> str:
        job_id = job.get("id")
        if not job_id:
            return fallback
        base = search_url[: -len("/search")] if search_url.endswith("/search") else search_url
        try:
            response = await client.get(
                f"{base}/{job_id}", headers=HEADERS, timeout=15.0, follow_redirects=True
            )
            if not _ok_status(response):
                return fallback
            data = await _response_data(response)
            job_detail = data.get("job") if isinstance(data, dict) else None
            if isinstance(job_detail, dict):
                text = self._description(job_detail)
                if len(text) > len(fallback):
                    return text
        except Exception as exc:
            logger.debug("Freehire detail fetch failed: %s", exc)
        return fallback

    def _to_posting(
        self, entry: Dict[str, Any], job: Dict[str, Any], description: str
    ) -> JobPosting | None:
        title = str(job.get("title") or job.get("position") or job.get("name") or "").strip()
        raw_url = str(job.get("url") or job.get("apply_url") or job.get("link") or "").strip()
        if not title or not raw_url:
            return None
        company = str(
            job.get("company") or job.get("company_name") or job.get("employer") or "Unknown"
        ).strip()
        location = str(job.get("location") or job.get("candidate_required_location") or "Remote")
        canon_url = normalize_url(raw_url)
        return JobPosting(
            external_id=f"freehire-{job.get('id')}" if job.get("id") else None,
            source="freehire",
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
            posted_at=str(job.get("posted_at") or job.get("date") or job.get("created_at") or ""),
            metadata={"source_platform": "freehire"},
        )
