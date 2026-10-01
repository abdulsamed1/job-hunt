"""Shared recency + remote post-filters for all discovery adapters.

Rule (both filters): drop only what is PROVEN stale/on-site; anything without
a usable date or location signal passes through. Boards rarely carry dates, so
fail-closed filtering would silently nuke most sources — this keeps volume
while guaranteeing every dated posting is fresh.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)

REMOTE_SIGNALS = (
    "remote", "worldwide", "anywhere", "wfh", "work from home",
    "work from anywhere", "distributed", "telecommute", "virtual",
)


def parse_posted_at(value: object) -> Optional[datetime]:
    """Parse the zoo of board date formats into an aware datetime, or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None

    # Epoch seconds or milliseconds (int or numeric string).
    if re.fullmatch(r"\d{10}(\.\d+)?", text):
        return datetime.fromtimestamp(float(text), tz=timezone.utc)
    if re.fullmatch(r"\d{13}", text):
        return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)

    # Relative: "5 hours ago", "3 days ago".
    match = re.fullmatch(r"(\d+)\s+(hour|day|week|month)s?\s+ago", text.lower())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        days = {"hour": 0, "day": 1, "week": 7, "month": 30}[unit]
        hours = amount if unit == "hour" else 0
        return datetime.now(timezone.utc) - timedelta(days=days * amount if unit != "hour" else 0,
                                                      hours=hours)

    # RFC822 (feeds) and ISO 8601 (most ATS APIs).
    for parser in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            cleaned = text.replace("Z", "+00:00") if parser is datetime.fromisoformat else text
            parsed = parser(cleaned)
            if isinstance(parsed, datetime):
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            if hasattr(parsed, "year"):  # date -> start of day UTC
                from datetime import date as _date
                if isinstance(parsed, _date):
                    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def is_fresh(posting: JobPosting, hours_old: int) -> Optional[bool]:
    """True/False when the posting carries a usable date, else None (unknown)."""
    if not hours_old or hours_old <= 0:
        return True
    parsed = parse_posted_at(posting.posted_at)
    if parsed is None:
        return None
    return parsed >= datetime.now(timezone.utc) - timedelta(hours=hours_old)


def filter_recent(postings: List[JobPosting], hours_old: int) -> List[JobPosting]:
    """Drop only provably-stale postings; dateless ones pass through."""
    if not hours_old or hours_old <= 0:
        return postings
    kept = [p for p in postings if is_fresh(p, hours_old) is not False]
    dropped = len(postings) - len(kept)
    if dropped:
        logger.info("Recency filter: dropped %d stale postings (>%dh)", dropped, hours_old)
    return kept


def is_remoteish(posting: JobPosting) -> bool:
    """True when remote signals exist; False for unknown too (see filter)."""
    metadata = posting.metadata or {}
    for key in ("remote_flag", "isRemote", "is_remote"):
        if metadata.get(key) is True:
            return True
    haystack = f"{posting.location or ''} {posting.description or ''}".lower()
    return any(sig in haystack for sig in REMOTE_SIGNALS)


def _location_names_city(posting: JobPosting) -> bool:
    """Heuristic: location names a concrete place with no remote signal."""
    loc = (posting.location or "").strip().lower()
    if not loc:
        return False
    if any(sig in loc for sig in REMOTE_SIGNALS):
        return False
    # "City, Country" or a single non-generic token counts as placed.
    return bool(re.search(r"[a-z]{3,}", loc))


def filter_remote(postings: List[JobPosting]) -> List[JobPosting]:
    """Keep remote-signalled + unknown-location postings; drop placed on-site ones."""
    kept = [p for p in postings if is_remoteish(p) or not _location_names_city(p)]
    dropped = len(postings) - len(kept)
    if dropped:
        logger.info("Remote filter: dropped %d on-site postings", dropped)
    return kept
