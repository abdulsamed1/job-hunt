"""Repost and recycled job posting detector.

Inspired by career-ops detect-reposts: identifies when companies recycle
or re-list the exact same opening under a different URL across dates
to refresh search rankings ("ghost postings").
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from job_hunt.dedup import normalize_company, normalize_role
from job_hunt.models import JobPosting

logger = logging.getLogger(__name__)


def title_identity_key(title: str) -> str:
    """Derive an order-independent set of normalized keywords for role identity.

    Tolerates minor punctuation or word-order changes, while strictly keeping
    seniority, technology, and regional qualifiers distinct.
    """
    normalized = normalize_role(title).lower()
    words = re.findall(r"\b[a-z0-9+#.]+\b", normalized)
    # Filter out trivial stop words that drift between postings
    stop_words = {"and", "or", "the", "a", "an", "at", "for", "in", "to", "with", "of"}
    filtered = sorted({w for w in words if w not in stop_words})
    return " ".join(filtered)


class RepostCluster(BaseModel):
    """Cluster of postings identified as the same underlying role listing."""

    company: str
    identity_key: str
    postings: List[JobPosting] = Field(default_factory=list)
    first_seen: str = ""
    last_seen: str = ""
    distinct_urls: int = 0
    distinct_days: int = 0
    is_repost: bool = False


class RepostDetector:
    """Detects reposted, recurrent, or stale job openings across the database."""

    def __init__(self, min_span_days: int = 1, max_window_days: int = 90):
        self.min_span_days = min_span_days
        self.max_window_days = max_window_days

    def cluster_postings(self, postings: List[JobPosting]) -> List[RepostCluster]:
        """Group a set of postings into repost clusters."""
        buckets: Dict[Tuple[str, str], List[JobPosting]] = {}

        for p in postings:
            company_norm = normalize_company(p.company)
            identity = title_identity_key(p.title)
            key = (company_norm, identity)
            buckets.setdefault(key, []).append(p)

        clusters: List[RepostCluster] = []
        for (comp, ident), items in buckets.items():
            if len(items) < 2:
                continue

            urls = {p.canonical_url for p in items}
            # Dates
            dates = []
            for p in items:
                d_str = p.created_at or p.posted_at or ""
                if d_str:
                    try:
                        # Extract YYYY-MM-DD
                        dates.append(d_str[:10])
                    except Exception:
                        pass

            distinct_days = len(set(dates)) if dates else 1
            is_repost = len(urls) >= 2 and distinct_days >= self.min_span_days

            cluster = RepostCluster(
                company=comp,
                identity_key=ident,
                postings=items,
                first_seen=min(dates) if dates else "",
                last_seen=max(dates) if dates else "",
                distinct_urls=len(urls),
                distinct_days=distinct_days,
                is_repost=is_repost,
            )
            clusters.append(cluster)

        return clusters

    def check_is_repost(
        self, candidate_job: JobPosting, historical_jobs: List[JobPosting]
    ) -> Tuple[bool, Optional[JobPosting], str]:
        """Check if a newly discovered job is a repost of a historical posting."""
        cand_comp = normalize_company(candidate_job.company)
        cand_key = title_identity_key(candidate_job.title)

        for hist in historical_jobs:
            if hist.id == candidate_job.id:
                continue
            if hist.canonical_url == candidate_job.canonical_url:
                continue  # Handled by exact URL deduplication

            hist_comp = normalize_company(hist.company)
            if hist_comp != cand_comp:
                continue

            hist_key = title_identity_key(hist.title)
            if hist_key == cand_key:
                return (
                    True,
                    hist,
                    f"Repost detected: Same role identity at {hist.company} (originally listed in Job #{hist.id} on {hist.created_at[:10] if hist.created_at else 'earlier date'})",
                )

        return False, None, "Original first-seen opening"
