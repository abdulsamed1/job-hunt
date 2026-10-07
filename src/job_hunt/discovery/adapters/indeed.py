"""Indeed discovery adapter (native GraphQL, no browser).

Modeled on JobSpy's Indeed scraper endpoint usage (public apis.indeed.com
GraphQL; MIT, speedyapply/JobSpy). Country domains route the query; Egypt
(eg.indeed.com) is supported. No login, no extra dependencies.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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

API_URL = "https://apis.indeed.com/graphql"

API_HEADERS = {
    "Host": "apis.indeed.com",
    "content-type": "application/json",
    "indeed-api-key": "161092c2017b5bbab13edb12461a62d5a833871e7cad6d9d475304573de67ac8",
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Indeed App 193.1",
    "indeed-app-info": "appv=193.1; appid=com.indeed.jobsearch; osv=16.6.1; os=ios; dtype=phone",
}

# country name -> (domain prefix, API country code)
COUNTRY_DOMAINS = {
    "egypt": ("eg", "EG"),
    "usa": ("www", "US"),
    "united states": ("www", "US"),
    "united kingdom": ("uk", "GB"),
    "uk": ("uk", "GB"),
    "united arab emirates": ("ae", "AE"),
    "uae": ("ae", "AE"),
    "saudi arabia": ("sa", "SA"),
    "germany": ("de", "DE"),
    "netherlands": ("nl", "NL"),
    "canada": ("ca", "CA"),
    "india": ("in", "IN"),
    "singapore": ("sg", "SG"),
}

JOB_QUERY = """
    query GetJobData {
        jobSearch(
        %s
        limit: 100
        %s
        sort: RELEVANCE
        %s
        ) {
        pageInfo {
            nextCursor
        }
        results {
            job {
            key
            title
            datePublished
            description {
                html
            }
            location {
                countryCode
                admin1Code
                city
            }
            compensation {
                baseSalary {
                unitOfWork
                range {
                    min
                    max
                }
                }
                estimated {
                currencyCode
                unitOfWork
                range {
                    min
                    max
                }
                }
            }
            recruit {
                viewJobUrl
            }
            attributes {
                key
                label
            }
            employer {
                relativeCompanyPageUrl
                name
            }
            }
        }
        }
    }
    """

MAX_RETRIES = 3


class IndeedAdapter(DiscoveryAdapter):
    """Discovers jobs from Indeed's public GraphQL API (multi-country)."""

    adapter_id = "indeed"

    def matches_url(self, url: str) -> bool:
        return "indeed.com" in url.lower()

    def _domain_for(self, country: Optional[str]) -> tuple[str, str]:
        if country:
            hit = COUNTRY_DOMAINS.get(country.strip().lower())
            if hit:
                return hit
        return ("www", "US")

    def _build_query(
        self, search_term: str, location: Optional[str], radius: int,
        cursor: Optional[str], hours_old: Optional[int], remote_only: bool,
    ) -> str:
        what = f'what: "{search_term}"' if search_term else ""
        where = (
            f'location: {{where: "{location}", radius: {radius}, radiusUnit: MILES}}'
            if location else ""
        )
        cursor_s = f'cursor: "{cursor}"' if cursor else ""
        filters = []
        if hours_old:
            filters.append(f'{{ date: {{ field: "dateOnIndeed", start: "{hours_old}h" }} }}')
        if remote_only:
            filters.append('{ keyword: { field: "attributes", keys: ["DSQF7"] } }')
        filters_s = f"filters: [{', '.join(filters)}]" if filters else ""
        return JOB_QUERY % (what, where, cursor_s + "\n        " + filters_s if (cursor_s or filters_s) else "")

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Cairo, Egypt"]
        country = entry.get("country")
        max_pages = entry.get("max_pages_per_query", 2)
        target_count = entry.get("target_jobs_count", 40)
        hours_old = entry.get("hours_old")
        remote_only = entry.get("remote_only", False)
        radius = entry.get("distance", 50)

        domain, country_code = self._domain_for(country)
        base_url = f"https://{domain}.indeed.com"

        postings: List[JobPosting] = []
        seen_keys = set()
        for kw in queries:
            if len(postings) >= target_count:
                break
            for loc in locations:
                if len(postings) >= target_count:
                    break
                cursor: Optional[str] = None
                for _ in range(max_pages):
                    batch, cursor = await self._fetch_page(
                        client, base_url, country_code, kw, loc, radius,
                        cursor, hours_old, remote_only,
                    )
                    if batch is None:
                        break
                    new = [p for p in batch if p.external_id not in seen_keys]
                    seen_keys.update(p.external_id for p in new)
                    postings.extend(new)
                    if len(postings) >= target_count or not cursor:
                        break
                    await asyncio.sleep(random.uniform(1.0, 3.0))

        logger.info("Indeed discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    async def _fetch_page(
        self, client: httpx.AsyncClient, base_url: str, country_code: str,
        keyword: str, location: Optional[str], radius: int, cursor: Optional[str],
        hours_old: Optional[int], remote_only: bool,
    ) -> tuple[Optional[List[JobPosting]], Optional[str]]:
        query = self._build_query(keyword, location, radius, cursor, hours_old, remote_only)
        headers = dict(API_HEADERS)
        headers["indeed-co"] = country_code
        for attempt in range(MAX_RETRIES):
            try:
                resp = await client.post(API_URL, headers=headers, json={"query": query}, timeout=15.0)
            except Exception as exc:
                logger.debug("Indeed request failed (attempt %d): %s", attempt + 1, exc)
                await asyncio.sleep(2.0 * (2 ** attempt))
                continue
            if resp.status_code == 429:
                await asyncio.sleep(5.0 * (2 ** attempt))
                continue
            if resp.status_code != 200:
                logger.debug("Indeed status %d", resp.status_code)
                return None, None
            try:
                search = resp.json()["data"]["jobSearch"]
            except (ValueError, KeyError, TypeError):
                return None, None
            postings: List[JobPosting] = []
            for item in search.get("results") or []:
                try:
                    posting = self._parse_job(item.get("job") or {}, base_url)
                except Exception as exc:
                    logger.debug("Skipping malformed Indeed job: %s", exc)
                    continue
                if posting is not None:
                    postings.append(posting)
            next_cursor = (search.get("pageInfo") or {}).get("nextCursor")
            return postings, next_cursor
        return None, None

    def _parse_job(self, job: Dict[str, Any], base_url: str) -> Optional[JobPosting]:
        key = job.get("key")
        title = job.get("title") or ""
        if not key or not title:
            return None
        raw_url = f"{base_url}/viewjob?jk={key}"
        employer = job.get("employer") or {}
        company = employer.get("name") or "Indeed Employer"
        company_url = base_url + employer["relativeCompanyPageUrl"] if employer.get("relativeCompanyPageUrl") else None
        loc = job.get("location") or {}
        parts = [loc.get("city"), loc.get("admin1Code"), loc.get("countryCode")]
        location = ", ".join(p for p in parts if p) or "Unknown"

        desc_html = (job.get("description") or {}).get("html") or ""
        description = re_text(desc_html) or f"{title} position at {company}. Discovered via Indeed."

        date_posted = None
        posted_at = None
        if job.get("datePublished"):
            try:
                posted_at = datetime.fromtimestamp(job["datePublished"] / 1000, tz=timezone.utc).date().isoformat()
                date_posted = posted_at
            except (TypeError, ValueError, OSError):
                pass

        attrs = " ".join(a.get("label", "") for a in (job.get("attributes") or []))
        is_remote = "remote" in attrs.lower() or "remote" in description.lower()[:500]

        recruit = job.get("recruit") or {}
        job_url_direct = recruit.get("viewJobUrl") or ""

        comp = job.get("compensation") or {}
        salary_min = salary_max = salary_currency = None
        salary_source = None
        try:
            est = comp.get("estimated") or {}
            salary_currency = est.get("currencyCode")
            base = comp.get("baseSalary") or {}
            base_range = base.get("range") or {}
            if base_range.get("min") is not None or base_range.get("max") is not None:
                salary_min = base_range.get("min")
                salary_max = base_range.get("max")
                salary_source = "stated"
            else:
                est_range = est.get("range") or {}
                if est_range.get("min") is not None or est_range.get("max") is not None:
                    salary_min = est_range.get("min")
                    salary_max = est_range.get("max")
                    salary_source = "inferred"
        except AttributeError:
            pass

        canon_url = normalize_url(raw_url)
        metadata: Dict[str, Any] = {"source_platform": "indeed", "remote_flag": is_remote}
        if company_url:
            metadata["company_url"] = company_url

        return JobPosting(
            external_id=f"in-{key}",
            source="indeed",
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
            job_url_direct=job_url_direct,
            salary_source=salary_source,
            posted_at=posted_at,
            metadata=metadata,
        )


def re_text(html: str) -> str:
    """Strip tags to plain text (small local helper, no extra deps)."""
    import re as _re

    text = _re.sub(r"<[^>]+>", " ", html or "")
    text = _re.sub(r"\s+", " ", text).strip()
    return text
