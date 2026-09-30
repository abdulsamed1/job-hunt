"""OAuth provisioning helpers for the Rezi MCP server (offline-testable parts).

Interactive browser login cannot run on headless servers, so this module splits
the flow: pure functions (PKCE, URL building, metadata discovery, token saving)
are unit-tested here, and scripts/rezi_login.py orchestrates them on a machine
with a browser. Follows MCP authorization discovery (RFC 8414 / RFC 8707).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlencode

logger = logging.getLogger(__name__)

REZI_MCP_SERVER_URL = "https://api.rezi.ai/mcp"


def generate_pkce_pair() -> tuple[str, str]:
    """Return (verifier, S256 challenge) for the OAuth authorization request."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def parse_www_authenticate(header: str) -> Optional[str]:
    """Extract the resource_metadata URL from a 401 WWW-Authenticate header."""
    if not header:
        return None
    match = re.search(r'resource_metadata="([^"]+)"', header)
    return match.group(1) if match else None


def discover_oauth_metadata(
    http_get: Callable[[str], Dict[str, Any]],
    www_authenticate: str,
    server_url: str = REZI_MCP_SERVER_URL,
) -> Dict[str, str]:
    """Discover OAuth endpoints via protected-resource metadata (RFC 8414 style).

    http_get is injectable so discovery is testable without network.
    Returns {authorization_endpoint, token_endpoint, registration_endpoint}.
    """
    metadata_url = parse_www_authenticate(www_authenticate or "")
    if not metadata_url:
        # Fallback: well-known path on the resource server itself
        metadata_url = server_url.rstrip("/") + "/.well-known/oauth-protected-resource"
    protected = http_get(metadata_url)
    auth_servers = protected.get("authorization_servers") or []
    if not auth_servers:
        raise ValueError("No authorization servers advertised for the Rezi MCP server")
    auth_server = auth_servers[0].rstrip("/")
    server_meta = http_get(auth_server + "/.well-known/oauth-authorization-server")
    for key in ("authorization_endpoint", "token_endpoint"):
        if key not in server_meta:
            raise ValueError(f"Authorization server metadata missing {key}")
    return {
        "authorization_endpoint": server_meta["authorization_endpoint"],
        "token_endpoint": server_meta["token_endpoint"],
        "registration_endpoint": server_meta.get("registration_endpoint", ""),
    }


def build_authorize_url(
    auth_endpoint: str,
    client_id: str,
    redirect_uri: str,
    challenge: str,
    resource: str = REZI_MCP_SERVER_URL,
    state: Optional[str] = None,
) -> str:
    """Build the browser consent URL for the authorization-code + PKCE flow."""
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    if state:
        params["state"] = state
    return auth_endpoint + ("&" if "?" in auth_endpoint else "?") + urlencode(params)


def parse_callback_query(query: str) -> str:
    """Extract the authorization code from the loopback redirect query string."""
    parsed = parse_qs(query or "")
    if "error" in parsed:
        raise ValueError(f"OAuth consent failed: {parsed['error'][0]}")
    codes = parsed.get("code")
    if not codes:
        raise ValueError("OAuth callback carried no authorization code")
    return codes[0]


def save_token_file(
    access_token: str,
    expires_in: Optional[int] = None,
    path: str | Path = Path(".rezi_token"),
) -> Dict[str, Any]:
    """Persist the token JSON with owner-only permissions (0600).

    Returns the stored record WITHOUT echoing guidance containing the token.
    """
    record = {
        "access_token": access_token,
        "obtained_at": int(time.time()),
        "expires_in": expires_in,
        "server_url": REZI_MCP_SERVER_URL,
    }
    target = Path(path)
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    os.chmod(str(target), 0o600)
    return {
        "access_token": access_token,
        "expires_in": expires_in,
        "path": str(target),
        "note": "stored with owner-only permissions; re-authentication needed on expiry",
    }
