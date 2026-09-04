"""Generic JSON/RSS Feed, RemoteOK, and aggregator discovery adapter."""

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


class FeedAdapter(DiscoveryAdapter):
    """Fetches job postings from standard JSON feeds (RemoteOK, Himalayas, Remotive, WWR, etc.)."""

    adapter_id = "feed"

    def matches_url(self, url: str) -> bool:
        return (
            bool(re.search(r"(remoteok\.com|remotive\.com|jobicy\.com|arbeitnow\.com|weworkremotely\.com|swissdevjobs\.ch|cryptocurrencyjobs\.co|jobspresso\.co)", url, re.IGNORECASE))
            or url.endswith(".json")
            or url.endswith(".xml")
            or url.endswith(".rss")
            or "feed" in url.lower()
            or "rss" in url.lower()
            or "api" in url
        )

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        url = entry.get("api") or entry.get("url") or ""
        if not url:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, application/xml, text/xml, text/plain, text/html, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }

        response = await client.get(url, timeout=15.0, headers=headers, follow_redirects=True)
        if response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception:
            return self._parse_rss_xml(response.text, entry)

        # Handle RemoteOK or list of jobs
        if isinstance(data, list):
            jobs_raw = [j for j in data if isinstance(j, dict) and "legal" not in j]
        elif isinstance(data, dict):
            jobs_raw = data.get("jobs") or data.get("data") or data.get("postings") or data.get("results") or []
        else:
            return []

        postings: List[JobPosting] = []
        for j in jobs_raw:
            title = (j.get("position") or j.get("title") or j.get("name") or "").strip()
            company = (j.get("company") or j.get("company_name") or entry.get("name") or "").strip()
            raw_url = j.get("url") or j.get("apply_url") or ""
            if not title or not raw_url or not company:
                continue

            canon_url = normalize_url(raw_url)
            location_str = j.get("location") or ("Remote" if j.get("remote") else "")
            description = j.get("description") or title

            posting = JobPosting(
                external_id=str(j.get("id", "")),
                source=entry.get("name", "feed").lower(),
                source_name=company,
                title=title,
                company=company,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company, title, location_str),
                content_hash=content_hash(description),
                location=location_str,
                description=description,
                posted_at=str(j.get("date") or j.get("epoch") or ""),
                metadata={"tags": j.get("tags", [])},
            )
            postings.append(posting)

        return postings

    def _parse_rss_xml(self, xml_text: str, entry: Dict[str, Any]) -> List[JobPosting]:
        """Parse standard RSS/Atom XML feeds for job postings."""
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text.strip())
            # Search both unqualified tags and wildcard namespaces
            items = root.findall(".//item") or [
                el for el in root.iter() if el.tag.endswith("item") or el.tag.endswith("entry")
            ]
            postings: List[JobPosting] = []

            for item in items:
                title = ""
                raw_url = ""
                description = ""
                company = entry.get("name", "Company")
                posted_at = ""

                for child in item:
                    tag_lower = child.tag.lower()
                    if tag_lower.endswith("title"):
                        title = (child.text or "").strip()
                    elif tag_lower.endswith("link"):
                        raw_url = child.get("href") or (child.text or "").strip()
                    elif tag_lower.endswith("description") or tag_lower.endswith("content") or tag_lower.endswith("summary"):
                        description = (child.text or "").strip()
                    elif tag_lower.endswith("creator") or tag_lower.endswith("author") or tag_lower.endswith("company"):
                        company = (child.text or "").strip() or company
                    elif tag_lower.endswith("pubdate") or tag_lower.endswith("updated") or tag_lower.endswith("published"):
                        posted_at = (child.text or "").strip()

                if not title or not raw_url:
                    continue

                canon_url = normalize_url(raw_url)
                desc = description or title
                posting = JobPosting(
                    external_id=None,
                    source=entry.get("name", "rss").lower(),
                    source_name=company,
                    title=title,
                    company=company,
                    raw_url=raw_url,
                    canonical_url=canon_url,
                    canonical_url_hash=canonical_url_hash(canon_url),
                    role_fingerprint=compute_role_fingerprint(company, title, "Remote"),
                    content_hash=content_hash(desc),
                    location="Remote",
                    description=desc,
                    posted_at=posted_at or None,
                    metadata={"feed_format": "rss_xml"},
                )
                postings.append(posting)

            return postings
        except Exception:
            return []
