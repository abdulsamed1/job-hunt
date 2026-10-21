"""The scheduled sweep must wire Telegram lifecycle alerts from secrets.

Drift guard for the BMAD review patch: CI runs previously produced no
notifications because the workflow never exported TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID. The values live in GitHub Secrets; the YAML only references
them, so no credential material can leak into the repo.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "scheduled-sweep.yml"


def test_sweep_wires_telegram_secrets():
    doc = yaml.safe_load(WORKFLOW.read_text())
    env = doc["jobs"]["sweep"]["env"]
    assert env["TELEGRAM_BOT_TOKEN"] == "${{ secrets.TELEGRAM_BOT_TOKEN }}"
    assert env["TELEGRAM_CHAT_ID"] == "${{ secrets.TELEGRAM_CHAT_ID }}"


def test_sweep_wires_the_telegram_event_allowlist():
    """The CI sweep must be able to narrow the Telegram event set.

    An unset repository variable resolves to an empty string, which the
    notifier reads as "use the default outcome set" — so wiring it here
    never changes the default behavior, it only makes it overridable.
    """
    doc = yaml.safe_load(WORKFLOW.read_text())
    env = doc["jobs"]["sweep"]["env"]
    assert env["TELEGRAM_EVENTS"] == "${{ vars.TELEGRAM_EVENTS }}"


def test_sweep_never_contains_literal_credentials():
    body = WORKFLOW.read_text()
    assert "8788179007" not in body  # pasted chat token must never appear
    assert "921966934" not in body  # chat id comes from secrets only
