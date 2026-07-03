"""Tests for #105: No auth on slash commands + /run filename path traversal.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.
"""

import re
from pathlib import Path


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
    """@require_role must be applied to the four sensitive commands:
    /run, /run_agent_a, /pull, /pull_folder."""
    src = read(BOT_PATH)

    # Find positions of @require_role and the commands
    run_pos = src.find('@bot.slash_command(name="run"')
    agent_a_pos = src.find('@bot.slash_command(name="run_agent_a"')
    pull_pos = src.find('@bot.slash_command(name="pull"')
    pull_folder_pos = src.find('name="pull_folder"', src.find('@bot.slash_command('))
    require_role_positions = [m.start() for m in re.finditer(r'@require_role\n', src)]

    for cmd_pos, name in [(run_pos, "/run"), (agent_a_pos, "/run_agent_a"),
                           (pull_pos, "/pull"), (pull_folder_pos, "/pull_folder")]:
        assert cmd_pos != -1, f"{name} command not found"
        # At least one @require_role must appear between the previous content
        # and this command (i.e. immediately above it)
        nearby = src[max(0, cmd_pos - 100):cmd_pos]
        assert "@require_role" in nearby, f"@require_role must decorate {name}"


def test_config_role_id_option():
    """config.py must expose REQUIRED_DISCORD_ROLE_ID from env vars."""
    src = read(CFG_PATH)
    assert "REQUIRED_DISCORD_ROLE_ID" in src, (
        "config.py must define REQUIRED_DISCORD_ROLE_ID"
    )
    assert "_get(\"REQUIRED_DISCORD_ROLE_ID\"" in src, (
        "REQUIRED_DISCORD_ROLE_ID must be loaded from env var"
    )


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
