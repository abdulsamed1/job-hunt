"""BDJobs (bdjobs.com, Bangladesh) discovery adapter.

Queries the public GetJobSearch JSON endpoint per query/location. When a
city-level search yields nothing, widens once to all-Bangladesh before
moving on.
"""

from __future__ import annotations

import inspect
import logging
import math
from typing import Any, Dict, List, Optional
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
DETAILS_URL = "https://bdjobs.com/h/details/{job_id}"
RESULTS_PER_PAGE = 50

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# BDJobs location codes for the `location` search param. Unknown places fall
# back to an unfiltered (all-Bangladesh) search.
BD_LOCATION_CODES = {
    "dhaka": 14,
    "dhaka division": 1003,
    "faridpur": 16,
    "gazipur": 19,
    "gopalganj": 20,
    "kishoreganj": 29,
    "madaripur": 34,
    "manikganj": 36,
    "munshiganj": 39,
    "narayanganj": 43,
    "narsingdi": 44,
    "rajbari": 53,
    "shariatpur": 58,
    "tangail": 63,
    "chattogram": 10,
    "chattogram division": 1002,
    "bandarban": 3,
    "brahmanbaria": 1,
    "chandpur": 8,
    "cox's bazar": 13,
    "cumilla": 12,
    "feni": 17,
    "khagrachhari": 27,
    "lakshmipur": 33,
    "noakhali": 48,
    "rangamati": 55,
    "barishal": 4,
    "barishal division": 1001,
    "barguna": 7,
    "bhola": 5,
    "jhalakathi": 24,
    "patuakhali": 51,
    "pirojpur": 52,
    "khulna": 28,
    "khulna division": 1004,
    "bagerhat": 2,
    "chuadanga": 11,
    "jashore": 23,
    "jhenaidah": 25,
    "kushtia": 31,
    "magura": 35,
    "meherpur": 37,
    "narail": 42,
    "satkhira": 57,
    "mymensingh": 40,
    "mymensingh division": 1005,
    "jamalpur": 22,
    "netrokona": 46,
    "sherpur": 59,
    "rajshahi": 54,
    "rajshahi division": 1006,
    "bogura": 6,
    "chapainawabganj": 9,
    "joypurhat": 26,
    "naogaon": 41,
    "natore": 45,
    "pabna": 49,
    "sirajganj": 60,
    "rangpur": 56,
    "rangpur division": 1007,
    "dinajpur": 15,
    "gaibandha": 18,
    "kurigram": 30,
    "lalmonirhat": 32,
    "nilphamari": 47,
    "panchagarh": 50,
    "thakurgaon": 64,
    "sylhet": 62,
    "sylhet division": 1008,
    "habiganj": 21,
    "moulvibazar": 38,
    "sunamganj": 61,
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


def _location_code(location: str) -> Optional[int]:
    place = (location or "").split(",")[0].strip().lower()
    if not place or place == "bangladesh":
        return None
    code = BD_LOCATION_CODES.get(place)
    if code is None:
        logger.info("BDJobs unknown location %r, searching all of Bangladesh", location)
    return code


def _posted_within_days(hours_old: Any) -> Optional[int]:
    try:
        hours = float(hours_old) if hours_old else 0
    except (TypeError, ValueError):
        return None
    if hours <= 0:
        return None
    # The site counts calendar days in Bangladesh, today included, up to 5.
    days = math.ceil(hours / 24) + 1
    return days if days <= 5 else None


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
        hours_old = entry.get("hours_old")

        postings: List[JobPosting] = []
        seen: set[str] = set()
        for query in queries:
            if len(postings) >= target:
                break
            for location in locations:
                if len(postings) >= target:
                    break
                batch = await self._search_location(client, url, query, location, max_pages, hours_old)
                for job in batch:
                    key = str(job.get("Jobid") or job.get("id") or job.get("url") or job.get("jobTitle"))
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
        self,
        client: httpx.AsyncClient,
        url: str,
        query: str,
        location: str,
        max_pages: int,
        hours_old: Any,
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            batch = await self._search_page(client, url, query, location, page, hours_old)
            if not batch:
                break
            collected.extend(batch)
        if not collected and location.strip().lower() != WIDENED_LOCATION.lower():
            logger.info("BDJobs no hits for %s, widening to %s", location, WIDENED_LOCATION)
            for page in range(1, max_pages + 1):
                batch = await self._search_page(client, url, query, WIDENED_LOCATION, page, hours_old)
                if not batch:
                    break
                collected.extend(batch)
        return collected

    def _search_params(self, query: str, location: str, page: int, hours_old: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "rpp": RESULTS_PER_PAGE,
            "isPro": 0,
            "ToggleJobs": "true",
            "isFresher": "false",
            "keyword": query,
            "pg": page,
        }
        code = _location_code(location)
        if code is not None:
            params["location"] = code
        days = _posted_within_days(hours_old)
        if days is not None:
            params["postedWithin"] = days
        return params

    async def _search_page(
        self,
        client: httpx.AsyncClient,
        url: str,
        query: str,
        location: str,
        page: int,
        hours_old: Any,
    ) -> List[Dict[str, Any]]:
        try:
            response = await client.get(
                url,
                params=self._search_params(query, location, page, hours_old),
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
            return [j for j in data if isinstance(j, dict)]
        if isinstance(data, dict):
            merged: List[Dict[str, Any]] = []
            for key in ("data", "premiumData"):
                chunk = data.get(key)
                if chunk is None:
                    continue
                if not isinstance(chunk, list):
                    raise ValueError(f"BDJobs: unexpected jobs payload shape: {key} is {type(chunk).__name__}")
                merged.extend(j for j in chunk if isinstance(j, dict))
            if merged or "data" in data or "premiumData" in data:
                return merged
            if "jobs" in data:
                raw = data["jobs"]
            else:
                raw = data.get("results") or data.get("postings") or []
            if raw is None:
                return []
            if not isinstance(raw, list):
                raise ValueError(f"BDJobs: unexpected jobs payload shape: {type(raw).__name__}")
            return [j for j in raw if isinstance(j, dict)]
        raise ValueError(f"BDJobs: unexpected payload shape: {type(data).__name__}")

    def _to_posting(self, entry: Dict[str, Any], job: Dict[str, Any]) -> JobPosting | None:
        title = str(job.get("jobTitle") or job.get("title") or job.get("position") or "").strip()
        job_id = job.get("Jobid") or job.get("id")
        raw_url = str(job.get("url") or job.get("apply_url") or job.get("link") or "").strip()
        if not raw_url and job_id is not None and str(job_id).strip():
            raw_url = DETAILS_URL.format(job_id=str(job_id).strip())
        if not title or not raw_url:
            return None
        company = str(job.get("companyName") or job.get("company") or job.get("employer") or "Unknown").strip()
        location = str(job.get("location") or job.get("jobLocation") or "Bangladesh")
        bits = [f"{title} at {company} ({location})."]
        for key in ("JobType", "WorkPlace", "experience", "Salary"):
            val = job.get(key)
            if val:
                bits.append(f"{key}: {val}.")
        description = str(job.get("description") or job.get("jobDescription") or " ".join(bits)).strip()
        canon_url = normalize_url(raw_url)
        external_id = f"bdjobs-{job_id}" if job_id else None
        return JobPosting(
            external_id=external_id,
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
                job.get("publishDate")
                or job.get("pub")
                or job.get("posted_at")
                or job.get("deadline")
                or job.get("date")
                or ""
            ),
            metadata={"source_platform": "bdjobs"},
        )
