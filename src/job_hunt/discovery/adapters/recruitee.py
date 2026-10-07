"""Recruitee per-tenant public offers API discovery adapter.

Each tenant exposes https://<tenant>.recruitee.com/api/offers/ returning
{"offers": [...]} with numeric `id`, `title`, `slug`, `careers_url`,
embedded `description`/`requirements`, `remote` flag, and `published_at`.
No auth required.
"""

from __future__ import annotations

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


class RecruiteeAdapter(DiscoveryAdapter):
    """Fetches job postings from a Recruitee tenant's public offers API."""

    adapter_id = "recruitee"

    def matches_url(self, url: str) -> bool:
        try:
            host = (urlsplit(url).hostname or "").lower()
        except Exception:
            return False
        return host == "recruitee.com" or host.endswith(".recruitee.com")

    def _api_url(self, entry: Dict[str, Any]) -> str:
        url = (entry.get("url") or entry.get("careers_url") or "").strip()
        if "/api/offers" in url:
            return url
        slug = (entry.get("tenant") or entry.get("org") or "").strip()
        if not slug and url:
            try:
                host = (urlsplit(url).hostname or "").lower()
                if host.endswith(".recruitee.com"):
                    slug = host[: -len(".recruitee.com")].split(".")[0]
            except Exception:
                slug = ""
        if not slug:
            return ""
        return f"https://{slug}.recruitee.com/api/offers/"

    @staticmethod
    def _location(offer: Dict[str, Any]) -> str:
        parts: List[str] = []
        if offer.get("remote"):
            parts.append("Remote")
        for key in ("city", "country"):
            val = (offer.get(key) or "").strip()
            if val and val.lower() != "remote" and val not in parts:
                parts.append(val)
        if not parts:
            locs = offer.get("locations") or []
            if locs and isinstance(locs[0], dict):
                name = (locs[0].get("name") or "").strip()
                if name:
                    parts.append(name)
        if not parts:
            fallback = (offer.get("location") or "").strip()
            if fallback:
                parts.append(fallback)
        return ", ".join(parts)

    async def fetch(self, entry: Dict[str, Any], client: httpx.AsyncClient) -> List[JobPosting]:
        api_url = self._api_url(entry)
        if not api_url:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept": "application/json, */*",
        }
        resp = await client.get(api_url, headers=headers, timeout=15.0, follow_redirects=True)
        if resp.status_code != 200:
            return []

        try:
            data = resp.json()
        except Exception as exc:
            raise ValueError(f"Recruitee endpoint {api_url} did not return JSON: {exc}")
        if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
            raise ValueError(
                f"Recruitee endpoint {api_url} returned unexpected shape "
                f"(expected {{\"offers\": [...]}})"
            )

        company = entry.get("name") or ""
        postings: List[JobPosting] = []
        for offer in data["offers"]:
            if not isinstance(offer, dict):
                continue
            title = (offer.get("title") or "").strip()
            if not title:
                translations = offer.get("translations") or {}
                en = translations.get("en") or {}
                title = (en.get("title") or "").strip()
            raw_url = offer.get("careers_url") or ""
            if not title or not raw_url:
                continue

            company_name = offer.get("company_name") or company
            canon_url = normalize_url(raw_url)
            location_str = self._location(offer)
            desc_parts = [p for p in (offer.get("description"), offer.get("requirements")) if p]
            desc = "\n".join(desc_parts) or title
            posting = JobPosting(
                external_id=str(offer.get("id")),
                source="recruitee",
                source_name=company_name or None,
                title=title,
                company=company_name,
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company_name, title, location_str),
                content_hash=content_hash(desc),
                location=location_str,
                description=desc,
                posted_at=offer.get("published_at"),
                metadata={
                    "slug": offer.get("slug"),
                    "department": offer.get("department"),
                    "employment_type": offer.get("employment_type_code"),
                },
            )
            postings.append(posting)
        return postings
