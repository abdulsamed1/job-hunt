"""Unit tests for the Rezi MCP-over-HTTP client (mocked transport, no network)."""

import json

import httpx
import pytest

from job_hunt.rezi import (
    ReziAuthExpired,
    ReziError,
    ReziMCPClient,
    ReziNotConfigured,
    get_mcp_settings_snippet,
)

INIT_RESULT = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "rezi"}}
RESUMES = {"resumes": [{"id": "r1", "name": "Master", "jobTitle": "SWE"}]}


def _rpc_result(payload):
    return {"jsonrpc": "2.0", "id": 1, "result": payload}


def _sse(payload) -> str:
    return f"event: message\ndata: {json.dumps(_rpc_result(payload))}\n\n"


def _make_client(handler, token="tok-123"):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://api.rezi.ai")
    return ReziMCPClient(token=token, client=http)


@pytest.mark.asyncio
async def test_list_resumes_json_response():
    seen = {}

    async def handler(request):
        body = json.loads(request.content.decode())
        seen[body.get("method")] = body
        if body.get("method") == "initialize":
            return httpx.Response(200, json=_rpc_result(INIT_RESULT))
        return httpx.Response(200, json=_rpc_result(RESUMES))

    client = _make_client(handler)
    resumes = await client.list_resumes()
    assert resumes == RESUMES["resumes"]
    assert "initialize" in seen  # handshake happens first


@pytest.mark.asyncio
async def test_list_resumes_sse_response():
    async def handler(request):
        body = json.loads(request.content.decode())
        if body.get("method") == "initialize":
            return httpx.Response(200, json=_rpc_result(INIT_RESULT))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=_sse(RESUMES),
        )

    client = _make_client(handler)
    assert await client.list_resumes() == RESUMES["resumes"]


@pytest.mark.asyncio
async def test_write_resume_create_omits_resume_id():
    sent = {}

    async def handler(request):
        body = json.loads(request.content.decode())
        if body.get("method") == "initialize":
            return httpx.Response(200, json=_rpc_result(INIT_RESULT))
        sent.update((body.get("params") or {}).get("arguments", {}))
        return httpx.Response(200, json=_rpc_result({"id": "new-1"}))

    client = _make_client(handler)
    out = await client.write_resume({"name": "Acme - Backend", "data": {}})
    assert out == {"id": "new-1"}
    assert "resume_id" not in sent and "resumeId" not in sent


@pytest.mark.asyncio
async def test_write_resume_update_includes_resume_id():
    sent = {}

    async def handler(request):
        body = json.loads(request.content.decode())
        if body.get("method") == "initialize":
            return httpx.Response(200, json=_rpc_result(INIT_RESULT))
        sent.update((body.get("params") or {}).get("arguments", {}))
        return httpx.Response(200, json=_rpc_result({"id": "r1"}))

    client = _make_client(handler)
    await client.write_resume({"name": "x"}, resume_id="r1")
    assert sent.get("resume_id") == "r1"


@pytest.mark.asyncio
async def test_401_raises_auth_expired():
    async def handler(request):
        return httpx.Response(401, text="unauthorized")

    client = _make_client(handler)
    with pytest.raises(ReziAuthExpired):
        await client.list_resumes()


@pytest.mark.asyncio
async def test_rpc_error_raises_rezi_error():
    async def handler(request):
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "bad args"}}
        )

    client = _make_client(handler)
    with pytest.raises(ReziError):
        await client.list_resumes()


def test_missing_token_raises_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("REZI_MCP_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .rezi_token here
    with pytest.raises(ReziNotConfigured):
        ReziMCPClient(token=None)


def test_token_from_env(monkeypatch):
    monkeypatch.setenv("REZI_MCP_TOKEN", "env-token")
    client = ReziMCPClient(token=None)
    assert client.token == "env-token"


def test_auth_header_never_logged(caplog):
    client = ReziMCPClient(token="super-secret-token")
    headers = client._headers()
    assert headers["Authorization"] == "Bearer super-secret-token"
    assert "super-secret-token" not in caplog.text


def test_mcp_settings_snippet_has_no_secrets():
    snippet = get_mcp_settings_snippet()
    dumped = json.dumps(snippet)
    assert "api.rezi.ai/mcp" in dumped
    assert "token" not in dumped.lower()
    assert "secret" not in dumped.lower()


def test_token_file_json_extracts_access_token(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.delenv("REZI_MCP_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".rezi_token").write_text(
        _json.dumps({"access_token": "file-json-token", "obtained_at": 1})
    )
    assert ReziMCPClient(token=None).token == "file-json-token"


def test_token_file_bare_string(tmp_path, monkeypatch):
    monkeypatch.delenv("REZI_MCP_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".rezi_token").write_text("bare-token-xyz\n")
    assert ReziMCPClient(token=None).token == "bare-token-xyz"
