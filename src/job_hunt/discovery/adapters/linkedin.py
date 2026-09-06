"""LinkedIn public guest jobs discovery adapter.

Extracts real-time job postings from LinkedIn's public guest endpoint
without requiring credentials or API keys. Supports filtering by remote status,
recency (last 24 hours), role keywords, and international locations.
"""

from __future__ import annotations

import asyncio
import logging
import urllib.parse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx

from job_hunt.dedup import (
    canonical_url_hash,
    compute_role_fingerprint,
    content_hash,
    normalize_url,
)
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.models import JobPosting

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/",
}

logger = logging.getLogger(__name__)

DEFAULT_QUERIES = [
    "Software Engineer",
    "Backend Engineer",
    "Full Stack Engineer",
    "Python Developer",
    "Go Engineer",
    "Distributed Systems Engineer",
    "Cloud Infrastructure Engineer",
    "Senior Software Engineer",
    "DevOps Engineer",
    "ML Engineer",
    "Data Engineer",
    "Site Reliability Engineer",
    "Platform Engineer",
    "Infrastructure Engineer",
]

DEFAULT_LOCATIONS = [
    "Worldwide",
    "Remote",
    "United States",
    "European Union",
    "United Kingdom",
    "Germany",
    "Canada",
    "Netherlands",
    "Singapore",
    "Australia",
]

USER_AGENTS = [
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.2 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64; rv:123.0) Gecko/20100101 "
        "Firefox/123.0"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0"
    ),
]

MAX_RETRIES = 3
RETRY_DELAY_BASE = 2.0  # exponential backoff base in seconds


class LinkedInAdapter(DiscoveryAdapter):
    """Discovers live software engineering jobs from LinkedIn public guest search."""

    adapter_id = "linkedin"

    def matches_url(self, url: str) -> bool:
        return "linkedin.com" in url.lower()

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        """Fetch jobs from LinkedIn matching search matrix with adaptive pacing."""
        import random

        queries = entry.get("queries") or DEFAULT_QUERIES
        locations = entry.get("locations") or DEFAULT_LOCATIONS
        remote_only = entry.get("remote_only", False)
        past_24h_only = entry.get("past_24h_only", False)
        max_pages = entry.get("max_pages_per_query", 10)
        target_count = entry.get("target_jobs_count", 1000)

        postings: List[JobPosting] = []
        seen_urls = set()
        rate_limit_delay = 1.0  # Start with 1s, increase on 429

        search_tasks = []
        for kw in queries:
            for loc in locations:
                for page in range(max_pages):
                    start = page * 10
                    search_tasks.append((kw, loc, start))

        logger.info(
            "Starting LinkedIn discovery: %d search batches scheduled (target: %d jobs, past_24h=%s, remote_only=%s)...",
            len(search_tasks),
            target_count,
            past_24h_only,
            remote_only,
        )

        for kw, loc, start in search_tasks:
            if len(postings) >= target_count:
                logger.info("Reached target of %d LinkedIn jobs, concluding scan.", target_count)
                break

            params: Dict[str, Any] = {
                "keywords": kw,
                "location": loc,
                "start": start,
                "count": 10,
            }
            if remote_only:
                params["f_WT"] = "2"
            if past_24h_only:
                params["f_TPR"] = "r86400"

            url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{urllib.parse.urlencode(params)}"
            headers = {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.linkedin.com/jobs/",
                "Connection": "keep-alive",
            }

            for attempt in range(MAX_RETRIES):
                try:
                    resp = await client.get(url, headers=headers, timeout=15.0, follow_redirects=True)
                    if resp.status_code == 429:
                        delay = rate_limit_delay * (2 ** attempt)
                        logger.warning("LinkedIn rate limited (429), backing off for %.1fs", delay)
                        await asyncio.sleep(delay)
                        rate_limit_delay = min(rate_limit_delay * 2, 30.0)
                        continue
                    elif resp.status_code != 200:
                        logger.debug("LinkedIn query (%s, %s, %d) returned status %d", kw, loc, start, resp.status_code)
                        break

                    batch = self._parse_job_cards(resp.text, entry)
                    if not batch and len(postings) < target_count:
                        logger.debug("No job cards parsed for (%s, %s, %d) - LinkedIn may have changed HTML", kw, loc, start)

                    for job in batch:
                        if job.canonical_url not in seen_urls:
                            seen_urls.add(job.canonical_url)
                            postings.append(job)

                    rate_limit_delay = max(rate_limit_delay * 0.5, 0.6)
                    break

                except Exception as exc:
                    logger.debug("LinkedIn query (%s, %s, %d) attempt %d failed: %s", kw, loc, start, attempt + 1, exc)
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY_BASE * (2 ** attempt))

            # Adaptive pacing: slow down if we're getting too many results, speed up if not
            if len(postings) > target_count * 0.8:
                await asyncio.sleep(0.3)
            else:
                await asyncio.sleep(rate_limit_delay)

        logger.info("LinkedIn discovery completed: %d unique jobs harvested.", len(postings))
        return postings

    def _parse_job_cards(self, html: str, entry: Dict[str, Any]) -> List[JobPosting]:
        """Parse HTML fragment containing job search cards."""
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", class_="base-search-card")
        postings: List[JobPosting] = []

        for card in cards:
            title_elem = card.find("h3", class_="base-search-card__title")
            company_elem = card.find("h4", class_="base-search-card__subtitle")
            location_elem = card.find("span", class_="job-search-card__location")
            link_elem = card.find("a", class_="base-card__full-link")
            time_elem = card.find("time")

            title = title_elem.get_text(strip=True) if title_elem else ""
            company = company_elem.get_text(strip=True) if company_elem else ""
            location = location_elem.get_text(strip=True) if location_elem else "Remote"
            raw_url = link_elem["href"].split("?")[0] if link_elem and "href" in link_elem.attrs else ""

            if not title or not raw_url:
                continue

            canon_url = normalize_url(raw_url)
            external_id = (
                card.get("data-entity-urn", "")
                .replace("urn:li:jobPosting:", "")
                .strip()
            )
            posted_at = (
                time_elem.get("datetime")
                if time_elem and time_elem.has_attr("datetime")
                else (time_elem.get_text(strip=True) if time_elem else "")
            )

            # Try to find job description snippet in the card
            description_elem = card.find("div", class_="base-search-card__description")
            description_snippet = description_elem.get_text(strip=True) if description_elem else ""

            description = (
                f"{title} position at {company}. Location: {location}. "
                f"Discovered via LinkedIn Jobs. Posted: {posted_at}."
            )
            if description_snippet:
                description += f" Description: {description_snippet}"

            # Detect application type: Easy Apply vs External URL
            application_type = None
            easy_apply_btn = card.find("button", string=lambda t: t and "easy apply" in t.lower().strip())
            if easy_apply_btn:
                application_type = "easy_apply"
            else:
                # Check if the full-link points to an external ATS (not a LinkedIn view page)
                if link_elem and "href" in link_elem.attrs:
                    href = link_elem["href"].split("?")[0]
                    if "linkedin.com/jobs/view" not in href:
                        application_type = "external_url"

            posting = JobPosting(
                external_id=external_id or None,
                source="linkedin",
                source_name=company or "LinkedIn",
                title=title,
                company=company or "LinkedIn Employer",
                raw_url=raw_url,
                canonical_url=canon_url,
                canonical_url_hash=canonical_url_hash(canon_url),
                role_fingerprint=compute_role_fingerprint(company, title, location),
                content_hash=content_hash(description),
                location=location,
                description=description,
                posted_at=posted_at or None,
                application_type=application_type,
                metadata={"source_platform": "linkedin", "remote_flag": not entry.get("remote_only", False)},
            )
            postings.append(posting)

        return postings

    async def fetch_full_description(self, job_id: str, client: httpx.AsyncClient) -> Optional[str]:
        """Fetch detailed job description text for a specific LinkedIn job ID."""
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
        try:
            resp = await client.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Try multiple selectors for LinkedIn's changing HTML structure
                markup = (
                    soup.find("div", class_="show-more-less-html__markup")
                    or soup.find("div", class_="description__text")
                    or soup.find("div", class_="jobs-description-text")
                )
                if markup:
                    return markup.get_text(separator="\n", strip=True)
                # Fallback: look for any large text content blocks
                for tag in soup.find_all("div"):
                    if tag.get("class") and any("description" in c.lower() for c in tag.get("class", [])):
                        text = tag.get_text(separator="\n", strip=True)
                        if len(text) > 200:
                            return text
        except Exception as exc:
            logger.debug("Failed to fetch full description for LinkedIn job %s: %s", job_id, exc)
        return None
