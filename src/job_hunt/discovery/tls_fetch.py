"""WAF-friendly HTTP fetch via curl_cffi TLS impersonation (sync, thread-bridged).

Some boards (Bayt Cloudflare, Naukri API stalls) block plain httpx. This helper
runs curl_cffi's Chrome-impersonated requests in a worker thread so async
adapters stay non-blocking. curl_cffi is a light binary-wheel dependency.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional, Tuple

import logging

logger = logging.getLogger(__name__)


def fetch_sync(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    impersonate: str = "chrome",
) -> Tuple[int, str, str]:
    """Blocking GET; returns (status_code, text, final_url). Raises on error."""
    from curl_cffi import requests as cr

    resp = cr.get(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        impersonate=impersonate,
        allow_redirects=True,
    )
    return resp.status_code, resp.text, str(resp.url)


async def fetch_text(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
    impersonate: str = "chrome",
) -> Tuple[int, str, str]:
    """Async wrapper around fetch_sync via a worker thread."""
    return await asyncio.to_thread(fetch_sync, url, params, headers, timeout, impersonate)


def fetch_json(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Tuple[int, Any, str]:
    """Blocking GET decoded as JSON; returns (status, parsed, final_url)."""
    import json as _json

    status, text, final_url = fetch_sync(url, params, headers, timeout)
    if status != 200:
        return status, None, final_url
    try:
        return status, _json.loads(text), final_url
    except ValueError as exc:
        logger.debug("Non-JSON response from %s: %s", url, exc)
        return status, None, final_url


async def fetch_json_async(
    url: str,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 20,
) -> Tuple[int, Any, str]:
    """Async wrapper around fetch_json via a worker thread."""
    return await asyncio.to_thread(fetch_json, url, params, headers, timeout)
