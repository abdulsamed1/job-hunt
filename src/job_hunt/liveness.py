"""Zero-token job posting liveness detector and ghost-job guard.

Inspired by career-ops liveness engine: checks whether an ATS or job board
posting is still live, active, and accepting applications before consuming
LLM evaluation tokens or browser automation sessions.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple
import httpx

logger = logging.getLogger(__name__)

HARD_EXPIRED_PATTERNS = [
    re.compile(r"job (?:is )?no longer available", re.IGNORECASE),
    re.compile(r"job.*no longer open", re.IGNORECASE),
    re.compile(r"this job has expired", re.IGNORECASE),
    re.compile(r"job posting has expired", re.IGNORECASE),
    re.compile(r"no longer accepting applications", re.IGNORECASE),
    re.compile(r"this (?:position|role|job) (?:is )?no longer", re.IGNORECASE),
    re.compile(r"this job (?:listing )?is closed", re.IGNORECASE),
    re.compile(r"job (?:listing )?not found", re.IGNORECASE),
    re.compile(r"the page you are looking for doesn['’]?t exist", re.IGNORECASE),
    re.compile(r"applications?\s+(?:(?:have|are|is)\s+)?closed", re.IGNORECASE),
    re.compile(r"\b(?:job|position|role|opening|vacancy|req)\b.{0,60}?has been filled\b(?!\s+out)", re.IGNORECASE),
    re.compile(r"closed on \d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.IGNORECASE),
    re.compile(r"offre (?:expiree|n['’]est plus disponible)", re.IGNORECASE),
    re.compile(r"diese stelle (?:ist )?(?:nicht mehr|bereits) besetzt", re.IGNORECASE),
]

BOT_CHALLENGE_PATTERNS = [
    re.compile(r"just a moment\.\.\.", re.IGNORECASE),
    re.compile(r"checking your browser before accessing", re.IGNORECASE),
    re.compile(r"verify you are (?:a )?human", re.IGNORECASE),
    re.compile(r"attention required.*cloudflare", re.IGNORECASE),
    re.compile(r"\bcf-ray\b", re.IGNORECASE),
    re.compile(r"turnstile", re.IGNORECASE),
    re.compile(r"cf-turnstile", re.IGNORECASE),
]

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def normalize_for_liveness(text: str) -> str:
    """Normalize text by standardizing quotes, whitespace, and diacritics."""
    if not text:
        return ""
    text = re.sub(r"[‘’ʼ′´`]", "'", text)
    text = re.sub(r'[“”″]', '"', text)
    return re.sub(r"\s+", " ", text).strip()


class LivenessDetector:
    """Zero-token HTTP validator that identifies closed, filled, or 404 postings."""

    def __init__(self, timeout_sec: float = 8.0):
        self.timeout_sec = timeout_sec

    def check_html_content(self, html: str, final_url: str = "") -> Tuple[bool, str]:
        """Inspect HTML content directly for expiration or closure indicators."""
        if not html or len(html.strip()) < 100:
            return False, "Empty or insufficient page content"

        # Check URL for error redirects
        if any(tok in final_url.lower() for tok in ["error=true", "/404", "/job-not-found", "/expired"]):
            return False, f"Redirected to expired/error URL: {final_url}"

        # Bot challenge guard: if Cloudflare challenge is returned, do not mark as expired
        for pat in BOT_CHALLENGE_PATTERNS:
            if pat.search(html):
                return True, "Bot challenge / Cloudflare interstitial detected (assumed active)"

        normalized = normalize_for_liveness(html)

        for pat in HARD_EXPIRED_PATTERNS:
            match = pat.search(normalized)
            if match:
                matched_phrase = match.group(0).strip()
                return False, f"Posting expired / closed: '{matched_phrase}'"

        return True, "Posting appears live and active"

    async def check_url_async(
        self, url: str, client: Optional[httpx.AsyncClient] = None
    ) -> Tuple[bool, str]:
        """Perform zero-token HTTP request to verify if job posting is still live."""
        if not url:
            return False, "Empty URL"

        should_close = False
        if client is None:
            client = httpx.AsyncClient()
            should_close = True

        try:
            resp = await client.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_sec,
                follow_redirects=True,
            )

            # HTTP level checks
            if resp.status_code in (404, 410):
                return False, f"HTTP {resp.status_code} (Not Found / Gone)"
            elif resp.status_code >= 500:
                # Server error: do not permanently mark as expired; give benefit of doubt
                return True, f"HTTP {resp.status_code} (Temporary server issue, retry allowed)"
            elif resp.status_code != 200:
                return False, f"HTTP status {resp.status_code}"

            final_url = str(resp.url)
            return self.check_html_content(resp.text, final_url=final_url)

        except httpx.TimeoutException:
            # Network timeout: treat as uncertain/alive to avoid false positive drops
            return True, "Request timeout (assumed alive)"
        except Exception as exc:
            logger.debug("Liveness check error for %s: %s", url, exc)
            return True, f"Liveness check error: {exc} (assumed alive)"
        finally:
            if should_close:
                await client.aclose()

    def check_url(self, url: str) -> Tuple[bool, str]:
        """Synchronous wrapper for liveness check."""
        try:
            with httpx.Client(headers=DEFAULT_HEADERS, timeout=self.timeout_sec, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code in (404, 410):
                    return False, f"HTTP {resp.status_code} (Not Found / Gone)"
                if resp.status_code != 200:
                    return False, f"HTTP status {resp.status_code}"
                return self.check_html_content(resp.text, final_url=str(resp.url))
        except Exception as exc:
            return True, f"Liveness check exception: {exc} (assumed alive)"
