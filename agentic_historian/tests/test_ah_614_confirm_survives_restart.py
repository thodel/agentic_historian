"""The Confirm button of `/update` survives the restart `/update` causes — #614.

Measured on 2026-10-10, 21:15 and 21:16: two clicks on `/update`'s Confirm, both
answered by Discord with *"This component is no longer valid."* That string is
nowhere in py-cord (`grep -rn "no longer valid"` in the installed `discord/` is
empty), so it is Discord saying: the click arrived, and the application's process
has no handler for this `custom_id`.

Why it had none: `_ConfirmView` was built with a 300 s timeout, which makes it a
**non-persistent** view. py-cord keeps such a view only in the sending process's
memory, and `client.add_view` refuses to re-register it — the method requires a
persistent view and says so. The pending record was an in-memory dict besides.

And `/update` **restarts the bot on purpose** (#245: marker, `sys.exit(0)`,
systemd brings it back on the new code). So a dead button is not an edge case in
this routine; it is the routine.

#150 solved this for the gate cards and wrote down why: *"The bot restarts
(deploys, crashes); a routing card whose buttons die on restart teaches users to
ignore the cards."* This file holds that fix for the command that causes the
restart.

Offline. Discord is mocked; no process is restarted — a restart is simulated by
reading the record back the way a fresh process would.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config              # noqa: E402
import pending_confirm as pc   # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def _entry(token="tok0", kind="update", requester="111", created_at=None, **data):
    e = pc.Pending(token=token, kind=kind, channel_id="999", message_id="12345",
                   requester=requester, data=data or {"target_sha": "abc123"})
    if created_at is not None:
        e.created_at = created_at
    return e


# ── the record outlives the process ─────────────────────────────────────────

def test_a_confirmation_is_read_back_after_a_restart():
    """The whole point: a new process finds what the old one was asked."""
    pc.add(_entry(token="t1"))
    status, found = pc.lookup("t1")
    assert status == pc.LIVE
    assert found.kind == "update" and found.message_id == "12345"
    assert found.data["target_sha"] == "abc123"


def test_an_unknown_token_is_missing_not_expired():
    """Different answers: "expired" means wait and retry, "unknown" means ask
    again. Collapsing them tells somebody to wait for something that is gone."""
    status, found = pc.lookup("nope")
    assert status == pc.MISSING and found is None


def test_an_old_confirmation_is_expired_and_still_returned():
    """Returned with its status, so the refusal can say how old it was —
    "expired" without a number reads as "broken"."""
    pc.add(_entry(token="t2", created_at=time.time() - pc.TTL_S - 60))
    status, found = pc.lookup("t2")
    assert status == pc.EXPIRED
    assert found is not None and found.age_s() > pc.TTL_S


def test_the_deadline_is_the_records_age_not_a_view_timeout():
    """A deadline enforced by a timer in one process is not a deadline — it is a
    property of that process's lifetime, and this process exits on purpose."""
    fresh = _entry(created_at=1_000.0)
    assert not fresh.expired(now=1_000.0 + pc.TTL_S - 1)
    assert fresh.expired(now=1_000.0 + pc.TTL_S + 1)


def test_adding_prunes_what_has_already_expired():
    pc.add(_entry(token="old", created_at=time.time() - pc.TTL_S - 1))
    pc.add(_entry(token="new"))
    tokens = {e.token for e in pc.load()}
    assert tokens == {"new"}


def test_adding_the_same_token_twice_keeps_one():
    pc.add(_entry(token="t3", target_sha="first"))
    pc.add(_entry(token="t3", target_sha="second"))
    [only] = pc.load()
    assert only.data["target_sha"] == "second"


def test_dropping_forgets_one_and_leaves_the_others():
    pc.add(_entry(token="a"))
    pc.add(_entry(token="b"))
    pc.drop("a")
    assert {e.token for e in pc.load()} == {"b"}


def test_two_flows_share_one_file():
    pc.add(_entry(token="u", kind="update"))
    pc.add(_entry(token="r", kind="atr_restart", engine="kraken"))
    kinds = {e.token: e.kind for e in pc.load()}
    assert kinds == {"u": "update", "r": "atr_restart"}


# ── it must never be the reason the bot fails to start ─────────────────────

def test_an_unreadable_file_yields_nothing_rather_than_raising():
    """Read on `on_ready`. A bot that refuses to start because a confirmation
    file is corrupt has turned a cosmetic problem into an outage."""
    pc.path().parent.mkdir(parents=True, exist_ok=True)
    pc.path().write_text("{not json at all", encoding="utf-8")
    assert pc.load() == []
    assert pc.lookup("anything")[0] == pc.MISSING


def test_a_record_from_a_newer_build_is_skipped_not_fatal():
    pc.path().parent.mkdir(parents=True, exist_ok=True)
    pc.path().write_text('[{"token": "x", "kind": "update", "channel_id": "1", '
                         '"message_id": "2", "requester": "3", '
                         '"from_the_future": true}]', encoding="utf-8")
    assert pc.load() == []


def test_a_payload_that_is_not_a_list_is_ignored():
    pc.path().parent.mkdir(parents=True, exist_ok=True)
    pc.path().write_text('{"token": "x"}', encoding="utf-8")
    assert pc.load() == []


def test_no_file_is_not_an_error():
    assert pc.load() == []


def test_the_record_carries_no_secret():
    """The token is an HMAC over a timestamp and identifies a pending action;
    the channel, message and requester are already visible on the message the
    button sits on. Nothing here may be a credential."""
    pc.add(_entry(token="t", target_sha="abc123", from_sha="000111"))
    raw = pc.path().read_text(encoding="utf-8")
    for forbidden in ("DISCORD_BOT_TOKEN", "GITHUB_TOKEN", "SWITCHDRIVE_PASS",
                      "ATR_API_KEY"):
        assert forbidden not in raw
    assert getattr(config, "DISCORD_BOT_TOKEN", "x") not in raw or not config.DISCORD_BOT_TOKEN


# ── the views are persistent, which is what makes rebinding possible ──────

def _bot():
    import bot
    return bot


def test_every_confirm_view_is_persistent():
    """``client.add_view`` raises ValueError for a view with a timeout, so a
    timed view **cannot** be rebound after a restart — this is the property the
    whole fix rests on, and the three views that need it are the three that ask
    a human to confirm something.

    (Constructed inside a loop: ``View.__init__`` calls ``get_running_loop``.)
    """
    import asyncio

    async def go():
        bot = _bot()
        return [
            bot._ConfirmView(token="t", requester="1", target_sha="x"),
            bot._RestartView(token="t", requester="1", engine="kraken"),
            bot._McpProposeView(token="t", requester="1"),
        ]

    for view in asyncio.run(go()):
        assert view.timeout is None, type(view).__name__
        for item in view.children:
            assert getattr(item, "custom_id", None), \
                f"{type(view).__name__}: a persistent view needs explicit custom_ids"


def test_the_update_view_rebuilds_from_a_stored_record():
    import asyncio

    async def go():
        bot = _bot()
        return bot, bot._view_for_confirm(_entry(kind="update", target_sha="deadbeef"))

    bot, view = asyncio.run(go())
    assert isinstance(view, bot._ConfirmView)
    assert view.target_sha == "deadbeef"
    assert view.timeout is None


def test_the_restart_view_rebuilds_from_a_stored_record():
    import asyncio

    async def go():
        bot = _bot()
        return bot, bot._view_for_confirm(_entry(kind="atr_restart", engine="trocr"))

    bot, view = asyncio.run(go())
    assert isinstance(view, bot._RestartView)
    assert view.engine == "trocr"


def test_an_unknown_kind_rebuilds_nothing():
    """A record written by a newer build names a flow this one does not have.
    Binding the wrong view to its message would put a working button on the
    wrong action — worse than a dead one."""
    import asyncio

    async def go():
        return _bot()._view_for_confirm(_entry(kind="something_else"))

    assert asyncio.run(go()) is None


# ── the callback refuses with a reason, instead of Discord refusing ───────

def _interaction(user_id="111"):
    from unittest.mock import AsyncMock, MagicMock
    found = MagicMock()
    found.user.id = int(user_id)
    found.response.defer = AsyncMock()
    found.response.edit_message = AsyncMock()
    found.followup.send = AsyncMock()
    return found


def test_an_expired_click_is_answered_by_the_bot_with_a_reason():
    """Not by Discord with "This component is no longer valid", which names
    nothing and looks like a Discord problem."""
    import asyncio

    pc.add(_entry(token="old", requester="111",
                  created_at=time.time() - pc.TTL_S - 600))
    found = _interaction()

    async def go():
        view = _bot()._ConfirmView(token="old", requester="111", target_sha="x")
        await view.confirm.callback(found)

    asyncio.run(go())

    [said] = [c.args[0] for c in found.followup.send.call_args_list]
    assert "abgelaufen" in said
    assert "/update" in said
    assert "Minuten" in said           # it says how old, not just that it is old
    # And the dead record is cleared rather than left to be clicked again.
    assert pc.lookup("old")[0] == pc.MISSING


def test_a_click_with_no_record_says_so_distinctly():
    import asyncio

    found = _interaction()

    async def go():
        view = _bot()._ConfirmView(token="gone", requester="111", target_sha="x")
        await view.confirm.callback(found)

    asyncio.run(go())

    [said] = [c.args[0] for c in found.followup.send.call_args_list]
    assert "keine offene Anfrage" in said


def test_a_live_click_still_only_belongs_to_its_requester():
    """Unchanged by making the view persistent — and now it matters more, since
    the button stays clickable rather than expiring out from under itself."""
    import asyncio

    from unittest.mock import AsyncMock

    pc.add(_entry(token="t", requester="111"))
    other = _interaction(user_id="222")
    other.response.send_message = AsyncMock()

    async def go():
        view = _bot()._ConfirmView(token="t", requester="111", target_sha="x")
        return await view.interaction_check(other)

    assert asyncio.run(go()) is False
