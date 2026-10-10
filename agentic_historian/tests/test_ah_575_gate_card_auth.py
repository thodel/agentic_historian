"""Tests for SEC-4 (#575): the HITL gate cards must enforce the role gate.

The Gate-1 (``routing_card``) and Gate-2 (``path_compare``) views defined their
select/button callbacks with **no** ``interaction_check``, so anyone who could
see the card could change routing criteria (Gate 1) or cast a vote that flows
into the preference log, the routing prior and the published RDF export
(Gate 2). Both views now share ``gate_guard.enforce_gate`` — the same
fail-closed rule the slash commands use (SEC-1, #572).

Offline: builds the real Discord views and drives their ``interaction_check``
with stub interactions (no live Discord, no network). Follows the asyncio.run
pattern of test_ah_146 — a py-cord View must be constructed on a running loop.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
import gate_guard
import path_compare
import routing_card
from runstate import RunState

# Two short, non-empty candidate readings → a two-button Gate-2 card.
GATE2_PATHS = {"vlm": "hallo welt", "kraken": "hallo wel"}


# ── stubs ────────────────────────────────────────────────────────────────────

def _member(*, roles=(), admin=False, member_id=77):
    return SimpleNamespace(
        id=member_id,
        roles=[SimpleNamespace(id=r) for r in roles],
        guild_permissions=SimpleNamespace(administrator=admin, manage_guild=False),
    )


def _interaction(*, guild=True, owner=False, roles=(), admin=False, member_id=77):
    """A minimal py-cord Interaction stub for exercising interaction_check."""
    sent = []

    class _Resp:
        def __init__(self):
            self._done = False

        def is_done(self):
            return self._done

        async def send_message(self, content=None, ephemeral=False):
            self._done = True
            sent.append(content)

    g = SimpleNamespace(id=1, owner_id=(member_id if owner else 999)) if guild else None
    return SimpleNamespace(
        user=_member(roles=roles, admin=admin, member_id=member_id),
        guild=g,
        response=_Resp(),
        sent=sent,
    )


# ── the shared rule: gate_guard.member_authorised (fail-closed, #572) ────────

def test_configured_role_is_strict(monkeypatch):
    """With a role configured, only the role-holder passes — a server admin who
    lacks it is NOT silently widened in (same as the slash-command gate)."""
    g = SimpleNamespace(id=1, owner_id=999)
    assert gate_guard.member_authorised(_member(roles=(4242,)), g, 4242) is True
    assert gate_guard.member_authorised(_member(roles=(), admin=True), g, 4242) is False


def test_unconfigured_is_fail_closed():
    """No role configured → refuse everyone but the admin / guild owner floor."""
    g = SimpleNamespace(id=1, owner_id=7)
    assert gate_guard.member_authorised(_member(member_id=7), g, None) is True       # owner
    assert gate_guard.member_authorised(_member(member_id=2, admin=True), g, None) is True
    assert gate_guard.member_authorised(_member(member_id=2), g, None) is False       # member
    # Outside a guild nobody passes when the gate is unconfigured.
    assert gate_guard.member_authorised(_member(member_id=2), None, None) is False


# ── enforce_gate: silent allow vs refuse-with-ephemeral-notice ───────────────

def test_enforce_gate_allows_role_holder(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)
    itx = _interaction(roles=(4242,))
    assert asyncio.run(gate_guard.enforce_gate(itx)) is True
    assert itx.sent == []                       # a permitted click is not interrupted


def test_enforce_gate_refuses_and_notifies(monkeypatch):
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)
    itx = _interaction(roles=())
    assert asyncio.run(gate_guard.enforce_gate(itx)) is False
    assert itx.sent and "⛔" in itx.sent[0]


# ── the views actually carry (and enforce) the check ─────────────────────────

def test_gate1_view_enforces_role(monkeypatch):
    """Gate-1 (routing card) refuses a non-holder and admits the role-holder."""
    import discord
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)

    async def _run():
        st = RunState(doc_id="sec4")
        st.criteria.update(script="Kurrent", lang="de", century=16)
        view = routing_card.build_view(st)
        assert type(view).interaction_check is not discord.ui.View.interaction_check
        ok = await view.interaction_check(_interaction(roles=(4242,)))
        denied_itx = _interaction(roles=())
        denied = await view.interaction_check(denied_itx)
        return ok, denied, denied_itx.sent

    ok, denied, sent = asyncio.run(_run())
    assert ok is True
    assert denied is False and sent and "⛔" in sent[0]


def test_gate2_view_enforces_role(monkeypatch):
    """Gate-2 (vote card) refuses a non-holder and admits the role-holder."""
    import discord
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)

    async def _run():
        view = path_compare.build_view(RunState(doc_id="sec4"), GATE2_PATHS)
        assert type(view).interaction_check is not discord.ui.View.interaction_check
        ok = await view.interaction_check(_interaction(roles=(4242,)))
        denied_itx = _interaction(roles=())
        denied = await view.interaction_check(denied_itx)
        return ok, denied, denied_itx.sent

    ok, denied, sent = asyncio.run(_run())
    assert ok is True
    assert denied is False and sent and "⛔" in sent[0]


def test_persistent_rebind_carries_the_check(monkeypatch):
    """The views #150 rebinds on startup (timeout=None, so clickable forever) are
    built via build_view, so they carry the check too — the bug's main vector."""
    import discord
    import persistent_views
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)

    async def _run():
        v1 = persistent_views._build_view_for_gate(RunState(doc_id="g1"), "gate1")
        st2 = RunState(doc_id="g2")
        st2.artifacts["paths"] = GATE2_PATHS
        v2 = persistent_views._build_view_for_gate(st2, "gate2")
        return v1, v2

    v1, v2 = asyncio.run(_run())
    assert v1 is not None and v2 is not None
    assert type(v1).interaction_check is not discord.ui.View.interaction_check
    assert type(v2).interaction_check is not discord.ui.View.interaction_check


# ── one rule, two surfaces: cards and slash commands must agree ──────────────

def test_card_gate_matches_command_gate():
    """gate_guard (cards) and bot._authorised (slash commands) must reach the same
    fail-closed decision for every case — so the two gates cannot drift (#575)."""
    import bot

    # (role_id, roles, admin, owner)
    cases = [
        (4242, (4242,), False, False),   # configured: holder
        (4242, (), True, False),         # configured: admin without the role
        (4242, (), False, False),        # configured: plain member
        (None, (), False, True),         # unconfigured: guild owner
        (None, (), True, False),         # unconfigured: server admin
        (None, (), False, False),        # unconfigured: ordinary member
    ]
    for role_id, roles, admin, owner in cases:
        itx = _interaction(roles=roles, admin=admin, owner=owner)
        ctx = SimpleNamespace(author=itx.user, guild=itx.guild)
        via_card = gate_guard.member_authorised(itx.user, itx.guild, role_id)
        via_cmd = bot._authorised(ctx, role_id, what="base")
        assert via_card == via_cmd, (role_id, roles, admin, owner, via_card, via_cmd)


def test_both_views_wire_the_shared_guard():
    """Source guard: both gate views define interaction_check and route it through
    gate_guard, so the rule lives in one place."""
    for mod in (routing_card, path_compare):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "interaction_check" in src, f"{mod.__name__} must define interaction_check"
        assert "gate_guard" in src, f"{mod.__name__} must delegate to gate_guard"
