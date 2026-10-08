"""Universal web discovery adapter with Schema.org JSON-LD and HTML job card extraction."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx

from job_hunt.dedup import (
    canonical_url_hash,
    compute_role_fingerprint,
    content_hash,
    normalize_url,
)
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.discovery.salary_parse import parse_salary_text
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)


def _extract_stash_json(html: str) -> Optional[Any]:
    """Extract `var Stash = {...};` via balanced-brace scanning.

    A non-greedy `\\{.*?\\}` regex stops at the first nested closing brace and
    corrupts any payload with nested objects; scanning with string-awareness
    (single/double quotes + backslash escapes) finds the true matching brace.
    Returns the parsed JSON value, or None when absent/unparseable.
    """
    m = re.search(r"var\s+Stash\s*=", html)
    if not m:
        return None
    start = html.find("{", m.end())
    if start < 0:
        return None
    depth = 0
    quote: Optional[str] = None
    escaped = False
    for i in range(start, len(html)):
        ch = html[i]
        if quote is not None:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start : i + 1])
                except Exception:
                    return None
    return None


def extract_hydrated_jobs(html: str) -> list:
    """Third fallback: SPA-hydrated state when JSON endpoints 204/404."""
    out = []
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if m:
        try:
            out.extend(_find_job_dicts(json.loads(m.group(1))))
        except Exception:
            pass
        if out:
            return out
    stash = _extract_stash_json(html)
    if stash is not None:
        out.extend(_find_job_dicts(stash))
    return out


def _find_job_dicts(node, depth=0):
    found = []
    if depth > 6 or not isinstance(node, (dict, list)):
        return found
    items = node.values() if isinstance(node, dict) else node
    for v in items:
        if isinstance(v, dict) and isinstance(v.get("title"), str) and v.get("url"):
            found.append({"title": v["title"], "company": v.get("company", ""), "url": v["url"], "location": v.get("location", ""), "description": v.get("description", "")})
        else:
            found.extend(_find_job_dicts(v, depth + 1))
    return found


class UniversalWebAdapter(DiscoveryAdapter):
    """Fetches job postings from web portals using Schema.org JobPosting LD+JSON and HTML heuristics."""

    adapter_id = "web"

    def matches_url(self, url: str) -> bool:
        # Matches any valid web portal url
        return url.startswith("http://") or url.startswith("https://")

    def _extract_schema_org_jobs(self, html: str, base_url: str, source_name: str) -> List[JobPosting]:
        """Extract jobs from <script type="application/ld+json"> containing JobPosting."""
        postings: List[JobPosting] = []
        script_pattern = re.compile(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            re.DOTALL | re.IGNORECASE,
        )

        for match in script_pattern.finditer(html):
            raw_json = match.group(1).strip()
            try:
                data = json.loads(raw_json)
            except Exception:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("@type", "")
                if item_type == "JobPosting" or (isinstance(item_type, list) and "JobPosting" in item_type):
                    title = item.get("title") or item.get("name") or ""
                    if not title:
                        continue

                    # Company
                    hiring_org = item.get("hiringOrganization")
                    if isinstance(hiring_org, dict):
                        company = hiring_org.get("name") or source_name
                    elif isinstance(hiring_org, str):
                        company = hiring_org
                    else:
                        company = source_name

                    # URL
                    raw_url = item.get("url")
                    if not raw_url:
                        raw_url = base_url
                    elif not raw_url.startswith("http"):
                        raw_url = urllib.parse.urljoin(base_url, raw_url)

                    canon_url = normalize_url(raw_url)

                    # Location
                    job_loc = item.get("jobLocation")
                    loc_str = "Remote"
                    if isinstance(job_loc, dict):
                        address = job_loc.get("address", {})
                        if isinstance(address, dict):
                            loc_parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
                            loc_str = ", ".join([p for p in loc_parts if p]) or "Remote"
                        elif isinstance(address, str):
                            loc_str = address

                    description = item.get("description") or title

                    # Backstop: no structured compensation on web pages; try free-text parse.
                    parsed = parse_salary_text(description)
                    postings.append(
                        JobPosting(
                            external_id=str(item.get("identifier", {}).get("value") if isinstance(item.get("identifier"), dict) else item.get("identifier") or ""),
                            source=source_name.lower().replace(" ", "_"),
                            source_name=company,
                            title=title.strip(),
                            company=company.strip(),
                            raw_url=raw_url,
                            canonical_url=canon_url,
                            canonical_url_hash=canonical_url_hash(canon_url),
                            role_fingerprint=compute_role_fingerprint(company, title, loc_str),
                            content_hash=content_hash(description),
                            location=loc_str,
                            description=description,
                            salary_min=parsed[0] if parsed else None,
                            salary_max=parsed[1] if parsed else None,
                            salary_currency=parsed[2] if parsed else None,
                            salary_source=parsed[3] if parsed else None,
                            posted_at=item.get("datePosted"),
                            metadata={"schema_org": True},
                        )
                    )

        return postings

    def _extract_html_link_jobs(self, html: str, base_url: str, source_name: str) -> List[JobPosting]:
        """Fallback extractor: finds job links and titles from HTML anchor and article patterns."""
        postings: List[JobPosting] = []

        # Find links with /job/, /jobs/, /careers/, /posting/ in href
        link_pattern = re.compile(
            r'<a[^>]+href=["\']([^"\']*(?:/job/|/jobs/|/careers/|/position/)[^"\']*)["\'][^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )

        seen_urls = set()
        for match in link_pattern.finditer(html):
            raw_href = match.group(1).strip()
            anchor_text = re.sub(r"<[^>]+>", " ", match.group(2)).strip()

            # Filter out empty or pagination anchor text
            if len(anchor_text) < 4 or any(p in anchor_text.lower() for p in ("next", "previous", "view all", "see more")):
                continue

            full_url = urllib.parse.urljoin(base_url, raw_href)
            canon_url = normalize_url(full_url)
            if not canon_url or canon_url in seen_urls:
                continue
            seen_urls.add(canon_url)

            # Heuristic: title is anchor text
            title = anchor_text.split("\n")[0].strip()
            if not title:
                continue

            company = source_name
            loc_str = "Remote"

            # Backstop: no structured compensation on web pages; try free-text parse.
            parsed = parse_salary_text(title)
            postings.append(
                JobPosting(
                    source=source_name.lower().replace(" ", "_"),
                    source_name=company,
                    title=title,
                    company=company,
                    raw_url=full_url,
                    canonical_url=canon_url,
                    canonical_url_hash=canonical_url_hash(canon_url),
                    role_fingerprint=compute_role_fingerprint(company, title, loc_str),
                    content_hash=content_hash(title),
                    location=loc_str,
                    description=title,
                    salary_min=parsed[0] if parsed else None,
                    salary_max=parsed[1] if parsed else None,
                    salary_currency=parsed[2] if parsed else None,
                    salary_source=parsed[3] if parsed else None,
                    metadata={"html_heuristic": True},
                )
            )

        return postings

    def _hydrated_dict_to_posting(
        self, item: Dict[str, Any], base_url: str, source_name: str
    ) -> Optional[JobPosting]:
        """Convert one hydrated-state job dict to a JobPosting.

        URL-less dicts are skipped (no stable job identity, so no
        external_id); unknown locations stay "Unknown" (never defaulted to
        "Remote"); source/source_name are the fixed adapter id, never the
        per-entry dynamic name.
        """
        title = item.get("title", "")
        company = item.get("company") or source_name
        raw_url = (item.get("url") or "").strip()
        if not raw_url:
            return None
        if not raw_url.startswith("http"):
            raw_url = urllib.parse.urljoin(base_url, raw_url)
        canon_url = normalize_url(raw_url)
        loc_str = item.get("location") or "Unknown"
        description = item.get("description") or title
        parsed = parse_salary_text(description)
        return JobPosting(
            external_id=raw_url,
            source=self.adapter_id,
            source_name=self.adapter_id,
            title=title,
            company=company,
            raw_url=raw_url,
            canonical_url=canon_url,
            canonical_url_hash=canonical_url_hash(canon_url),
            role_fingerprint=compute_role_fingerprint(company, title, loc_str),
            content_hash=content_hash(description),
            location=loc_str,
            description=description,
            salary_min=parsed[0] if parsed else None,
            salary_max=parsed[1] if parsed else None,
            salary_currency=parsed[2] if parsed else None,
            salary_source=parsed[3] if parsed else None,
            metadata={"hydrated_spa": True},
        )

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("url") or entry.get("careers_url") or ""
        name = entry.get("name", "web_portal")
        if not url:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

        try:
            response = await client.get(url, headers=headers, timeout=15.0, follow_redirects=True)
            if response.status_code != 200:
                return []

            html = response.text
            # 1. Try Schema.org JSON-LD
            jobs = self._extract_schema_org_jobs(html, url, name)
            if jobs:
                return jobs

            # 2. Try HTML link heuristic
            heur_jobs = self._extract_html_link_jobs(html, url, name)
            if heur_jobs:
                return heur_jobs

            # 3. Hydrated-SPA fallback (JSON-dead boards), capped at 40 postings.
            hydrated = extract_hydrated_jobs(html)[:40]
            postings = []
            for j in hydrated:
                posting = self._hydrated_dict_to_posting(j, url, name)
                if posting is not None:
                    postings.append(posting)
            return postings
        except Exception as e:
            logger.debug("Web adapter fetch failed for %s (%s): %s", name, url, e)
            return []
