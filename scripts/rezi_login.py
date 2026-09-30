#!/usr/bin/env python3
"""Interactive Rezi MCP login — run ONCE on a machine with a browser (your laptop).

Flow:
  1. Probe the MCP server for OAuth discovery metadata.
  2. Dynamically register this client (RFC 7591) with a loopback redirect.
  3. Open your browser for the Rezi consent screen; capture the code locally.
  4. Exchange the code for a 30-day access token (no refresh tokens issued).
  5. Save it owner-only (0600) for the pipeline / `wrangler secret put`.

The token is NEVER printed. Copy it from the file into your server env var
(REZI_MCP_TOKEN) or Cloudflare worker secret, then delete local copies you no
longer need. Re-run monthly or on HTTP 401.

Usage:
    python scripts/rezi_login.py [--token-file .rezi_token]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from job_hunt.rezi_auth import (  # noqa: E402
    REZI_MCP_SERVER_URL,
    build_authorize_url,
    discover_oauth_metadata,
    generate_pkce_pair,
    parse_callback_query,
    save_token_file,
)


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _probe_www_authenticate(server_url: str) -> str:
    """Trigger the MCP 401 to learn the protected-resource metadata URL."""
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    ).encode()
    req = urllib.request.Request(
        server_url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=20)
        return ""
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            return exc.headers.get("WWW-Authenticate", "")
        raise


class _CallbackHandler(BaseHTTPRequestHandler):
    query: str = ""

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        type(self).query = parsed.query
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>Signed in. You can close this tab.</h1></body></html>"
        )

    def log_message(self, *args):  # silence request logs (may contain the code)
        pass


def _wait_for_callback(port: int, timeout: int = 180) -> str:
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if not _CallbackHandler.query:
        raise TimeoutError("Timed out waiting for the browser consent redirect")
    return _CallbackHandler.query


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", default=".rezi_token")
    parser.add_argument("--server-url", default=REZI_MCP_SERVER_URL)
    args = parser.parse_args()

    print("Step 1/5: discovering OAuth metadata…")
    www_auth = _probe_www_authenticate(args.server_url)

    def getter(url: str) -> dict:
        return _http_get_json(url)

    meta = discover_oauth_metadata(getter, www_auth, server_url=args.server_url)

    print("Step 2/5: registering loopback client…")
    port = 8765
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    reg_endpoint = meta.get("registration_endpoint") or ""
    if not reg_endpoint:
        print("ERROR: server metadata has no registration endpoint; cannot continue.")
        return 1
    reg_body = json.dumps(
        {
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
            "client_name": "job-hunt pipeline",
        }
    ).encode()
    reg_req = urllib.request.Request(
        reg_endpoint, data=reg_body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(reg_req, timeout=20) as resp:
        client_id = json.loads(resp.read().decode("utf-8"))["client_id"]

    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    auth_url = build_authorize_url(
        meta["authorization_endpoint"], client_id, redirect_uri, challenge,
        resource=args.server_url, state=state,
    )

    print("Step 3/5: opening your browser for Rezi consent…")
    print("If it does not open, visit this URL manually (it contains no secrets).")
    webbrowser.open(auth_url)
    query = _wait_for_callback(port)
    code = parse_callback_query(query)

    print("Step 4/5: exchanging code for token…")
    import urllib.parse as _up

    token_body = _up.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        }
    ).encode()
    token_req = urllib.request.Request(
        meta["token_endpoint"],
        data=token_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(token_req, timeout=20) as resp:
        token_data = json.loads(resp.read().decode("utf-8"))
    access_token = token_data.get("access_token")
    if not access_token:
        print("ERROR: token endpoint returned no access_token.")
        return 1

    print("Step 5/5: saving token owner-only…")
    saved = save_token_file(
        access_token, expires_in=token_data.get("expires_in"), path=args.token_file
    )
    print(f"Saved to {saved['path']} (mode 0600).")
    print("Next: set REZI_MCP_TOKEN on your server or `wrangler secret put REZI_MCP_TOKEN`.")
    print("The token itself was not printed; read it from the file when provisioning.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
