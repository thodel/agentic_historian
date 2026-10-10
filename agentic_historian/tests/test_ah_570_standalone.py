"""Tests for S5 (#570): the bot is a standalone process, provable in CI.

After the separation (S1–S4) the Historian bot must build and start without the
OpenClaw runtime and without the `workspace/` directory. The real proof is a CI
job that removes `workspace/`, installs only the runtime requirements, and runs
the start path up to `bot.run`. This pins that job (so it cannot be silently
dropped) and checks the start-path pieces it exercises.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402

CI = REPO_ROOT / ".github/workflows/ci.yml"


# ── the start path main() runs before bot.run ───────────────────────────────

def test_check_config_returns_a_list():
    out = config.check_config()
    assert isinstance(out, list)


def test_importing_the_bot_registers_slash_commands():
    import bot
    cmds = [c.name for c in bot.bot.pending_application_commands]
    assert cmds, "the bot is driven only by slash commands — none registered"


# ── the standalone CI job is present and proves the right thing ──────────────

def test_ci_has_a_standalone_job():
    ci = CI.read_text(encoding="utf-8")
    assert "standalone:" in ci, "the standalone CI job is missing"


def test_standalone_job_runs_without_the_workspace_and_only_runtime_deps():
    ci = CI.read_text(encoding="utf-8")
    assert "rm -rf workspace" in ci, "the standalone job must remove workspace/"
    assert "pip install -r agentic_historian/requirements.txt" in ci
    # it exercises the start path (ensure_dirs + check_config), asserts the slash
    # commands registered, and that the overlay is off — without reaching bot.run.
    assert "config.check_config()" in ci
    assert "pending_application_commands" in ci
    assert "ORCHESTRATOR_LLM_ENABLED is False" in ci
