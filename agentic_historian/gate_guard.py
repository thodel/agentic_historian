"""
gate_guard.py — role check shared by the interactive HITL gate cards (SEC-4, #575).

The Gate-1 (``routing_card``) and Gate-2 (``path_compare``) views let a click
change routing criteria or cast a vote — and a Gate-2 vote flows into the
preference log, the routing prior and the published RDF export, i.e. into the
research data. Unlike the ``/update`` and ``/mcp_propose`` views, the gate views
carried **no** ``interaction_check``, so anyone who could see the card could
operate it. Because ``persistent_views`` rebinds every card with ``timeout=None``
on startup, old cards stayed clickable indefinitely.

This module is the one place that answers "may this member operate a gate card?"
and both views call it through :func:`enforce_gate`. The rule mirrors the
fail-closed slash-command gate from SEC-1 (#572): with a role configured, the
member must hold exactly that role; with no role configured, everyone but a
Discord server admin / the guild owner is refused (and a warning is logged so the
gate gets configured). A parity test (test_ah_575) pins this to ``bot._authorised``
so the two gates cannot drift apart.

Bot-free on purpose: it reads :mod:`config` and a Member/Guild duck-typed object
(a command context's author, or an interaction's user), never ``discord`` or
``bot``, so it carries no import cycle into the core modules that build the views.
"""

from __future__ import annotations

from loguru import logger

import config


def member_role_ids(member) -> set:
    """The role ids *member* carries (empty set when unknown)."""
    return {role.id for role in getattr(member, "roles", None) or []}


def is_guild_admin(member, guild) -> bool:
    """A Discord server admin or the guild owner — the floor when no role gate is
    configured, so ordinary members are refused but the operator is not."""
    if guild is not None and getattr(member, "id", None) == getattr(guild, "owner_id", None):
        return True
    perms = getattr(member, "guild_permissions", None)
    return bool(perms is not None
                and (getattr(perms, "administrator", False)
                     or getattr(perms, "manage_guild", False)))


def member_authorised(member, guild, role_id, *, what: str = "gate") -> bool:
    """Whether *member* may operate a gated surface (fail-closed, #572).

    A configured ``role_id`` → the member must hold exactly that role. No role id
    → refuse all but Discord server admins / the guild owner, and warn so the gate
    gets configured. This is the same decision the slash-command gate makes in
    ``bot._authorised``; keep the two in step (see test_ah_575).
    """
    if role_id:
        return role_id in member_role_ids(member)
    logger.warning(
        "[auth] no {} role configured — refusing all but Discord server admins. "
        "Set REQUIRED_DISCORD_ROLE_ID (and REQUIRED_ADMIN_ROLE_ID) to gate by role.",
        what)
    return is_guild_admin(member, guild)


async def enforce_gate(interaction) -> bool:
    """The ``interaction_check`` body shared by the Gate-1 and Gate-2 views (#575).

    Authorises the clicking member against the configured base role (fail-closed).
    Returns True to let py-cord run the component callback; on refusal posts an
    ephemeral notice and returns False so the callback is skipped. Never raises —
    the denial stands even if the notice cannot be posted.
    """
    member = getattr(interaction, "user", None)
    guild = getattr(interaction, "guild", None)
    if member_authorised(member, guild, getattr(config, "REQUIRED_DISCORD_ROLE_ID", None)):
        return True
    try:
        await interaction.response.send_message(
            "⛔ Du hast nicht die erforderliche Rolle, um diese Karte zu bedienen.",
            ephemeral=True)
    except Exception as e:  # noqa: BLE001 — the denial holds even if the notice fails
        logger.warning("[auth] gate refusal notice failed: {}", e)
    return False
