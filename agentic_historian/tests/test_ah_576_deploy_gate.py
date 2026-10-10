"""Tests for SEC-5 (#576): /update requires its own, explicitly-set admin role.

`/update` pulls arbitrary `origin/main`, installs requirements and restarts the
process with every secret on the host. SEC-1 made the generic `admin_only` gate
fail-closed, but it still inherits the base role (config.py) and admits the
Discord-admin / guild-owner bootstrap floor. For a deploy that is too weak, so
`/update` now uses the stricter `deploy_admin_only`: a **dedicated**
`REQUIRED_ADMIN_ROLE_ID`, held — no base-role inherit, no floor, and refused for
everyone when that role is unset.

Offline: drives the decorator with a minimal ApplicationContext stub (same shape
as test_ah_105).
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import bot
import config


def _gate_ctx(*, guild=True, owner=False, roles=(), admin=False, author_id=77):
    """A minimal ApplicationContext stub for exercising the deploy gate."""
    sent = []

    async def _respond(content=None, ephemeral=False):
        sent.append(content)

    g = SimpleNamespace(id=1, owner_id=(author_id if owner else 999)) if guild else None
    author = SimpleNamespace(
        id=author_id,
        roles=[SimpleNamespace(id=r) for r in roles],
        guild_permissions=SimpleNamespace(administrator=admin, manage_guild=False),
    )
    return SimpleNamespace(guild=g, author=author, respond=_respond, sent=sent)


def _run(decorator, ctx):
    """Apply a gate decorator to a probe and invoke it; return (ran, ctx)."""
    ran = {"v": False}

    @decorator
    async def probe(ctx):
        ran["v"] = True

    asyncio.run(probe(ctx))
    return ran["v"], ctx


def test_deploy_gate_requires_the_dedicated_admin_role(monkeypatch):
    monkeypatch.setattr(config, "DEPLOY_ADMIN_ROLE_ID", 99)
    allowed, _ = _run(bot.deploy_admin_only, _gate_ctx(roles=(99,)))
    assert allowed is True
    denied, ctx = _run(bot.deploy_admin_only, _gate_ctx(roles=(1,)))
    assert denied is False and ctx.sent and "⛔" in ctx.sent[0]


def test_deploy_gate_refuses_everyone_when_unset(monkeypatch):
    """The core of SEC-5: with no dedicated admin role, /update is refused even to
    the guild owner and server admins — a deploy has no safe default operator, so
    there is no bootstrap floor (unlike the generic admin_only gate)."""
    monkeypatch.setattr(config, "DEPLOY_ADMIN_ROLE_ID", None)
    for ctx in (_gate_ctx(owner=True), _gate_ctx(admin=True), _gate_ctx()):
        ran, c = _run(bot.deploy_admin_only, ctx)
        assert ran is False, "unset dedicated admin role must refuse everyone"
        assert c.sent and "⛔" in c.sent[0]


def test_deploy_gate_does_not_inherit_the_base_role(monkeypatch):
    """A base-role holder cannot deploy unless they also hold the dedicated admin
    role — the deploy gate reads DEPLOY_ADMIN_ROLE_ID, never the base role."""
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)
    monkeypatch.setattr(config, "DEPLOY_ADMIN_ROLE_ID", 99)
    denied, ctx = _run(bot.deploy_admin_only, _gate_ctx(roles=(4242,)))
    assert denied is False and ctx.sent and "⛔" in ctx.sent[0]


def test_deploy_gate_rejects_outside_guild(monkeypatch):
    monkeypatch.setattr(config, "DEPLOY_ADMIN_ROLE_ID", 99)
    ran, ctx = _run(bot.deploy_admin_only, _gate_ctx(guild=False, roles=(99,)))
    assert ran is False and ctx.sent and "Discord-Server" in ctx.sent[0]


def test_update_command_registers_the_deploy_wrapper():
    """The registered /update callback is the deploy_admin_only wrapper (applied
    BELOW @bot.slash_command), so the gated callback is the one py-cord calls."""
    pending = {c.name: c for c in bot.bot.pending_application_commands
               if getattr(c, "name", None) == "update"}
    assert "update" in pending, "/update not registered"
    assert hasattr(pending["update"].callback, "__wrapped__")


def test_config_captures_explicit_admin_role_before_the_inherit():
    """config.DEPLOY_ADMIN_ROLE_ID is the admin role as explicitly set — it must be
    captured BEFORE REQUIRED_ADMIN_ROLE_ID inherits REQUIRED_DISCORD_ROLE_ID, or a
    base-role-only deployment would silently re-open /update."""
    src = (PKG / "config.py").read_text(encoding="utf-8")
    assert "DEPLOY_ADMIN_ROLE_ID: int" in src
    cap = src.index("DEPLOY_ADMIN_ROLE_ID: int")
    inherit = src.index("REQUIRED_ADMIN_ROLE_ID = REQUIRED_DISCORD_ROLE_ID")
    assert cap < inherit, "DEPLOY_ADMIN_ROLE_ID must be captured before the base-role inherit"
