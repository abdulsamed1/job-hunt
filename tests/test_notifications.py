"""Tests for the safe local Telegram event notifier (stdlib only, no live network)."""

from job_hunt.notifications import (
    DEFAULT_TELEGRAM_EVENTS,
    TelegramNotifier,
    format_event,
    parse_telegram_events,
)

STAGE_EVENTS = ("SEEKING", "FILTERED", "ELIGIBLE", "TAILORED")
OUTCOME_EVENTS = ("APPLICATION_ATTEMPT", "SUBMITTED", "DRY_RUN", "FAILED", "STAGE_FAILED")


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


def test_default_allowlist_is_the_outcome_set():
    assert set(DEFAULT_TELEGRAM_EVENTS) == set(OUTCOME_EVENTS)


def test_stage_events_are_suppressed_and_never_call_the_sender():
    calls = []
    notifier = TelegramNotifier("token", "chat", sender=lambda text: calls.append(text) or True)
    for event in STAGE_EVENTS:
        assert notifier.emit(event, "Backend", "Acme", "why", "u") is False
    assert calls == []


def test_outcome_events_still_reach_the_sender():
    calls = []
    notifier = TelegramNotifier("token", "chat", sender=lambda text: calls.append(text) or True)
    for event in OUTCOME_EVENTS:
        assert notifier.emit(event, "Backend", "Acme", "why", "u") is True
    assert len(calls) == len(OUTCOME_EVENTS)


def test_allowlist_argument_narrows_the_set():
    calls = []
    notifier = TelegramNotifier(
        "token", "chat", sender=lambda text: calls.append(text) or True,
        allowed_events={"SUBMITTED"},
    )
    assert notifier.emit("SUBMITTED", "Backend", "Acme", "ok", "u") is True
    assert notifier.emit("FAILED", "Backend", "Acme", "boom", "u2") is False
    assert len(calls) == 1


def test_allowlist_argument_widens_the_set():
    calls = []
    notifier = TelegramNotifier(
        "token", "chat", sender=lambda text: calls.append(text) or True,
        allowed_events={"SEEKING", "SUBMITTED"},
    )
    assert notifier.emit("SEEKING", "Backend", "Acme", "found", "u") is True
    assert notifier.emit("SUBMITTED", "Backend", "Acme", "ok", "u2") is True
    assert notifier.emit("ELIGIBLE", "Backend", "Acme", "ok", "u3") is False
    assert len(calls) == 2


def test_suppressed_event_is_remembered_so_a_re_emit_stays_quiet():
    calls = []
    notifier = TelegramNotifier("token", "chat", sender=lambda text: calls.append(text) or True)
    assert notifier.emit("SEEKING", "Backend", "Acme", "why", "u") is False
    assert notifier.emit("SEEKING", "Backend", "Acme", "why", "u") is False
    assert calls == []


def test_parse_telegram_events_rules():
    # Empty or absent means the default set.
    assert set(parse_telegram_events(None)) == set(DEFAULT_TELEGRAM_EVENTS)
    assert set(parse_telegram_events("")) == set(DEFAULT_TELEGRAM_EVENTS)
    assert set(parse_telegram_events("   ")) == set(DEFAULT_TELEGRAM_EVENTS)
    # Comma separated, whitespace trimmed, case-insensitive.
    assert parse_telegram_events("SEEKING, SUBMITTED") == {"SEEKING", "SUBMITTED"}
    assert parse_telegram_events("seeking") == {"SEEKING"}
    assert parse_telegram_events("  SEEKING  ,  , SUBMITTED  ") == {"SEEKING", "SUBMITTED"}


def test_cli_build_notifier_reads_the_env_override(monkeypatch):
    from job_hunt import cli

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    monkeypatch.delenv("TELEGRAM_EVENTS", raising=False)
    assert cli.build_notifier().allowed_events == set(DEFAULT_TELEGRAM_EVENTS)

    monkeypatch.setenv("TELEGRAM_EVENTS", "SUBMITTED, SEEKING")
    assert cli.build_notifier().allowed_events == {"SUBMITTED", "SEEKING"}

    monkeypatch.setenv("TELEGRAM_EVENTS", "")
    assert cli.build_notifier().allowed_events == set(DEFAULT_TELEGRAM_EVENTS)
