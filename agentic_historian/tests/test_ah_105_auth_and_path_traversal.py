"""Tests for #105: No auth on slash commands + /run filename path traversal.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.
"""

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest


BOT_PATH = "agentic_historian/bot.py"
CFG_PATH = "agentic_historian/config.py"


def read(path):
    with open(path) as f:
        return f.read()


# ── Part 1: Role-gating decorator exists and is applied ──────────────────────

def test_require_role_decorator_exists():
    """bot.py must define a require_role decorator that checks guild membership
    and, if REQUIRED_DISCORD_ROLE_ID is set, the user's role."""
    src = read(BOT_PATH)
    assert "def require_role(func)" in src, "require_role decorator must be defined"
    assert "REQUIRED_DISCORD_ROLE_ID" in src, "must reference REQUIRED_DISCORD_ROLE_ID"
    assert "ctx.guild" in src, "must check ctx.guild (guild-only)"
    assert "author_role_ids" in src or "role.id" in src, "must inspect user roles"


def test_require_role_applied_to_sensitive_commands():
    """@require_role must decorate the four sensitive commands
    (/run, /run_agent_a, /pull, /pull_folder) — and, critically, it must sit
    BELOW @bot.slash_command (between the command decorator and `async def`).
    If it sits above, py-cord registers the un-gated callback (dead code).
    """
    src = read(BOT_PATH)

    run_pos = src.find('@bot.slash_command(name="run"')
    agent_a_pos = src.find('@bot.slash_command(name="run_agent_a"')
    pull_pos = src.find('@bot.slash_command(name="pull"')
    pull_folder_pos = src.find('@bot.slash_command(', src.find('name="pull_folder"') - 200)

    for cmd_pos, name in [(run_pos, "/run"), (agent_a_pos, "/run_agent_a"),
                           (pull_pos, "/pull"), (pull_folder_pos, "/pull_folder")]:
        assert cmd_pos != -1, f"{name} command not found"
        def_pos = src.find("async def", cmd_pos)
        assert def_pos != -1, f"{name}: no async def after command decorator"
        # @require_role must appear between the slash_command decorator and def
        between = src[cmd_pos:def_pos]
        assert "@require_role" in between, (
            f"@require_role must decorate {name} BELOW @bot.slash_command "
            f"(so the gated wrapper is the registered callback)"
        )


def test_config_role_id_option():
    """config.py must expose REQUIRED_DISCORD_ROLE_ID from env vars."""
    src = read(CFG_PATH)
    assert "REQUIRED_DISCORD_ROLE_ID" in src, (
        "config.py must define REQUIRED_DISCORD_ROLE_ID"
    )
    assert "_get(\"REQUIRED_DISCORD_ROLE_ID\"" in src, (
        "REQUIRED_DISCORD_ROLE_ID must be loaded from env var"
    )


# ── Part 1b: Functional — the gate is actually the registered callback ───────

def test_sensitive_commands_are_functionally_gated():
    """Regression for the decorator-ORDER bug: string checks alone pass even
    when @require_role sits ABOVE @bot.slash_command, in which case py-cord
    registers the UN-gated callback and the role check becomes dead code.

    py-cord's slash_command decorator must be the OUTER one so it registers the
    require_role wrapper.  We assert the actually-registered callback is the
    functools.wraps wrapper (has __wrapped__) and that Options survived.
    """
    import bot as bot_module

    sensitive = {"run", "run_agent_a", "pull", "pull_folder"}
    registered = {
        c.name: c
        for c in bot_module.bot.pending_application_commands
        if getattr(c, "name", None) in sensitive
    }
    assert sensitive.issubset(registered), (
        f"missing sensitive commands: {sensitive - set(registered)}"
    )
    for name, cmd in registered.items():
        cb = cmd.callback
        assert hasattr(cb, "__wrapped__"), (
            f"/{name} registered callback is NOT the require_role wrapper — "
            f"@require_role must be applied BELOW @bot.slash_command"
        )
    # Options must survive the wrapper (py-cord follows __wrapped__ for signature)
    assert [o.name for o in registered["run"].options] == ["filename"], (
        "the require_role wrapper dropped the /run 'filename' option"
    )


# ── Part 1c: SEC-1 (#572) — more commands gated + fail-closed gate ───────────

# The state-changing / expensive commands that SEC-1 adds @require_role to.
NEWLY_GATED = ("route", "votes", "hotfolder", "agent_d", "agent_e")


def test_sec1_newly_gated_commands_have_require_role():
    """SEC-1: /route, /votes, /hotfolder, /agent_d, /agent_e must be gated,
    with @require_role BELOW @bot.slash_command so the gated wrapper registers."""
    src = read(BOT_PATH)
    for name in NEWLY_GATED:
        start = src.find(f'name="{name}"')
        assert start != -1, f"/{name} command not found"
        between = src[start:src.find("async def", start)]
        assert "@require_role" in between, (
            f"/{name} must be decorated with @require_role below @bot.slash_command"
        )


def test_sec1_newly_gated_commands_are_functionally_wrapped():
    """The registered callback for each newly gated command must be the
    require_role wrapper (functools.wraps → __wrapped__), not the raw function."""
    import bot as bot_module

    want = set(NEWLY_GATED)
    registered = {
        c.name: c
        for c in bot_module.bot.pending_application_commands
        if getattr(c, "name", None) in want
    }
    assert want.issubset(registered), f"missing gated commands: {want - set(registered)}"
    for name, cmd in registered.items():
        assert hasattr(cmd.callback, "__wrapped__"), (
            f"/{name} registered callback is NOT the require_role wrapper — "
            f"@require_role must be applied BELOW @bot.slash_command"
        )


def _gate_ctx(*, guild=True, owner=False, roles=(), admin=False, author_id=77):
    """A minimal ApplicationContext stub for exercising the role gate."""
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


def _run_gate(decorator, ctx):
    """Apply a gate decorator to a probe and invoke it; return (ran, ctx)."""
    ran = {"v": False}

    @decorator
    async def probe(ctx):
        ran["v"] = True
        return "ran"

    asyncio.run(probe(ctx))
    return ran["v"], ctx


def test_sec1_gate_rejects_outside_guild(monkeypatch):
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)
    ran, ctx = _run_gate(bot.require_role, _gate_ctx(guild=False, roles=(4242,)))
    assert ran is False
    assert ctx.sent and "Discord-Server" in ctx.sent[0]


def test_sec1_fail_closed_denies_unconfigured_ordinary_member(monkeypatch):
    """The core of SEC-1: with no role configured, an ordinary member is REFUSED
    (not allowed, as the old fail-open behaviour did)."""
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", None)
    ran, ctx = _run_gate(bot.require_role, _gate_ctx(owner=False, admin=False))
    assert ran is False, "unconfigured gate must refuse ordinary members (fail-closed)"
    assert ctx.sent and "⛔" in ctx.sent[0]


def test_sec1_fail_closed_allows_guild_owner(monkeypatch):
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", None)
    ran, _ = _run_gate(bot.require_role, _gate_ctx(owner=True))
    assert ran is True, "the guild owner is the bootstrap floor when unconfigured"


def test_sec1_fail_closed_allows_server_admin(monkeypatch):
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", None)
    ran, _ = _run_gate(bot.require_role, _gate_ctx(owner=False, admin=True))
    assert ran is True, "a Discord server admin may run gated commands when unconfigured"


def test_sec1_configured_role_is_strict(monkeypatch):
    """With a role configured, holding the role is required — a server admin who
    lacks it is NOT silently widened in."""
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_DISCORD_ROLE_ID", 4242)
    denied, ctx = _run_gate(bot.require_role, _gate_ctx(roles=(), admin=True))
    assert denied is False
    allowed, _ = _run_gate(bot.require_role, _gate_ctx(roles=(4242,)))
    assert allowed is True


def test_sec1_admin_only_is_fail_closed(monkeypatch):
    """admin_only must also refuse ordinary members when no admin role is set."""
    import bot
    import config
    monkeypatch.setattr(config, "REQUIRED_ADMIN_ROLE_ID", None)
    ran, _ = _run_gate(bot.admin_only, _gate_ctx(owner=False, admin=False))
    assert ran is False


# ── Part 2: Path traversal fix ────────────────────────────────────────────────

def test_fp_resolve_used_in_run_commands():
    """Both /run and /run_agent_a must resolve the file path before use:
    fp = (config.HOT_FOLDER / filename).resolve()  — not fp = config.HOT_FOLDER / filename"""
    src = read(BOT_PATH)

    # Find the two fp = assignments inside the command handlers
    # (not the import of config)
    fp_assignments = [
        m.start() for m in re.finditer(r'fp\s*=\s*\(config\.HOT_FOLDER\s*/\s*filename\)\.resolve\(\)', src)
    ]
    assert len(fp_assignments) >= 2, (
        f"Expected 2 resolved fp assignments (run_pipeline + run_agent_a), "
        f"found {len(fp_assignments)}"
    )


def test_is_relative_to_check():
    """Both /run and /run_agent_a must verify the resolved path is still
    inside HOT_FOLDER using is_relative_to."""
    src = read(BOT_PATH)
    count = src.count("is_relative_to(config.HOT_FOLDER.resolve())")
    assert count >= 2, (
        f"Expected 2 is_relative_to checks (run_pipeline + run_agent_a), "
        f"found {count}"
    )


def test_path_traversal_rejected():
    """If a user provides ../../etc/passwd as filename, the is_relative_to
    check must reject it with a clear error message."""
    src = read(BOT_PATH)

    # The rejection message must mention the access restriction
    assert "Zugriff ausserhalb" in src or "outside" in src.lower(), (
        "Path escape must produce a clear error message"
    )


# ── Functional: demonstrate the fix ─────────────────────────────────────────

def test_resolve_prevents_dotdot_escape():
    """Path.resolve() collapses '../' components.  Simulate what happens:
    HOT_FOLDER=/data/hot_folder  +  ../../etc/passwd  →  /etc/passwd
    is_relative_to resolves to False → access denied."""
    # Simulate the check
    from pathlib import Path

    hot_folder = Path("/data/hot_folder").resolve()
    malicious = (hot_folder / "../../../etc/passwd").resolve()

    # Before the fix: fp = HOT_FOLDER / filename would give /etc/passwd (EXISTS!)
    # After the fix: fp.resolve().is_relative_to(HOT_FOLDER) gives False
    assert not malicious.is_relative_to(hot_folder), (
        "Malicious path must be detected as outside HOT_FOLDER"
    )

    # Normal file must still work
    normal = (hot_folder / "scan_001.jpg").resolve()
    assert normal.is_relative_to(hot_folder), "Normal file must pass the check"


def test_dotdot_not_collapsing_in_naive_concat():
    """Concatenating HOT_FOLDER / '../../etc/passwd' gives a different path
    than resolving first — the is_relative_to check catches this."""
    from pathlib import Path

    hot_folder = Path("/data/hot_folder")
    # The naive concatenation
    naive = hot_folder / "../../../etc/passwd"
    # is_relative_to works on the *resolved* path
    assert not naive.resolve().is_relative_to(hot_folder.resolve())


if __name__ == "__main__":
    test_require_role_decorator_exists()
    print("PASS: test_require_role_decorator_exists")

    test_require_role_applied_to_sensitive_commands()
    print("PASS: test_require_role_applied_to_sensitive_commands")

    test_config_role_id_option()
    print("PASS: test_config_role_id_option")

    test_fp_resolve_used_in_run_commands()
    print("PASS: test_fp_resolve_used_in_run_commands")

    test_is_relative_to_check()
    print("PASS: test_is_relative_to_check")

    test_path_traversal_rejected()
    print("PASS: test_path_traversal_rejected")

    test_resolve_prevents_dotdot_escape()
    print("PASS: test_resolve_prevents_dotdot_escape")

    test_dotdot_not_collapsing_in_naive_concat()
    print("PASS: test_dotdot_not_collapsing_in_naive_concat")

    print("\nAll #105 tests passed.")
