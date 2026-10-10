"""Tests for SEC-11 (#582): cost/DoS — one FIFO queue, expensive commands.

All blocking work runs through a single-worker FIFO queue, so an unthrottled
member can hold the queue (and the GPUs/LLM) by repeating an expensive command,
and /entity rebuilt the entity index on every call. SEC-1 gated WHO can run the
heavy commands; this adds a per-user cooldown for HOW OFTEN, and caches the
entity index.

Pure logic — the cooldown helper and the cache are exercised directly (no queue,
so no event-loop coupling), plus a wiring check that the three heavy commands
call the cooldown.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import asyncio

import bot          # noqa: E402
import entity_index  # noqa: E402


def _ctx(uid=5):
    sent = []

    async def _send(content=None, **kw):
        sent.append(content)

    return SimpleNamespace(author=SimpleNamespace(id=uid),
                           followup=SimpleNamespace(send=_send), sent=sent)


def setup_function(_):
    bot._last_expensive.clear()
    bot._entity_index_cache.update(index=None, at=0.0)


# ── the cooldown ─────────────────────────────────────────────────────────────

def test_first_run_is_allowed_then_the_next_is_blocked():
    ctx = _ctx()
    assert bot._cooldown_remaining(ctx, "hotfolder") == 0.0      # allowed, recorded
    assert bot._cooldown_remaining(ctx, "hotfolder") > 0.0       # too soon


def test_cooldown_is_per_user():
    assert bot._cooldown_remaining(_ctx(1), "agent_d") == 0.0
    assert bot._cooldown_remaining(_ctx(2), "agent_d") == 0.0    # a different user is independent


def test_cooldown_is_per_command():
    ctx = _ctx()
    assert bot._cooldown_remaining(ctx, "agent_d") == 0.0
    assert bot._cooldown_remaining(ctx, "agent_e") == 0.0        # a different command is independent


def test_cooldown_clears_after_the_window():
    ctx = _ctx()
    bot._cooldown_remaining(ctx, "agent_e")
    # Push the recorded time back beyond the window.
    bot._last_expensive[(ctx.author.id, "agent_e")] = time.time() - (bot.EXPENSIVE_COOLDOWN_S + 1)
    assert bot._cooldown_remaining(ctx, "agent_e") == 0.0


def test_cooldown_block_warns_only_on_the_second_call():
    ctx = _ctx()
    assert asyncio.run(bot._cooldown_block(ctx, "hotfolder")) is False
    assert ctx.sent == []
    assert asyncio.run(bot._cooldown_block(ctx, "hotfolder")) is True
    assert ctx.sent and "⏳" in ctx.sent[-1] and "hotfolder" in ctx.sent[-1]


# ── the entity-index cache ───────────────────────────────────────────────────

def test_entity_index_is_built_once_within_the_ttl(monkeypatch):
    builds = []
    monkeypatch.setattr(entity_index, "build_index", lambda root: builds.append(root) or {"i": len(builds)})
    first = bot._get_entity_index()
    second = bot._get_entity_index()
    assert first is second and len(builds) == 1       # one build serves both


def test_entity_index_rebuilds_after_the_ttl(monkeypatch):
    builds = []
    monkeypatch.setattr(entity_index, "build_index", lambda root: builds.append(root) or {"i": len(builds)})
    bot._get_entity_index()
    bot._entity_index_cache["at"] = time.time() - (bot.ENTITY_INDEX_TTL_S + 1)
    bot._get_entity_index()
    assert len(builds) == 2


# ── the heavy commands are wired to the cooldown ─────────────────────────────

def test_expensive_commands_call_the_cooldown():
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    for command in ("hotfolder", "agent_d", "agent_e"):
        assert f'_cooldown_block(ctx, "{command}")' in src, f"/{command} is not throttled"
