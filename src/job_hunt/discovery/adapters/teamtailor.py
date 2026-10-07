"""Teamtailor per-tenant public RSS discovery adapter.

The global Teamtailor API (api.teamtailor.com) is token-walled; each tenant
publishes a public RSS feed at https://<tenant>.teamtailor.com/jobs.rss with
<item> blocks (title/link/guid/pubDate/description/remoteStatus/tt:location).
Multi-level subdomains exist (e.g. softwarefinder.na.teamtailor.com), so host
matching uses an endswith check, not a single-label regex.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
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
from job_hunt.discovery.salary_parse import parse_salary_text
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)


class TeamtailorAdapter(DiscoveryAdapter):
    """Fetches job postings from a Teamtailor tenant's public jobs.rss feed."""

    adapter_id = "teamtailor"

    def matches_url(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return host == "teamtailor.com" or host.endswith(".teamtailor.com")

    def _feed_url(self, entry: Dict[str, Any]) -> str:
        url = (entry.get("url") or entry.get("careers_url") or "").strip()
        if not url:
            return ""
        if url.lower().endswith(".rss"):
            return url
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if not host:
                return ""
            scheme = parts.scheme or "https"
            return f"{scheme}://{host}/jobs.rss"
        except Exception:
            return ""

    @staticmethod
    def _location(item: ET.Element) -> str:
        city = ""
        country = ""
        name = ""
        remote_status = ""
        for child in item.iter():
            tag = child.tag.lower()
            if tag.endswith("city") and child.text:
                city = child.text.strip()
            elif tag.endswith("country") and child.text:
                country = child.text.strip()
            elif tag.endswith("remotstatus") or tag.endswith("remotestatus"):
                remote_status = (child.text or "").strip()
            elif tag.endswith("}name") or tag == "tt:name":
                if not name and child.text:
                    name = child.text.strip()
        parts = [p for p in (city, country) if p]
        loc = ", ".join(parts) or name
        if re.search(r"remote", remote_status, re.IGNORECASE):
            loc = f"{loc} (Remote)" if loc else "Remote"
        return loc

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        feed_url = self._feed_url(entry)
        if not feed_url:
            return []

        company = entry.get("name") or ""
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        resp = await client.get(feed_url, headers=headers, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return []

        try:
            root = ET.fromstring(resp.text.strip())
        except ET.ParseError as exc:
            raise ValueError(f"Teamtailor feed at {feed_url} is not parseable RSS/XML: {exc}")

        items = root.findall(".//item") or [
            el for el in root.iter() if el.tag.lower().endswith("item")
        ]
        postings: List[JobPosting] = []
        for item in items:
            title = ""
            raw_url = ""
            guid = ""
            description = ""
            posted_at = ""
            for child in item:
                tag = child.tag.lower()
                if tag.endswith("title") and not title:
                    title = (child.text or "").strip()
                elif tag.endswith("link") and not raw_url:
                    raw_url = (child.text or "").strip()
                elif tag.endswith("guid") and not guid:
                    guid = (child.text or "").strip()
                elif tag.endswith("description") and not description:
                    description = (child.text or "").strip()
                elif tag.endswith("pubdate"):
                    posted_at = (child.text or "").strip()
            if not title or not raw_url:
                continue

            canon_url = normalize_url(raw_url)
            location_str = self._location(item)
            desc = description or title
            parsed = parse_salary_text(desc)
            posting = JobPosting(
                external_id=raw_url,
                source="teamtailor",
                source_name=company or None,
                title=title,
                company=company,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company, title, location_str),
                content_hash=content_hash(desc),
                location=location_str,
                description=desc,
                salary_min=parsed[0] if parsed else None,
                salary_max=parsed[1] if parsed else None,
                salary_currency=parsed[2] if parsed else None,
                salary_source=parsed[3] if parsed else None,
                posted_at=posted_at or None,
                metadata={"guid": guid, "feed_format": "teamtailor_rss"},
            )
            postings.append(posting)
        return postings
