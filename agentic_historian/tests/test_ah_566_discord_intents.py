"""Tests for S1 (#566): the bot needs no privileged Discord intent — and that stays so.

The Historian bot gets its own Discord application, separate from the OpenClaw
agent `dh-bot` — an operator step, written up in deploy/systemd/SEPARATION.md
(S1). What the repository can pin is the code half of item 3: the bot runs on
`Intents.default()` — no message content, no members, no presences — because
it is driven by slash commands and buttons, its role gate reads the member
Discord sends with each interaction, and it never reads a message. A privileged
intent switched on here would silently widen what the new application has to
be granted in the portal, and would do so without anyone touching the portal.

Offline: static checks on bot.py and the write-up.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BOT = REPO_ROOT / "agentic_historian/bot.py"
SEPARATION = REPO_ROOT / "deploy/systemd/SEPARATION.md"


def _bot_src() -> str:
    return BOT.read_text(encoding="utf-8")


# ── the code half of #566 item 3 ─────────────────────────────────────────────

def test_the_bot_runs_on_default_intents():
    assert re.search(r"^intents = Intents\.default\(\)\s*$", _bot_src(), re.M), \
        "bot.py must build its gateway intents from Intents.default()"


def test_no_privileged_intent_is_switched_on():
    src = _bot_src()
    for flag in ("message_content", "members", "presences"):
        assert not re.search(rf"intents\.{flag}\s*=\s*True", src), \
            f"privileged intent {flag!r} switched on — the portal must not need it"
    assert "Intents.all(" not in src, "Intents.all() grants every privileged intent"


def test_nothing_in_the_bot_reads_messages():
    """Without the message-content intent a message handler would see empty
    content; the bot has none, which is why the intent can stay off."""
    assert "on_message" not in _bot_src()


# ── the operator half is written up ──────────────────────────────────────────

def test_the_separation_doc_carries_the_s1_steps():
    text = SEPARATION.read_text(encoding="utf-8")
    assert "#566" in text
    assert "applications.commands" in text, "the invite scope the slash commands need"
    assert "Privileged" in text, "the portal switches that have to stay off"
    assert "1519706253314756758" in text, "dh-bot's id, for the shared-or-not check"
