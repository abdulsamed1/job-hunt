"""Naukri.com (India) discovery adapter.

Native httpx implementation modeled on JobSpy's Naukri scraper endpoint usage
(public jobapi v3/v4 JSON APIs; MIT, speedyapply/JobSpy). The request token is
a local RSA PKCS#1 v1.5 encryption (stdlib only). No browser, no login.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import logging
import math
import random
import secrets
import time
from typing import Any, Dict, List, Optional

import httpx

from job_hunt.dedup import (
    canonical_url_hash,
    compute_role_fingerprint,
    content_hash,
    normalize_url,
)
from job_hunt.discovery.base import DiscoveryAdapter
from job_hunt.discovery.tls_fetch import fetch_json_async
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)

BASE_URL = "https://www.naukri.com"
SEARCH_URL = f"{BASE_URL}/jobapi/v3/search"
DETAIL_URL = f"{BASE_URL}/jobapi/v4/job"
JOBS_PER_PAGE = 20

SEARCH_HEADERS = {"appid": "109", "systemid": "Naukri"}
DETAIL_HEADERS = {"appid": "121", "systemid": "Naukri"}

# RSA public key for the request token (as published in JobSpy's constants).
NKPARAM_PUBLIC_KEY = (
    "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBALrlQ+djR0RjJwBF1xuisHmdFv334MImK6LgzJhmLhN7"
    "B5yuEyaKoasgXQk3+OQglsOaBxEJ0j5PcTL3nbOvt80CAwEAAQ=="
)


def _read_der(data: bytes, i: int) -> tuple[bytes, int]:
    length = data[i + 1]
    i += 2
    if length & 0x80:
        size = length & 0x7F
        length = int.from_bytes(data[i:i + size], "big")
        i += size
    return data[i:i + length], i + length


@functools.cache
def _rsa_params() -> tuple[int, int]:
    key, _ = _read_der(base64.b64decode(NKPARAM_PUBLIC_KEY), 0)
    _, i = _read_der(key, 0)
    bit_string, _ = _read_der(key, i)
    rsa_key, _ = _read_der(bit_string[1:], 0)
    modulus, i = _read_der(rsa_key, 0)
    exponent, _ = _read_der(rsa_key, i)
    return int.from_bytes(modulus, "big"), int.from_bytes(exponent, "big")


def generate_nkparam(page: str) -> str:
    """Naukri request token: RSA (PKCS#1 v1.5) of "v0|<ms>|121_<page>"."""
    modulus, exponent = _rsa_params()
    size = (modulus.bit_length() + 7) // 8
    message = f"v0|{int(time.time() * 1000)}|121_{page}".encode()
    padding = bytes(secrets.randbelow(255) + 1 for _ in range(size - 3 - len(message)))
    block = b"\x00\x02" + padding + b"\x00" + message
    cipher = pow(int.from_bytes(block, "big"), exponent, modulus)
    return base64.b64encode(cipher.to_bytes(size, "big")).decode()


class NaukriAdapter(DiscoveryAdapter):
    """Discovers India/remote tech jobs from Naukri's public JSON API."""

    adapter_id = "naukri"

    def matches_url(self, url: str) -> bool:
        return "naukri.com" in url.lower()

    async def fetch(
        self, entry: Dict[str, Any], client: httpx.AsyncClient
    ) -> List[JobPosting]:
        queries = entry.get("queries") or ["backend"]
        locations = entry.get("locations") or ["Remote"]
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
                page = 1
                while len(postings) < target_count and page <= max_pages:
                    if page > 1:
                        await asyncio.sleep(random.uniform(3.0, 7.0))
                    batch = await self._fetch_page(client, kw, loc, page, hours_old, remote_only)
                    if batch is None:  # transport/API failure: stop this query
                        break
                    new = [p for p in batch if p.external_id not in seen_ids]
                    if not new:
                        break
                    seen_ids.update(p.external_id for p in new)
                    postings.extend(new)
                    if len(batch) < JOBS_PER_PAGE:
                        break
                    page += 1

        logger.info("Naukri discovery completed: %d jobs harvested.", len(postings))
        return postings[:target_count]

    def _search_params(
        self, keyword: str, location: Optional[str], page: int,
        hours_old: Optional[int], remote_only: bool,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "noOfResults": JOBS_PER_PAGE,
            "urlType": "search_by_keyword",
            "searchType": "adv",
            "keyword": keyword or "",
        }
        if location:
            params["location"] = location
        if hours_old:
            params["jobAge"] = math.ceil(hours_old / 24)
        if remote_only:
            params["wfhType"] = 2
        return params

    async def _fetch_page(
        self, client: httpx.AsyncClient, keyword: str, location: Optional[str],
        page: int, hours_old: Optional[int], remote_only: bool,
    ) -> Optional[List[JobPosting]]:
        # Naukri stalls plain httpx from some networks: use TLS impersonation.
        headers = dict(SEARCH_HEADERS)
        headers["nkparam"] = generate_nkparam("srp")
        headers["Accept-Language"] = "en-US,en;q=0.9"
        params = self._search_params(keyword, location, page, hours_old, remote_only)
        params["pageNo"] = page
        try:
            status, data, _ = await fetch_json_async(SEARCH_URL, params=params, headers=headers)
        except Exception as exc:
            logger.debug("Naukri request failed: %s", exc)
            return None
        if status == 406:
            logger.warning("Naukri rejected the request token (406); public key may be stale")
            return None
        if status != 200 or not isinstance(data, dict):
            logger.debug("Naukri status %d", status)
            return None
        jobs = data.get("jobDetails") or []
        postings: List[JobPosting] = []
        for job in jobs:
            try:
                posting = self._parse_job(job, hours_old)
            except Exception as exc:
                logger.debug("Skipping malformed Naukri job: %s", exc)
                continue
            if posting is not None:
                postings.append(posting)
        return postings

    def _parse_job(self, job: Dict[str, Any], hours_old: Optional[int]) -> Optional[JobPosting]:
        created_ms = job.get("createdDate") or 0
        posted = created_ms / 1000 if created_ms else 0
        if hours_old and posted and posted < time.time() - hours_old * 3600:
            return None

        job_id = str(job.get("jobId") or "")
        title = job.get("title") or ""
        raw_url = BASE_URL + (job.get("jdURL") or "")
        if not title or not job.get("jdURL"):
            return None

        labels = {label.get("type"): label.get("label") for label in (job.get("placeholders") or [])}
        work_mode, _, city = (labels.get("location") or "").rpartition(" - ")
        if city == "Remote":
            work_mode = "Remote"
        if city in ("Remote", "India"):
            city = None
        is_remote = work_mode == "Remote"

        company = job.get("companyName") or "Naukri Employer"
        location = city or "India"
        if is_remote:
            location = f"{location} (Remote)" if city else "Remote"

        skills = [s.strip() for s in (job.get("tagsAndSkills") or "").split(",") if s.strip()]
        description = f"{title} position at {company}. Location: {location}."
        if skills:
            description += f" Skills: {', '.join(skills)}."
        experience = job.get("experienceText") or ""
        if experience:
            description += f" Experience: {experience}."
        description += " Discovered via Naukri."

        salary_min, salary_max, salary_currency = None, None, None
        salary = job.get("salaryDetail") or {}
        if not salary.get("hideSalary"):
            try:
                salary_min = float(salary["minimumSalary"])
                salary_max = float(salary["maximumSalary"])
                salary_currency = salary.get("currency") or "INR"
            except (KeyError, TypeError, ValueError):
                pass

        canon_url = normalize_url(raw_url)
        metadata: Dict[str, Any] = {
            "source_platform": "naukri",
            "remote_flag": is_remote,
            "skills": skills,
            "experience_range": experience,
        }
        company_url = f"{BASE_URL}/{job['staticUrl']}" if job.get("staticUrl") else None
        if company_url:
            metadata["company_url"] = company_url

        return JobPosting(
            external_id=f"nk-{job_id}" if job_id else None,
            source="naukri",
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

    async def fetch_full_description(self, job_id: str, client: httpx.AsyncClient) -> Optional[str]:
        """Fetch detailed description for a Naukri job ID (numeric part)."""
        numeric_id = job_id.replace("nk-", "")
        headers = dict(DETAIL_HEADERS)
        headers["nkparam"] = generate_nkparam(numeric_id)
        try:
            status, data, _ = await fetch_json_async(f"{DETAIL_URL}/{numeric_id}", headers=headers)
            if status != 200 or not isinstance(data, dict):
                return None
            details = data.get("jobDetails") or {}
            return details.get("description")
        except Exception as exc:
            logger.debug("Naukri detail fetch failed for %s: %s", job_id, exc)
            return None
