"""Tests for SEC-10 (#581): the RunState-load read oracle.

`/route <doc_id>` and `/votes <doc_id>` load a file into the pydantic model. A
doc_id pointing at a neighbour (e.g. `../mcp_oauth`, the MCP server's token store)
used to fail with a ValidationError whose text held the start of that file — and
the bot posted it as `❌ Error: {e}`, turning every .json beside data/runs/ into a
read oracle.

Two things close it: SEC-2 already refuses a traversal doc_id before it loads, and
the handlers no longer post a raw load error (generic message, detail to the log).
This drives the real command callbacks with a stub ctx and proves neither a
traversal nor an injected load error reaches the channel.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import bot          # noqa: E402
import runstate     # noqa: E402

SECRET = "SECRET_TOKEN_9f3a_and_the_head_of_a_neighbour_file"


def _ctx(role_id=7):
    sent = []

    async def _send(content=None, **kw):
        sent.append(content)

    async def _noop(*a, **k):
        pass

    author = SimpleNamespace(
        id=5, roles=[SimpleNamespace(id=role_id)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False))
    return SimpleNamespace(
        guild=SimpleNamespace(id=1, owner_id=999), author=author,
        defer=_noop, respond=_send, followup=SimpleNamespace(send=_send), sent=sent)


def _callback(name):
    cmd = next(c for c in bot.bot.pending_application_commands
               if getattr(c, "name", None) == name)
    return cmd.callback


def _authorise(monkeypatch):
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 7)


def test_route_does_not_leak_a_load_error(monkeypatch):
    _authorise(monkeypatch)

    def _raise(cls, doc_id):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(runstate.RunState, "load_or_new", classmethod(_raise))

    ctx = _ctx()
    asyncio.run(_callback("route")(ctx, doc_id="BAT_664"))     # valid slug → reaches load

    assert ctx.sent, "the command said nothing"
    assert SECRET not in ctx.sent[-1], "the load error text leaked into the channel"
    assert "Details siehe Log" in ctx.sent[-1]


def test_votes_does_not_leak_a_load_error(monkeypatch):
    _authorise(monkeypatch)
    monkeypatch.setattr(runstate.RunState, "exists", classmethod(lambda cls, d: True))

    def _raise(cls, doc_id):
        raise RuntimeError(SECRET)
    monkeypatch.setattr(runstate.RunState, "load_or_new", classmethod(_raise))

    ctx = _ctx()
    asyncio.run(_callback("votes")(ctx, doc_id="BAT_664"))

    assert ctx.sent
    assert SECRET not in ctx.sent[-1]
    assert "Details siehe Log" in ctx.sent[-1]


def test_route_rejects_a_traversal_doc_id(monkeypatch):
    """SEC-2's slug check fires before any load, so `../mcp_oauth` never reads a
    file and never errors — it gets a clean 'invalid id' message."""
    _authorise(monkeypatch)
    ctx = _ctx()
    asyncio.run(_callback("route")(ctx, doc_id="../mcp_oauth"))

    assert ctx.sent and "Ungültige Dokument-ID" in ctx.sent[-1]
    assert "Error" not in ctx.sent[-1]


def test_votes_reports_not_found_for_a_traversal_doc_id(monkeypatch):
    """RunState.exists swallows the traversal guard's error and returns False, so
    `/votes ../mcp_oauth` is 'no such run', not a leaking error."""
    _authorise(monkeypatch)
    ctx = _ctx()
    asyncio.run(_callback("votes")(ctx, doc_id="../mcp_oauth"))

    assert ctx.sent and "Kein Lauf" in ctx.sent[-1]
    assert "Error" not in ctx.sent[-1]
