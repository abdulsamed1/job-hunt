"""ZipRecruiter discovery adapter (US/Canada).

Native httpx implementation modeled on JobSpy's ZipRecruiter scraper mechanics
(public jobs-search pages with embedded Next.js flight JSON; MIT,
speedyapply/JobSpy). No login. US/Canada coverage only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import random
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

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

BASE_URL = "https://www.ziprecruiter.com"
JOBS_PER_PAGE = 20

PAY_INTERVALS = {
    "PAY_INTERVAL_HOUR": "hourly",
    "PAY_INTERVAL_DAY": "daily",
    "PAY_INTERVAL_WEEK": "weekly",
    "PAY_INTERVAL_MONTH": "monthly",
    "PAY_INTERVAL_YEAR": "yearly",
}

MAX_RETRIES = 3


def page_values(html: str, *keys: str) -> list:
    """Extract embedded Next.js flight values for keys (stdlib JSON only)."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html)
    data = "".join(json.loads(chunk) for chunk in chunks)

    def fill(obj: dict) -> dict:
        for name, value in obj.items():
            if isinstance(value, str) and value.startswith("$"):
                text = value[1:] if value.startswith("$$") else value
                obj[name] = texts.get(value, text)
        return obj

    texts = _text_rows(data)
    decoder = json.JSONDecoder(object_hook=fill)
    values = []
    for key in keys:
        start = data.find(f'"{key}":')
        if start == -1:
            raise ValueError(f"no {key} in the page")
        values.append(decoder.raw_decode(data, start + len(key) + 3)[0])
    return values


def _text_rows(data: str) -> Dict[str, str]:
    raw, texts, pos = data.encode(), {}, 0
    row = re.compile(rb"([0-9a-f]*):(?:T([0-9a-f]+),)?")
    while match := row.match(raw, pos):
        if match[2]:
            pos = match.end() + int(match[2], 16)
            texts[f"${match[1].decode()}"] = raw[match.end():pos].decode()
        else:
            pos = raw.find(b"\n", pos) + 1 or len(raw)
    return texts


def direct_url(redirect_url: Optional[str]) -> Optional[str]:
    """Recover the employer's outbound link from a match token, if present."""
    if not redirect_url:
        return None
    try:
        token = parse_qs(urlsplit(redirect_url).query)["match_token"][0]
        fields = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        start = re.search(rb"https?://", fields).start()
        length = fields[start - 1]
        if fields[start - 2] >= 0x80:
            length = fields[start - 2] - 0x80 + length * 0x80
        url = fields[start:start + length].decode()
        return re.sub(r"\[\w+\]", "", url)
    except Exception:
        return None


class ZipRecruiterAdapter(DiscoveryAdapter):
    """Discovers US/Canada jobs from ZipRecruiter's public search pages."""

    adapter_id = "ziprecruiter"

    def matches_url(self, url: str) -> bool:
        return "ziprecruiter.com" in url.lower()

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Austin, TX"]
        max_pages = entry.get("max_pages_per_query", 2)
        target_count = entry.get("target_jobs_count", 40)
        hours_old = entry.get("hours_old")
        remote_only = entry.get("remote_only", False)

        postings: List[JobPosting] = []
        seen_keys = set()
        for kw in queries:
            if len(postings) >= target_count:
                break
            for loc in locations:
                if len(postings) >= target_count:
                    break
                page = 1
                while len(postings) < target_count and page <= max_pages:
                    if page > 1:
                        await asyncio.sleep(random.uniform(3.0, 7.0))
                    batch = await self._fetch_page(client, kw, loc, page, hours_old, remote_only)
                    if batch is None:
                        break
                    new = [p for p in batch if p.external_id not in seen_keys]
                    if not new:
                        break
                    seen_keys.update(p.external_id for p in new)
                    postings.extend(new)
                    page += 1

        logger.info("ZipRecruiter discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    def _search_params(
        self, keyword: str, location: Optional[str], hours_old: Optional[int],
        remote_only: bool,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {"search": keyword or "", "location": location or ""}
        if hours_old:
            params["days"] = math.ceil(hours_old / 24)
        if remote_only:
            params["refine_by_location_type"] = "only_remote"
        return params

    async def _fetch_page(
        self, client: httpx.AsyncClient, keyword: str, location: Optional[str],
        page: int, hours_old: Optional[int], remote_only: bool,
    ) -> Optional[List[JobPosting]]:
        path = "/jobs-search" if page == 1 else f"/jobs-search/{page}"
        url = BASE_URL + path
        params = self._search_params(keyword, location, hours_old, remote_only)
        for attempt in range(MAX_RETRIES):
            try:
                status, html, _ = await fetch_text(
                    url, params=params,
                    headers={"Accept-Language": "en-US,en;q=0.9"}, timeout=20,
                )
            except Exception as exc:
                logger.debug("ZipRecruiter request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            if status == 429:
                await asyncio.sleep(5.0 * (2 ** attempt))
                continue
            if status != 200:
                logger.debug("ZipRecruiter status %d", status)
                return None
            try:
                jobs_map, _count = page_values(html, "jobKeysMap", "jobCount")
            except (ValueError, KeyError) as exc:
                logger.debug("ZipRecruiter page has no job data: %s", exc)
                return []
            postings: List[JobPosting] = []
            for key, job in (jobs_map or {}).items():
                try:
                    posting = self._parse_job(str(key), job if isinstance(job, dict) else {}, hours_old)
                except Exception as exc:
                    logger.debug("Skipping malformed ZipRecruiter job: %s", exc)
                    continue
                if posting is not None:
                    postings.append(posting)
            return postings
        return None

    def _parse_job(
        self, map_key: str, job: Dict[str, Any], hours_old: Optional[int]
    ) -> Optional[JobPosting]:
        title = job.get("title") or ""
        listing_key = job.get("listingKey") or map_key
        page_path = job.get("rawCanonicalZipJobPageUrl") or ""
        if not title or not page_path:
            return None

        try:
            posted = datetime.fromisoformat(
                (job.get("status") or {}).get("postedAtUtc", "").replace("Z", "+00:00")
            )
        except (ValueError, TypeError):
            posted = None
        if hours_old and posted and posted.timestamp() < datetime.now(timezone.utc).timestamp() - hours_old * 3600:
            return None

        raw_url = BASE_URL + page_path
        company = (job.get("company") or {}).get("name") or "ZipRecruiter Employer"
        loc = job.get("location") or {}
        city = loc.get("city")
        state = loc.get("stateCode")
        location = ", ".join(p for p in (city, state) if p) or "USA"
        is_remote = any("REMOTE" in (t.get("name") or "") for t in (job.get("locationTypes") or []))
        if is_remote:
            location = f"{location} (Remote)"

        pay = job.get("pay") or {}
        salary_min = salary_max = salary_currency = None
        if (pay.get("metadata") or {}).get("visible"):
            try:
                salary_min = float(pay["min"])
                salary_max = float(pay["max"])
                salary_currency = (pay.get("currency") or "").removeprefix("PAY_CURRENCY_") or None
            except (KeyError, TypeError, ValueError):
                pass

        apply_cfg = job.get("applyButtonConfig") or {}
        outbound = direct_url(apply_cfg.get("externalApplyUrl"))
        description = f"{title} position at {company}. Location: {location}. Discovered via ZipRecruiter."
        canon_url = normalize_url(raw_url)
        metadata: Dict[str, Any] = {"source_platform": "ziprecruiter", "remote_flag": is_remote}
        if outbound:
            metadata["job_url_direct"] = outbound

        return JobPosting(
            external_id=f"zr-{listing_key}",
            source="ziprecruiter",
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
            posted_at=posted.date().isoformat() if posted else None,
            metadata=metadata,
        )
