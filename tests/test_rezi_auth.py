"""Offline tests for Rezi OAuth provisioning helpers (no network, no browser)."""

import base64
import hashlib
import json
import os
import stat

import pytest

from job_hunt.rezi_auth import (
    build_authorize_url,
    discover_oauth_metadata,
    generate_pkce_pair,
    parse_callback_query,
    parse_www_authenticate,
    save_token_file,
)


def test_pkce_pair_verifies():
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 43
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_parse_www_authenticate_extracts_metadata_url():
    header = 'Bearer error="unauthorized", resource_metadata="https://api.rezi.ai/.well-known/oauth-protected-resource"'
    assert parse_www_authenticate(header) == "https://api.rezi.ai/.well-known/oauth-protected-resource"
    assert parse_www_authenticate("Bearer") is None
    assert parse_www_authenticate("") is None


def test_build_authorize_url_has_pkce_params():
    url = build_authorize_url(
        auth_endpoint="https://auth.rezi.ai/authorize",
        client_id="cid-1",
        redirect_uri="http://127.0.0.1:8765/callback",
        challenge="ch-abc",
        resource="https://api.rezi.ai/mcp",
        state="st-xyz",
    )
    assert "response_type=code" in url
    assert "code_challenge=ch-abc" in url
    assert "code_challenge_method=S256" in url
    assert "client_id=cid-1" in url
    assert "state=st-xyz" in url


def test_parse_callback_query_ok_and_error():
    assert parse_callback_query("code=abc123&state=st-xyz") == "abc123"
    with pytest.raises(ValueError):
        parse_callback_query("error=access_denied&state=st-xyz")
    with pytest.raises(ValueError):
        parse_callback_query("state=st-xyz")


def test_save_token_file_roundtrip_and_perms(tmp_path):
    path = tmp_path / "tok.json"
    saved = save_token_file("tok-secret", expires_in=3600 * 24 * 30, path=path)
    assert saved["access_token"] == "tok-secret"
    assert saved["expires_in"] == 3600 * 24 * 30
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600
    assert json.loads(path.read_text())["access_token"] == "tok-secret"
    assert "tok-secret" not in str(saved.get("note", ""))


def test_discover_oauth_metadata_with_fake_getter():
    calls = []

    def fake_get(url):
        calls.append(url)
        if "oauth-protected-resource" in url:
            return {"authorization_servers": ["https://auth.rezi.ai"]}
        if "auth.rezi.ai" in url and "oauth-authorization-server" in url:
            return {
                "authorization_endpoint": "https://auth.rezi.ai/authorize",
                "token_endpoint": "https://auth.rezi.ai/token",
                "registration_endpoint": "https://auth.rezi.ai/register",
            }
        raise AssertionError(f"unexpected GET {url}")

    meta = discover_oauth_metadata(
        fake_get,
        www_authenticate='Bearer resource_metadata="https://api.rezi.ai/.well-known/oauth-protected-resource"',
    )
    assert meta["authorization_endpoint"] == "https://auth.rezi.ai/authorize"
    assert meta["token_endpoint"] == "https://auth.rezi.ai/token"
    assert meta["registration_endpoint"] == "https://auth.rezi.ai/register"
