"""Bayt.com (MENA) discovery adapter.

Native httpx implementation modeled on JobSpy's BaytScraper endpoint usage
(public search HTML + per-card metadata; MIT, speedyapply/JobSpy). No browser,
no login, no extra dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
import httpx

from job_hunt.dedup import (
    canonical_url_hash,
    compute_role_fingerprint,
    content_hash,
    normalize_url,
)
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.discovery.tls_fetch import fetch_text
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)

BASE_URL = "https://www.bayt.com"
JOBS_PER_PAGE = 20

# Coarse server-side buckets; client re-filters to the exact hours_old.
DATE_INTERVALS = [(24, 3), (24 * 7, 2), (24 * 30, 1)]

COUNTRY_ALIASES = {
    "uae": "uae",
    "united arab emirates": "uae",
    "dubai": "uae",
    "egypt": "egypt",
    "cairo": "egypt",
    "saudi arabia": "saudi-arabia",
    "saudi": "saudi-arabia",
    "riyadh": "saudi-arabia",
    "qatar": "qatar",
    "doha": "qatar",
    "kuwait": "kuwait",
    "oman": "oman",
    "bahrain": "bahrain",
    "jordan": "jordan",
    "amman": "jordan",
    "lebanon": "lebanon",
    "morocco": "morocco",
    "remote": "remote",
}

USER_AGENTS = [
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

MAX_RETRIES = 3


def slugify(text: str) -> str:
    return quote("-".join(text.lower().split()), safe="")


def parse_location(location: Optional[str]) -> tuple[str, Optional[str]]:
    """"Cairo, Egypt" -> ("egypt", "cairo"); country alone -> (country, None)."""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return "international", None
    country = COUNTRY_ALIASES.get(parts[-1].lower(), slugify(parts[-1]))
    city = slugify(parts[0]) if len(parts) > 1 else None
    if city and city == country:
        city = None
    return country, city


class BaytAdapter(DiscoveryAdapter):
    """Discovers MENA tech jobs from Bayt's public search pages."""

    adapter_id = "bayt"

    def matches_url(self, url: str) -> bool:
        return "bayt.com" in url.lower()

    def _search_url(
        self, query: str, country: str, city: Optional[str], page: int,
        hours_old: Optional[int], remote_only: bool,
    ) -> str:
        slug = slugify(query or "")
        if not slug:
            path = ""
        elif city:
            path = f"{slug}-jobs-in-{city}/"
        else:
            path = f"{slug}-jobs/"
        filters: Dict[str, Any] = {}
        if hours_old:
            interval = next((v for hours, v in DATE_INTERVALS if hours_old <= hours), None)
            if interval:
                filters["jb_last_modification_date_interval"] = interval
        if remote_only:
            filters["remote_working_type"] = 1
        params = {f"filters[{k}][]": v for k, v in filters.items()} | {"page": page}
        return f"{BASE_URL}/en/{country}/jobs/{path}?{urllib.parse.urlencode(params)}"

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Cairo, Egypt"]
        max_pages = entry.get("max_pages_per_query", 2)
        target_count = entry.get("target_jobs_count", 40)
        hours_old = entry.get("hours_old")
        remote_only = entry.get("remote_only", False)

        postings: List[JobPosting] = []
        seen_ids = set()
        for kw in queries:
            if len(postings) >= target_count:
                break
            for loc in locations:
                if len(postings) >= target_count:
                    break
                country, city = parse_location(loc)
                page = 1
                while len(postings) < target_count and page <= max_pages:
                    if page > 1:
                        await asyncio.sleep(random.uniform(2.0, 5.0))
                    url = self._search_url(kw, country, city, page, hours_old, remote_only)
                    batch = await self._fetch_page(client, url, hours_old)
                    if batch is None:  # HTTP-level failure: stop this query
                        break
                    new = [p for p in batch if p.external_id not in seen_ids]
                    if not new:
                        break
                    seen_ids.update(p.external_id for p in new)
                    postings.extend(new)
                    page += 1

        logger.info("Bayt discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    async def _fetch_page(
        self, client: httpx.AsyncClient, url: str, hours_old: Optional[int]
    ) -> Optional[List[JobPosting]]:
        # Bayt's Cloudflare wall blocks plain httpx: use TLS impersonation.
        headers = {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        for attempt in range(MAX_RETRIES):
            try:
                status, text, _ = await fetch_text(url, headers=headers, timeout=20)
            except Exception as exc:
                logger.debug("Bayt request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            if status == 429:
                await asyncio.sleep(5.0 * (2 ** attempt))
                continue
            if status == 404:
                logger.info("Bayt location not found, widening is manual: %s", url)
                return []
            if status != 200:
                logger.debug("Bayt status %d for %s", status, url)
                return None
            return self._parse_cards(text, hours_old)
        return None

    def _parse_cards(self, html: str, hours_old: Optional[int]) -> List[JobPosting]:
        soup = BeautifulSoup(html, "html.parser")
        postings: List[JobPosting] = []
        for card in soup.select("li[data-js-job]"):
            try:
                posting = self._parse_card(card, hours_old)
            except Exception as exc:
                logger.debug("Skipping malformed Bayt card: %s", exc)
                continue
            if posting is not None:
                postings.append(posting)
        return postings

    def _parse_card(self, card: Any, hours_old: Optional[int]) -> Optional[JobPosting]:
        link = card.select_one("h2 a[href]")
        if not link:
            return None
        job_id = card.get("data-job-id") or ""
        raw_url = urljoin(BASE_URL, link["href"].strip().split("?")[0])
        title = link.get_text(strip=True)
        if not title or not raw_url:
            return None

        posted = card.select_one("[data-automation-jobactivedate]")
        stamp = posted.get("data-automation-jobactivedate", "") if posted else ""
        timestamp = int(stamp) if stamp and stamp.isdigit() else None
        if hours_old and timestamp and timestamp < time.time() - hours_old * 3600:
            return None

        company_wrap = card.select_one("h2 + div.job-company-location-wrapper")
        company_link = company_wrap.select_one("a[href*='/company/']") if company_wrap else None
        company = (company_wrap.get_text(strip=True) or "Bayt Employer") if company_wrap else "Bayt Employer"
        company_url = urljoin(BASE_URL, company_link["href"]) if company_link else None

        loc_tag = card.select_one("dt.jb-label-location")
        loc_parts = [s.get_text(strip=True) for s in loc_tag.find_all("span")] if loc_tag else []
        location = ", ".join(loc_parts) if loc_parts else "Middle East"

        salary_tag = card.select_one("dt.jb-label-salary")
        salary_min, salary_max, salary_currency = None, None, None
        if salary_tag:
            m = re.fullmatch(
                r"([A-Z]{3}|\$) ?([\d,]+(?:\.\d+)?) - (?:[A-Z]{3}|\$)? ?([\d,]+(?:\.\d+)?)",
                salary_tag.get_text(" ", strip=True),
            )
            if m:
                cur, low, high = m.groups()
                salary_currency = "USD" if cur == "$" else cur
                salary_min, salary_max = float(low.replace(",", "")), float(high.replace(",", ""))

        is_remote = card.select_one("dt.jb-label-remote") is not None
        description = f"{title} position at {company}. Location: {location}. Discovered via Bayt."
        canon_url = normalize_url(raw_url)
        metadata: Dict[str, Any] = {"source_platform": "bayt", "remote_flag": is_remote}
        if company_url:
            metadata["company_url"] = company_url

        return JobPosting(
            external_id=f"bayt-{job_id}" if job_id else None,
            source="bayt",
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
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
            metadata=metadata,
        )
