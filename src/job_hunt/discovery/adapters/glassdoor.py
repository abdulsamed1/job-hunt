"""Glassdoor discovery adapter (multi-country GraphQL).

Native httpx implementation modeled on JobSpy's Glassdoor scraper mechanics
(autocomplete cookie priming + graph API; MIT, speedyapply/JobSpy). Fragile by
nature (static CSRF token, ~30 req/IP limit): every failure degrades to [].
"""

from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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

GD_CSRF_TOKEN = (
    "Ft6oHEWlRZrxDww95Cpazw:0pGUrkb2y3TyOpAIqF2vbPmUXoXVkD3oEGDVkvfeCerceQ5-n8mBg3BovySUIjmCPHCaW0H2nQVdqzbtsYqf4Q:wcqRqeegRUa9MVLJGyujVXB7vWFPjdaS1CtrrzJq-ok"
)

GRAPH_QUERY = """
    query JobSearchResultsQuery(
        $keyword: String, $locationId: Int, $locationType: LocationTypeEnum,
        $numJobsToShow: Int!, $pageCursor: String, $pageNumber: Int,
        $filterParams: [FilterParams], $fromage: Int
    ) {
        jobListings(
            contextHolder: { searchParams: {
                keyword: $keyword, locationId: $locationId, locationType: $locationType,
                numPerPage: $numJobsToShow, pageCursor: $pageCursor, pageNumber: $pageNumber,
                filterParams: $filterParams, searchType: SR } }
        ) {
            jobListings {
                ...JobView
            }
            paginationCursors
        }
    }
    fragment JobView on JobView {
        job { listingId jobTitleText }
        header {
            employerNameFromSearch locationName locationType ageInDays
            employer { id }
        }
    }
"""

# country name -> (glassdoor base, default location handling)
COUNTRY_BASES = {
    "usa": "https://www.glassdoor.com",
    "united states": "https://www.glassdoor.com",
    "uk": "https://www.glassdoor.co.uk",
    "united kingdom": "https://www.glassdoor.co.uk",
}

MAX_RETRIES = 2


async def fetch_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Tuple[int, str, str]:
    """Plain httpx GET kept as a patchable seam (cookie jar lives on client)."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, params=params, headers=headers)
        return resp.status_code, resp.text, str(resp.url)


async def post_json(
    url: str,
    payload: Any,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Tuple[int, Any, str]:
    """Plain httpx POST kept as a patchable seam."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(url, json=payload, headers=headers)
        try:
            return resp.status_code, resp.json(), str(resp.url)
        except ValueError:
            return resp.status_code, None, str(resp.url)


class GlassdoorAdapter(DiscoveryAdapter):
    """Discovers jobs from Glassdoor's GraphQL API (fragile; degrades to [])."""

    adapter_id = "glassdoor"

    def matches_url(self, url: str) -> bool:
        return "glassdoor.com" in url.lower()

    def _base_for(self, country: Optional[str]) -> str:
        if country:
            hit = COUNTRY_BASES.get(country.strip().lower())
            if hit:
                return hit
        return "https://www.glassdoor.com"

    async def _location(
        self, base_url: str, location: Optional[str], remote_only: bool
    ) -> Optional[Tuple[int, str]]:
        if not location or remote_only:
            await fetch_text(
                f"{base_url}/autocomplete/location"
                "?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term=remote"
            )
            return 11047, "STATE"  # remote options bucket
        term = urllib.parse.quote(location)
        url = (
            f"{base_url}/autocomplete/location"
            f"?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term={term}"
        )
        status, text, _ = await fetch_text(url)
        if status == 429:
            logger.warning("Glassdoor rate limited (429)")
            return None
        if status != 200:
            return None
        try:
            import json as _json

            items = _json.loads(text)
        except ValueError:
            return None
        if not items:
            return None
        loc_type = {"C": "CITY", "S": "STATE", "N": "COUNTRY"}.get(
            items[0].get("locationType"), items[0].get("locationType")
        )
        try:
            return int(items[0]["locationId"]), loc_type
        except (KeyError, TypeError, ValueError):
            return None

    def _payload(
        self, keyword: str, location_id: int, location_type: str,
        hours_old: Optional[int], cursor: Optional[str], page: int,
    ) -> Dict[str, Any]:
        fromage = max(hours_old // 24, 1) if hours_old else None
        filter_params = []
        if fromage:
            filter_params.append({"filterKey": "fromAge", "values": str(fromage)})
        variables: Dict[str, Any] = {
            "excludeJobListingIds": [],
            "filterParams": filter_params,
            "keyword": keyword,
            "numJobsToShow": 30,
            "locationType": location_type,
            "locationId": int(location_id),
            "pageNumber": page,
            "pageCursor": cursor,
            "fromage": fromage,
        }
        return {
            "operationName": "JobSearchResultsQuery",
            "variables": variables,
            "query": GRAPH_QUERY,
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "accept": "*/*",
            "content-type": "application/json",
            "gd-csrf-token": GD_CSRF_TOKEN,
            "origin": "https://www.glassdoor.com",
            "referer": "https://www.glassdoor.com/",
            "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        }

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Remote"]
        country = entry.get("country")
        max_pages = entry.get("max_pages_per_query", 1)
        target_count = entry.get("target_jobs_count", 30)
        hours_old = entry.get("hours_old")
        remote_only = entry.get("remote_only", False)
        base_url = self._base_for(country)

        postings: List[JobPosting] = []
        seen_ids = set()
        for kw in queries:
            if len(postings) >= target_count:
                break
            for loc in locations:
                if len(postings) >= target_count:
                    break
                located = await self._location(base_url, loc, remote_only)
                if not located:
                    continue
                location_id, location_type = located
                cursor: Optional[str] = None
                for page in range(1, max_pages + 1):
                    if page > 1:
                        await asyncio.sleep(random.uniform(2.0, 5.0))
                    batch, cursor = await self._fetch_page(
                        base_url, kw, location_id, location_type,
                        hours_old, cursor, page,
                    )
                    if batch is None:
                        break
                    new = [p for p in batch if p.external_id not in seen_ids]
                    if not new:
                        break
                    seen_ids.update(p.external_id for p in new)
                    postings.extend(new)
                    if len(postings) >= target_count or not cursor:
                        break

        logger.info("Glassdoor discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    async def _fetch_page(
        self, base_url: str, keyword: str, location_id: int, location_type: str,
        hours_old: Optional[int], cursor: Optional[str], page: int,
    ) -> Tuple[Optional[List[JobPosting]], Optional[str]]:
        payload = self._payload(keyword, location_id, location_type, hours_old, cursor, page)
        for attempt in range(MAX_RETRIES):
            try:
                status, data, _ = await post_json(
                    f"{base_url}/graph", payload, headers=self._headers()
                )
            except Exception as exc:
                logger.debug("Glassdoor request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            if status == 429:
                logger.warning("Glassdoor rate limited (429)")
                return None, None
            if status != 200 or not isinstance(data, list) or not data:
                return None, None
            listings = ((data[0].get("data") or {}).get("jobListings") or {})
            items = listings.get("jobListings") or []
            postings: List[JobPosting] = []
            for item in items:
                try:
                    posting = self._parse_job(item, base_url)
                except Exception as exc:
                    logger.debug("Skipping malformed Glassdoor job: %s", exc)
                    continue
                if posting is not None:
                    postings.append(posting)
            return postings, self._next_cursor(listings.get("paginationCursors"), page + 1)
        return None, None

    @staticmethod
    def _next_cursor(cursors: Any, want_page: int) -> Optional[str]:
        if not isinstance(cursors, list):
            return None
        for entry in cursors:
            if isinstance(entry, dict) and entry.get("pageNumber") == want_page:
                for key in ("cursor", "pageCursor"):
                    if isinstance(entry.get(key), str):
                        return entry[key]
        return None

    def _parse_job(self, item: Dict[str, Any], base_url: str) -> Optional[JobPosting]:
        view = item.get("jobview") or {}
        job = view.get("job") or {}
        header = view.get("header") or {}
        listing_id = job.get("listingId")
        title = job.get("jobTitleText") or ""
        if not listing_id or not title:
            return None

        raw_url = f"{base_url}/job-listing/j?jl={listing_id}"
        company = header.get("employerNameFromSearch") or "Glassdoor Employer"
        location_name = header.get("locationName") or ""
        is_remote = header.get("locationType") == "S"
        if is_remote:
            location = location_name or "Remote"
        else:
            parts = [p.strip() for p in location_name.split(",") if p.strip()]
            location = ", ".join(parts) if parts else "Unknown"
        age = header.get("ageInDays")
        posted_at = None
        if isinstance(age, int):
            posted_at = (datetime.now(timezone.utc) - timedelta(days=age)).date().isoformat()

        description = f"{title} position at {company}. Location: {location}. Discovered via Glassdoor."
        canon_url = normalize_url(raw_url)
        return JobPosting(
            external_id=f"gd-{listing_id}",
            source="glassdoor",
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
            posted_at=posted_at,
            metadata={"source_platform": "glassdoor", "remote_flag": is_remote},
        )
