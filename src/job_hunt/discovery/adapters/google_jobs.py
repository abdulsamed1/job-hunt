"""Google Jobs discovery adapter (global widget scraping).

Native httpx implementation modeled on JobSpy's Google scraper mechanics
(search?q=+udm=8 widget, async cursor pagination; MIT, speedyapply/JobSpy).
Job entries are located by structural shape (title/company/location/url/id
positions), found recursively so markup drift degrades gracefully.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
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
from job_hunt.discovery.tls_fetch import fetch_text
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.google.com/search"
ASYNC_URL = "https://www.google.com/async/callback:550"
# Public async pagination blob, same role as JobSpy's async_param constant.
ASYNC_PARAM = (
    "_fmt:prog,_id:fc_5FwaZ86OKsfdwN4P4La3yA4_2"
)

HEADERS_INITIAL = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.google.com/",
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
}

MAX_RETRIES = 2


def find_job_info(node: Any) -> Optional[List[Any]]:
    """Recursively find a job-info-shaped list: [title, company, location, url-nest, ...].

    Shape contract: index 0/1/2 are non-empty strings, index 3 nests a URL,
    index 28 carries the posting id. Tolerant of surrounding markup changes.
    """
    if isinstance(node, dict):
        if isinstance(node.get("520084652"), list):
            return node["520084652"]
        for value in node.values():
            found = find_job_info(value)
            if found:
                return found
    elif isinstance(node, list):
        if (
            len(node) > 28
            and isinstance(node[0], str) and node[0].strip()
            and isinstance(node[1], str) and node[1].strip()
            and isinstance(node[2], str) and node[2].strip()
            and isinstance(node[28], (str, int))
        ):
            return node
        for item in node:
            found = find_job_info(item)
            if found:
                return found
    return None


def extract_infos(payload: Any) -> List[List[Any]]:
    """Collect every job-info-shaped list in a decoded JSON payload."""
    found: List[List[Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            candidate = find_job_info(node)
            if candidate is not None and candidate not in found:
                # find_job_info returns the first; keep walking siblings
                found.append(candidate)
            for item in node:
                if item is not candidate:
                    walk(item)

    walk(payload)
    return found


def _balanced_spans(text: str) -> List[Any]:
    """Parse every top-level balanced JSON value in a blob of text/HTML."""
    out: List[Any] = []
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] not in "{[":
            i += 1
        if i >= n:
            break
        depth = 0
        in_str = False
        esc = False
        j = i
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "[{":
                    depth += 1
                elif ch in "]}":
                    depth -= 1
                    if depth == 0:
                        try:
                            out.append(json.loads(text[i:j + 1]))
                        except ValueError:
                            pass
                        break
            j += 1
        i = j + 1 if j < n else n
    return out


def _job_json_blobs(html: str) -> List[Any]:
    """Best-effort extraction of JSON payloads from a Google jobs page."""
    blobs = _balanced_spans(html)
    if not blobs:
        try:
            blobs.append(json.loads(html))
        except ValueError:
            pass
    return blobs


class GoogleJobsAdapter(DiscoveryAdapter):
    """Discovers jobs worldwide via Google's public jobs widget."""

    adapter_id = "google"

    def matches_url(self, url: str) -> bool:
        lowered = url.lower()
        return "google.com/search" in lowered

    def _query(
        self, search_term: str, location: Optional[str], hours_old: Optional[int],
        remote_only: bool, custom: Optional[str] = None,
    ) -> str:
        if custom:
            return custom
        query = f"{search_term or ''} jobs".strip()
        if location:
            query += f" near {location}"
        if hours_old:
            if hours_old <= 24:
                query += " since yesterday"
            elif hours_old <= 72:
                query += " in the last 3 days"
            elif hours_old <= 168:
                query += " in the last week"
            else:
                query += " in the last month"
        if remote_only:
            query += " remote"
        return query

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Remote"]
        max_pages = entry.get("max_pages_per_query", 2)
        target_count = entry.get("target_jobs_count", 40)
        hours_old = entry.get("hours_old")
        remote_only = entry.get("remote_only", False)
        custom = entry.get("google_search_term")

        postings: List[JobPosting] = []
        seen_ids = set()
        for kw in queries:
            if len(postings) >= target_count:
                break
            for loc in locations:
                if len(postings) >= target_count:
                    break
                query = self._query(kw, loc, hours_old, remote_only, custom)
                cursor, batch = await self._initial_page(client, query)
                for posting in batch:
                    if posting.external_id not in seen_ids:
                        seen_ids.add(posting.external_id)
                        postings.append(posting)
                page = 1
                while len(postings) < target_count and cursor and page < max_pages:
                    if page > 0:
                        await asyncio.sleep(random.uniform(2.0, 5.0))
                    cursor, batch = await self._next_page(client, cursor)
                    for posting in batch:
                        if posting.external_id not in seen_ids:
                            seen_ids.add(posting.external_id)
                            postings.append(posting)
                    page += 1

        logger.info("Google Jobs discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    async def _get(
        self, client: httpx.AsyncClient, url: str, params: Dict[str, Any]
    ) -> Optional[str]:
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.get(url, params=params, headers=HEADERS_INITIAL, timeout=20.0)
            except Exception as exc:
                logger.debug("Google request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            if resp.status_code == 429:
                await asyncio.sleep(5.0 * (2 ** attempt))
                continue
            if resp.status_code != 200:
                return None
            return resp.text
        return None

    async def _initial_page(
        self, client: httpx.AsyncClient, query: str
    ) -> Tuple[Optional[str], List[JobPosting]]:
        html = await self._get(client, SEARCH_URL, {"q": query, "udm": "8"})
        if html:
            cursor, batch = self._parse_jobs(html)
            if batch or cursor:
                return cursor, batch
        # Fallback: TLS-impersonated fetch (bot-shaped plain responses).
        try:
            _status, text, _ = await fetch_text(
                SEARCH_URL, params={"q": query, "udm": "8"},
                headers=dict(HEADERS_INITIAL), timeout=20,
            )
            if text:
                return self._parse_jobs(text)
        except Exception as exc:
            logger.debug("Google fallback fetch failed: %s", exc)
        return None, []

    async def _next_page(
        self, client: httpx.AsyncClient, cursor: str
    ) -> Tuple[Optional[str], List[JobPosting]]:
        html = await self._get(
            client, ASYNC_URL,
            {"fc": [cursor], "fcv": ["3"], "async": [ASYNC_PARAM]},
        )
        if not html:
            return None, []
        return self._parse_jobs(html)

    def _parse_jobs(self, html: str) -> Tuple[Optional[str], List[JobPosting]]:
        cursor_match = re.search(r'data-async-fc="([^"]+)"', html)
        cursor = cursor_match.group(1) if cursor_match else None
        postings: List[JobPosting] = []
        seen: set = set()
        for blob in _job_json_blobs(html):
            for info in extract_infos(blob):
                try:
                    posting = self._parse_job(info)
                except Exception as exc:
                    logger.debug("Skipping malformed Google job: %s", exc)
                    continue
                if posting is not None and posting.external_id not in seen:
                    seen.add(posting.external_id)
                    postings.append(posting)
        return cursor, postings

    def _parse_job(self, info: List[Any]) -> Optional[JobPosting]:
        def at(i: int) -> Any:
            return info[i] if len(info) > i else None

        title = at(0) if isinstance(at(0), str) else None
        company = at(1) if isinstance(at(1), str) else None
        location_raw = at(2) if isinstance(at(2), str) else None
        url: Optional[str] = None
        try:
            nested = at(3)
            url = nested[0][0] if nested and nested[0] else None
        except (IndexError, TypeError):
            url = None
        job_id = at(28)
        if not title or not company or not job_id:
            return None

        city = state = country = None
        if location_raw and "," in location_raw:
            parts = [p.strip() for p in location_raw.split(",")]
            city = parts[0] or None
            state = parts[1] if len(parts) > 1 else None
            country = parts[2] if len(parts) > 2 else None
        elif location_raw:
            city = location_raw.strip() or None
        location = ", ".join(p for p in (city, state, country) if p) or "Unknown"

        days_ago_raw = at(12)
        posted_at = None
        if isinstance(days_ago_raw, str):
            match = re.search(r"\d+", days_ago_raw)
            if match:
                posted_at = (
                    datetime.now(timezone.utc) - timedelta(days=int(match.group()))
                ).date().isoformat()

        description = at(19) if isinstance(at(19), str) else ""
        if not description:
            description = f"{title} position at {company}. Discovered via Google Jobs."
        is_remote = "remote" in description.lower() or "wfh" in description.lower()

        raw_url = url if isinstance(url, str) and url.startswith("http") else (
            f"https://www.google.com/search?q={urllib.parse.quote(f'{title} {company}')}"
        )
        canon_url = normalize_url(raw_url)
        return JobPosting(
            external_id=f"go-{job_id}",
            source="google",
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
            metadata={"source_platform": "google", "remote_flag": is_remote},
        )
