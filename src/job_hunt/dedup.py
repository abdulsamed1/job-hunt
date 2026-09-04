"""Multi-signal deduplication for job postings.

Implements:
1. RFC 3986 canonical posting-URL key and hashing
2. Company name normalization
3. Role / title normalization
4. Location normalization
5. Content cleaning and hashing
6. Multi-signal role fingerprinting
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from typing import Optional

# Tracking query parameters that identify a click or campaign, never the posting itself.
TRACKING_PARAM_PATTERNS = [
    re.compile(r"^utm_", re.IGNORECASE),
    re.compile(r"^gh_src$", re.IGNORECASE),
    re.compile(r"^fbclid$", re.IGNORECASE),
    re.compile(r"^gclid$", re.IGNORECASE),
    re.compile(r"^mc_cid$", re.IGNORECASE),
    re.compile(r"^mc_eid$", re.IGNORECASE),
    re.compile(r"^igshid$", re.IGNORECASE),
    re.compile(r"^_hsenc$", re.IGNORECASE),
    re.compile(r"^_hsmi$", re.IGNORECASE),
    re.compile(r"^trk$", re.IGNORECASE),
    re.compile(r"^trackingid$", re.IGNORECASE),
    re.compile(r"^ref_code$", re.IGNORECASE),
]

# Company legal and generic suffixes
COMPANY_SUFFIXES = [
    r"\binc\.?\b",
    r"\bincorporated\b",
    r"\bllc\.?\b",
    r"\bltd\.?\b",
    r"\blimited\b",
    r"\bcorp\.?\b",
    r"\bcorporation\b",
    r"\bco\.?\b",
    r"\bcompany\b",
    r"\btechnologies\b",
    r"\btechnology\b",
    r"\bholdings\b",
    r"\bgmbh\b",
    r"\bag\b",
    r"\bsa\b",
    r"\bsas\b",
    r"\bplc\b",
    r"\bbv\b",
]

# Role abbreviations and expansions
ROLE_TOKEN_EXPANSIONS = {
    "sr": "senior",
    "sr.": "senior",
    "jr": "junior",
    "jr.": "junior",
    "swe": "software engineer",
    "sw": "software",
    "eng": "engineer",
    "engr": "engineer",
    "dev": "developer",
    "mgr": "manager",
    "lead": "lead",
    "fde": "forward deployed engineer",
    "sre": "site reliability engineer",
    "infra": "infrastructure",
    "qa": "quality assurance",
    "ml": "machine learning",
    "ai": "artificial intelligence",
    "genai": "generative ai",
}


def promote_known_fragment_identity(parsed_url: urllib.parse.ParseResult) -> urllib.parse.ParseResult:
    """Promote identity-bearing SPA hash fragments into query parameters before stripping fragments.

    Example: app.mokahr.com/#/job/{id} -> app.mokahr.com/?mokahr_job_id={id}
    """
    if parsed_url.hostname and parsed_url.hostname.lower() == "app.mokahr.com":
        match = re.match(r"^#/job/([^/?#]+)", parsed_url.fragment)
        if match:
            job_id = urllib.parse.unquote(match.group(1))
            query_dict = urllib.parse.parse_qsl(parsed_url.query, keep_blank_values=True)
            query_dict.append(("mokahr_job_id", job_id))
            new_query = urllib.parse.urlencode(query_dict)
            return parsed_url._replace(query=new_query, fragment="")
    return parsed_url


def normalize_url(raw_url: Optional[str]) -> str:
    """Normalize a posting URL to a stable canonical comparison key per RFC 3986.

    Returns '' when raw_url is not a valid http(s) URL (never a string stand-in).
    """
    if not raw_url or not isinstance(raw_url, str):
        return ""

    s = raw_url.strip()
    if not s:
        return ""

    try:
        parsed = urllib.parse.urlparse(s)
    except Exception:
        return ""

    if parsed.scheme.lower() not in ("http", "https"):
        return ""

    if not parsed.hostname:
        return ""

    # Promote SPA fragment if applicable
    parsed = promote_known_fragment_identity(parsed)

    # Scheme canonicalized to https
    scheme = "https"
    netloc = parsed.hostname.lower()
    if parsed.port and parsed.port not in (80, 443):
        netloc = f"{netloc}:{parsed.port}"

    # Path normalization: drop trailing slash (unless root path '/')
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    # Query normalization: drop tracking params, sort remaining params
    query_items = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    kept_params = []
    for k, v in query_items:
        if not any(pattern.search(k) for pattern in TRACKING_PARAM_PATTERNS):
            kept_params.append((k, v))

    kept_params.sort(key=lambda item: (item[0], item[1]))
    clean_query = urllib.parse.urlencode(kept_params)

    # Fragments never identify the posting (RFC 3986 §6)
    return urllib.parse.urlunparse((scheme, netloc, path, "", clean_query, ""))


def canonical_url_hash(url: Optional[str]) -> str:
    """Return SHA-256 hex digest of the canonicalized URL, or '' if empty."""
    norm = normalize_url(url)
    if not norm:
        return ""
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def normalize_company(company: Optional[str]) -> str:
    """Normalize company name by stripping legal suffixes, punctuation, and extra whitespace."""
    if not company or not isinstance(company, str):
        return ""

    text = company.strip().lower()
    for suffix_pattern in COMPANY_SUFFIXES:
        text = re.sub(suffix_pattern, "", text, flags=re.IGNORECASE)

    # Strip non-alphanumeric except space
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_role(title: Optional[str]) -> str:
    """Normalize job title/role by expanding common abbreviations and removing noise words."""
    if not title or not isinstance(title, str):
        return ""

    text = title.strip().lower()

    # Preserve special terms like c++, c#
    text = text.replace("c++", "__cpp__").replace("c#", "__csharp__")

    # Replace punctuation with spaces
    text = re.sub(r"[^\w\s]", " ", text)

    text = text.replace("__cpp__", "c++").replace("__csharp__", "c#")

    tokens = text.split()
    expanded_tokens = []
    for token in tokens:
        expanded = ROLE_TOKEN_EXPANSIONS.get(token, token)
        expanded_tokens.append(expanded)

    joined = " ".join(expanded_tokens)
    return re.sub(r"\s+", " ", joined).strip()


def normalize_location(location: Optional[str]) -> str:
    """Normalize location string, standardizing remote representations."""
    if not location or not isinstance(location, str):
        return "unknown"

    text = location.strip().lower()
    if not text:
        return "unknown"

    # Replace punctuation with space
    text = re.sub(r"[^\w\s]", " ", text)
    tokens = text.split()
    text = " ".join(tokens)

    # Common remote variations
    if "remote" in text:
        if any(term in text for term in ("us", "usa", "united states")):
            return "remote us"
        if any(term in text for term in ("eu", "europe")):
            return "remote eu"
        if any(term in text for term in ("uk", "united kingdom")):
            return "remote uk"
        if any(term in text for term in ("worldwide", "global", "anywhere")):
            return "remote global"
        return "remote"

    return text


def clean_content_text(content: Optional[str]) -> str:
    """Strip HTML tags and normalize whitespace for content comparison."""
    if not content or not isinstance(content, str):
        return ""

    # Strip HTML tags
    text = re.sub(r"<[^>]+>", "", content)
    # Decode basic HTML entities
    text = urllib.parse.unquote(text)
    # Lowercase and collapse whitespace
    text = re.sub(r"\s+", " ", text.lower()).strip()
    return text


def content_hash(content: Optional[str]) -> str:
    """Return SHA-256 hex digest of cleaned content text."""
    cleaned = clean_content_text(content)
    if not cleaned:
        return ""
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def compute_role_fingerprint(company: Optional[str], title: Optional[str], location: Optional[str]) -> str:
    """Compute deterministic role fingerprint combining normalized company, title, and location.

    Formula: sha256(normalized_company + "::" + normalized_title + "::" + normalized_location)
    """
    c = normalize_company(company)
    t = normalize_role(title)
    l = normalize_location(location)

    key = f"{c}::{t}::{l}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
