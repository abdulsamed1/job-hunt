"""Tests for the safe local Telegram event notifier (stdlib only, no live network)."""

from job_hunt.notifications import TelegramNotifier, format_event


def test_format_event_is_tagged_and_bounded():
    text = format_event("local", "SEEKING", "Backend", "Acme", "x" * 500, "https://x")
    assert text.startswith("[local] SEEKING: Backend @ Acme")
    assert len(text) <= 3000


def test_missing_credentials_do_not_send(monkeypatch):
    notifier = TelegramNotifier("", "")
    assert notifier.emit("SEEKING", "Backend", "Acme", "", "") is False


def test_network_failure_is_non_fatal(monkeypatch):
    notifier = TelegramNotifier("token", "chat")
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert notifier.emit("FAILED", "Backend", "Acme", "down", "https://x") is False


def test_same_event_is_sent_once(monkeypatch):
    calls = []
    notifier = TelegramNotifier("token", "chat", sender=lambda text: calls.append(text) or True)
    assert notifier.emit("SUBMITTED", "Backend", "Acme", "ok", "u") is True
    assert notifier.emit("SUBMITTED", "Backend", "Acme", "ok", "u") is False
    assert len(calls) == 1
