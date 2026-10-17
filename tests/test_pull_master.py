"""Unit tests for scripts/pull_master.py (no network, wrangler is mocked)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "pull_master.py"
    spec = importlib.util.spec_from_file_location("pull_master", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_parse_pointer_output_reads_value():
    mod = _load()
    out = json.dumps([{"results": [{"value": "abc123"}], "success": True}])
    assert mod.parse_pointer_output(out) == "abc123"


def test_parse_pointer_output_empty_and_garbage():
    mod = _load()
    assert mod.parse_pointer_output("") == ""
    assert mod.parse_pointer_output("not json") == ""
    assert mod.parse_pointer_output(json.dumps([{"results": [], "success": True}])) == ""
    assert mod.parse_pointer_output(json.dumps({"results": [{"value": "x"}]})) == ""


def test_file_sha256_missing_and_content(tmp_path):
    mod = _load()
    assert mod.file_sha256(tmp_path / "nope.pdf") == ""
    p = tmp_path / "a.bin"
    p.write_bytes(b"abc")
    assert mod.file_sha256(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_main_check_mode_reports_drift_without_writing(tmp_path, monkeypatch, capsys):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    target.write_bytes(b"old")
    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: "b" * 64)
    monkeypatch.setattr(mod, "download_master", lambda *a, **k: pytest.fail("must not download in --check"))
    rc = mod.main(["--check", "--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 1
    assert target.read_bytes() == b"old"
    assert "DRIFT" in capsys.readouterr().out


def test_main_downloads_on_drift(tmp_path, monkeypatch):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    calls = []

    def fake_download(worker_dir, sha, tgt):
        calls.append(sha)
        Path(tgt).write_bytes(b"new")

    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: "c" * 64)
    monkeypatch.setattr(mod, "download_master", fake_download)
    rc = mod.main(["--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 0
    assert calls == ["c" * 64]
    assert target.read_bytes() == b"new"


def test_main_up_to_date_is_noop(tmp_path, monkeypatch, capsys):
    mod = _load()
    target = tmp_path / "Abdulsamed_Hamdy.pdf"
    import hashlib
    target.write_bytes(b"same")
    sha = hashlib.sha256(b"same").hexdigest()
    monkeypatch.setattr(mod, "fetch_pointer", lambda worker_dir: sha)
    monkeypatch.setattr(mod, "download_master", lambda *a, **k: pytest.fail("up-to-date must not download"))
    rc = mod.main(["--master-path", str(target), "--worker-dir", str(tmp_path)])
    assert rc == 0
    assert "up to date" in capsys.readouterr().out


def test_main_errors_when_pointer_missing(tmp_path, monkeypatch, capsys):
    mod = _load()

    def boom(worker_dir):
        raise RuntimeError("no master_resume_sha in D1 settings (upload a PDF via Telegram first)")

    monkeypatch.setattr(mod, "fetch_pointer", boom)
    rc = mod.main(["--master-path", str(tmp_path / "x.pdf"), "--worker-dir", str(tmp_path)])
    assert rc == 2
    assert "no master_resume_sha" in capsys.readouterr().out


def test_download_master_verifies_sha(tmp_path, monkeypatch):
    mod = _load()

    class R:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, cwd):
        Path(cmd[cmd.index("--file") + 1]).write_bytes(b"wrong")
        return R()

    monkeypatch.setattr(mod, "_run", fake_run)
    with pytest.raises(RuntimeError):
        mod.download_master(tmp_path, "d" * 64, tmp_path / "out.pdf")
