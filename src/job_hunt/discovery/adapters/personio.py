"""Personio per-tenant public XML feed discovery adapter.

Each tenant exposes https://<tenant>.jobs.personio.de/xml with <position>
blocks (numeric <id>, <name>, <office>, <department>, <createdAt>,
<salaryInformation>). The feed embeds raw markup inside <jobDescriptions>
that breaks naive whole-document XML parsing (unclosed CDATA), so each
position block is split by regex and its <jobDescriptions> subtree stripped
before parsing — descriptions are recovered separately via regex.
Per-job URLs follow https://<tenant>.jobs.personio.de/job/<id>.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
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

_DESCRIPTIONS_RE = re.compile(
    r"<jobDescriptions\b[^>]*>[\s\S]*?</jobDescriptions>", re.IGNORECASE
)
_POSITION_RE = re.compile(r"<position\b[^>]*>[\s\S]*?</position>", re.IGNORECASE)
_VALUE_RE = re.compile(r"<value>([\s\S]*?)</value>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


class PersonioAdapter(DiscoveryAdapter):
    """Fetches job postings from a Personio tenant's public XML feed."""

    adapter_id = "personio"

    def matches_url(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return host == "personio.de" or host.endswith(".personio.de")

    def _feed_url(self, entry: Dict[str, Any]) -> str:
        url = (entry.get("url") or entry.get("careers_url") or "").strip()
        if not url:
            return ""
        if url.lower().rstrip("/").endswith("/xml"):
            return url
        try:
            parts = urlsplit(url)
            host = parts.hostname or ""
            if not host:
                return ""
            scheme = parts.scheme or "https"
            return f"{scheme}://{host}/xml"
        except Exception:
            return ""

    @staticmethod
    def _extract_description(raw_block: str) -> str:
        chunks: List[str] = []
        for value in _VALUE_RE.findall(raw_block):
            text = value.replace("<![CDATA[", "").replace("]]>", "")
            text = _TAG_RE.sub(" ", text)
            text = html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                chunks.append(text)
        return "\n".join(chunks)[:8000]

    @staticmethod
    def _parse_block(block: str) -> Optional[Dict[str, Any]]:
        cleaned = _DESCRIPTIONS_RE.sub("", block)
        try:
            el = ET.fromstring(cleaned)
        except ET.ParseError:
            return None

        def text(tag: str) -> str:
            child = el.find(tag)
            return (child.text or "").strip() if child is not None else ""

        salary_min: Optional[float] = None
        salary_max: Optional[float] = None
        salary_currency: Optional[str] = None
        sal = el.find("salaryInformation")
        if sal is not None:
            try:
                if sal.findtext("min"):
                    salary_min = float(sal.findtext("min") or "")
            except (TypeError, ValueError):
                salary_min = None
            try:
                if sal.findtext("max"):
                    salary_max = float(sal.findtext("max") or "")
            except (TypeError, ValueError):
                salary_max = None
            salary_currency = (sal.findtext("currencyCode") or "").strip() or None

        return {
            "id": text("id"),
            "name": text("name"),
            "subcompany": text("subcompany"),
            "office": text("office"),
            "department": text("department"),
            "employment_type": text("employmentType"),
            "schedule": text("schedule"),
            "created_at": text("createdAt"),
            "salary_min": salary_min,
            "salary_max": salary_max,
            "salary_currency": salary_currency,
        }

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        feed_url = self._feed_url(entry)
        if not feed_url:
            return []

        try:
            host = (urlsplit(feed_url).hostname or "").lower()
        except Exception:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/xml, text/xml, */*",
        }
        resp = await client.get(feed_url, headers=headers, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return []

        text = resp.text
        if "<position" not in text.lower() and "workzag-jobs" not in text.lower():
            raise ValueError(
                f"Personio feed {feed_url} returned unexpected shape "
                f"(no <position> blocks or <workzag-jobs> root)"
            )

        company = entry.get("name") or ""
        postings: List[JobPosting] = []
        for raw_block in _POSITION_RE.findall(text):
            parsed = self._parse_block(raw_block)
            if not parsed or not parsed["id"] or not parsed["name"]:
                continue
            company_name = parsed["subcompany"] or company
            raw_url = f"https://{host}/job/{parsed['id']}"
            canon_url = normalize_url(raw_url)
            location_str = parsed["office"]
            desc = self._extract_description(raw_block) or (
                f"{parsed['name']} — {parsed['department']} ({parsed['office']})".strip(" —()")
                if parsed["department"] or parsed["office"]
                else parsed["name"]
            )
            posting = JobPosting(
                external_id=parsed["id"],
                source="personio",
                source_name=company_name or None,
                title=parsed["name"],
                company=company_name,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company_name, parsed["name"], location_str),
                content_hash=content_hash(desc),
                location=location_str,
                description=desc,
                salary_min=parsed["salary_min"],
                salary_max=parsed["salary_max"],
                salary_currency=parsed["salary_currency"],
                salary_source="stated" if parsed["salary_currency"] else None,
                posted_at=parsed["created_at"] or None,
                metadata={
                    "department": parsed["department"],
                    "employment_type": parsed["employment_type"],
                    "schedule": parsed["schedule"],
                },
            )
            postings.append(posting)
        return postings
