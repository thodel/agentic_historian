"""Tests for #150 (HITL-2c): persistent views — gate clicks survive restarts.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_150_persistent_views.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import persistent_views as pv
import routing_card
from runstate import RunState, STAGES, DONE


class _FakeBot:
    def __init__(self):
        self.registered = []

    def add_view(self, view, *, message_id=None):
        self.registered.append((view, message_id))


def _state(doc="saa-0428"):
    rs = RunState(doc_id=doc)
    for s in STAGES:
        rs.stage_status[s] = DONE
    rs.criteria.update(script="Kurrent", lang="de", century=16)
    return rs


# ── custom_id parsing ────────────────────────────────────────────────────────

def test_parse_custom_id():
    assert pv.parse_custom_id("ah:saa-0428:gate1:century") == ("saa-0428", "gate1", "century")
    assert pv.parse_custom_id("ah:doc:gate2:kraken") == ("doc", "gate2", "kraken")
    assert pv.parse_custom_id("garbage") is None
    assert pv.parse_custom_id("") is None


def test_gate1_custom_ids_are_stable_and_encode_doc():
    view = asyncio.run(_abuild(_state()))
    import discord
    ids = {c.custom_id for c in view.children if isinstance(c, discord.ui.Select)}
    assert ids == {f"ah:saa-0428:gate1:{f}" for f in
                   ("century", "lang", "script", "document_type")}
    # every id round-trips through the parser
    for cid in ids:
        assert pv.parse_custom_id(cid)[0] == "saa-0428"


async def _abuild(state):
    return routing_card.build_view(state)


# ── views are persistent (timeout=None) ──────────────────────────────────────

def test_gate_view_timeout_is_none():
    view = asyncio.run(_abuild(_state()))
    assert view.timeout is None


# ── message-id persistence ───────────────────────────────────────────────────

def test_store_message_id_persists(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    st = _state("doc1")
    pv.store_message_id(st, "gate1", 999888)
    assert st.message_ids["gate1"] == 999888
    reloaded = RunState.load("doc1")
    assert reloaded.message_ids["gate1"] == 999888


# ── startup re-registration ──────────────────────────────────────────────────

def test_register_persistent_views_binds_stored_messages(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # one active run with a stored gate-1 message, one without
    a = _state("with-msg")
    pv.store_message_id(a, "gate1", 111)          # saves a
    b = _state("no-msg")
    b.save()                                       # no message id

    async def go():
        bot = _FakeBot()
        n = pv.register_persistent_views(bot)
        return bot, n

    bot, n = asyncio.run(go())
    assert n == 1
    assert len(bot.registered) == 1
    _view, msg_id = bot.registered[0]
    assert msg_id == 111


def test_register_handles_no_runs_dir(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "nonexistent")

    async def go():
        return pv.register_persistent_views(_FakeBot())
    assert asyncio.run(go()) == 0


# ── on_ready wiring ──────────────────────────────────────────────────────────

def test_on_ready_registers_and_route_stores_id():
    src = (PKG / "bot.py").read_text()
    assert "register_persistent_views(bot)" in src, "on_ready must re-register views"
    assert "store_message_id(state, \"gate1\", msg.id)" in src, "/route must store the message id"
