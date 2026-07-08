"""
Tests for updater.py (issue #246 — P3-1).
All subprocess calls intercepted via MockRunner — runs fully offline.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agentic_historian import updater as _updater
from agentic_historian.updater import (
    SubprocessRunner, CmdResult, fetch_status, apply_update,
    write_marker, read_marker, clear_marker, _MARKER_PATH,
    _SMOKE_COMMAND,
)


# ── MockRunner ───────────────────────────────────────────────────────────────

class MockRunner(SubprocessRunner):
    """
    Intercepts every .run() call.
    For smoke/pip commands the mock returns programmatic results.
    For git commands the sequence is consumed in order (keyed on normalised
    subcommand string).
    """

    def __init__(self, sequences: dict[str, list[CmdResult]] | None = None):
        super().__init__()
        self._seq: dict[str, list[CmdResult]] = sequences or {}
        self.calls: list[str] = []
        # Per-instance result for the smoke command (controlled by test).
        self._smoke_result: CmdResult = CmdResult(True, "smoke-ok\n", "", 0)
        self._pip_result: CmdResult = CmdResult(True, "Requirements satisfied.\n", "", 0)

    def _normalise(self, cmd) -> str:
        parts = cmd.split() if isinstance(cmd, str) else list(cmd)
        if len(parts) >= 4 and parts[0] == "git" and parts[1] == "-C":
            subcmd = parts[3]
            rest = parts[4:]
            return subcmd + (" " + " ".join(rest) if rest else "")
        return " ".join(parts)

    def run(self, cmd, cwd=None, check=False):
        key = self._normalise(cmd)
        self.calls.append(key)

        cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd

        # pip install — always intercepted by instance attribute.
        if "pip install" in cmd_str:
            return self._pip_result

        # Smoke test command — always intercepted.
        if cmd_str == _updater._SMOKE_COMMAND:
            return self._smoke_result

        # Git commands from sequence.
        if key in self._seq and self._seq[key]:
            return self._seq[key].pop(0)
        return CmdResult(True, "", "", 0)


# ── helpers ──────────────────────────────────────────────────────────────────

def seq(**kwargs) -> dict[str, list[CmdResult]]:
    out = {}
    for k, v in kwargs.items():
        ok, stdout, stderr = v
        out.setdefault(k, []).append(CmdResult(ok, stdout, stderr, 0 if ok else 1))
    return out


def updater_mod():
    return _updater


# ── fetch_status() tests ─────────────────────────────────────────────────────

def test_fetch_status_ahead_2(mock_runner):
    mock_runner._seq = seq(
        status__porcelain__uno=(True, "", ""),
        rev_parse_HEAD=(True, "abc123def456\n", ""),
        fetch_origin_main=(True, "", ""),
        rev_parse_refs_remotes_origin_main=(True, "def456abc123\n", ""),
        rev_list_left_right_count=(True, "0\t2\n", ""),
        log_FORMAT_H_s=(True, "abc123def456 commit one\ndef456abc123 commit two\n", ""),
    )
    # Normalise keys must match what _normalise() produces.
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t2\n", ""),
            "log --format=%H %s -n 2 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 commit one\ndef456abc123 commit two\n", ""
            ),
        }
    )

    status = fetch_status(runner=mock_runner)

    assert status["ok"] is True
    assert status["ahead"] == 2
    assert status["behind"] == 0
    assert status["diverged"] is False
    assert status["dirty"] is False
    assert status["current_sha"].startswith("abc123")
    assert status["target_sha"].startswith("def456")
    assert len(status["commits"]) == 2


def test_fetch_status_diverged(mock_runner):
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "3\t1\n", ""),
            "log --format=%H %s -n 1 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 one commit\n", ""
            ),
        }
    )

    status = fetch_status(runner=mock_runner)

    assert status["diverged"] is True
    assert status["ahead"] == 1
    assert status["behind"] == 3


def test_fetch_status_dirty(mock_runner):
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "M  agentic_historian/bot.py\n", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t0\n", ""),
        }
    )

    status = fetch_status(runner=mock_runner)
    assert status["dirty"] is True


def test_fetch_status_fetch_fails(mock_runner):
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (
                False, "", "fatal: couldn't find remote ref main"
            ),
        }
    )

    status = fetch_status(runner=mock_runner)
    assert status["ok"] is False
    assert "fetch failed" in status["error"]


# ── apply_update() tests ─────────────────────────────────────────────────────

def test_apply_update_happy_path(mock_runner):
    """pull + pip + smoke succeed → ok=True with from/to SHAs."""
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t1\n", ""),
            "log --format=%H %s -n 1 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 commit one\n", ""
            ),
            "pull --ff-only": (True, "Fast-forward\n", ""),
            "rev-parse HEAD": (True, "def456abc123\n", ""),
        }
    )
    # Default smoke/pip results are success — just run apply_update.
    result = apply_update(runner=mock_runner)

    assert result["ok"] is True
    assert "from_sha" in result
    assert "to_sha" in result
    assert "rolled_back" not in result


def test_apply_update_dirty_aborts(mock_runner):
    """Dirty tree → aborts before pulling."""
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "M  agentic_historian/bot.py\n", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t0\n", ""),
        }
    )

    result = apply_update(runner=mock_runner)

    assert result["ok"] is False
    assert result["stage"] == "dirty"
    assert result["rolled_back"] is False


def test_apply_update_diverged_aborts(mock_runner):
    """Diverged → aborts before pulling."""
    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "3\t1\n", ""),
            "log --format=%H %s -n 1 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 one commit\n", ""
            ),
        }
    )

    result = apply_update(runner=mock_runner)

    assert result["ok"] is False
    assert result["stage"] == "diverged"
    assert result["rolled_back"] is False


def test_apply_update_smoke_failure_rolls_back(mock_runner):
    """Smoke test fails → reset --hard pre_sha, rolled_back=True."""
    reset_calls: list[str] = []

    original_run = SubprocessRunner.run

    def tracking_run(self, cmd, cwd=None, check=False):
        key = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "reset --hard" in key:
            reset_calls.append(key)
        if "pip install" in key:
            return CmdResult(True, "Installed.\n", "", 0)
        return original_run(self, cmd, cwd=cwd, check=check)

    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t1\n", ""),
            "log --format=%H %s -n 1 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 one commit\n", ""
            ),
            "pull --ff-only": (True, "Fast-forward\n", ""),
            "rev-parse HEAD": (True, "def456abc123\n", ""),
        }
    )
    # Override smoke to fail.
    mock_runner._smoke_result = CmdResult(False, "", "ImportError: cannot import bot", 1)

    with patch.object(SubprocessRunner, "run", tracking_run):
        result = apply_update(runner=mock_runner)

    assert result["ok"] is False
    assert result["stage"] == "smoke"
    assert result["rolled_back"] is True
    assert result["pre_sha"].startswith("abc123")
    assert any("reset" in c and "abc123" in c for c in reset_calls), \
        f"reset not called. Got: {reset_calls}"


def test_apply_update_pip_failure_rolls_back(mock_runner):
    """pip install fails → rollback."""
    reset_calls: list[str] = []
    original_run = SubprocessRunner.run

    def tracking_run(self, cmd, cwd=None, check=False):
        key = " ".join(cmd) if isinstance(cmd, list) else cmd
        if "reset --hard" in key:
            reset_calls.append(key)
        if "pip install" in key:
            return CmdResult(False, "", "No such file: requirements-dev.txt", 1)
        return original_run(self, cmd, cwd=cwd, check=check)

    mock_runner._seq = seq(
        **{
            "status --porcelain -uno": (True, "", ""),
            "rev-parse HEAD": (True, "abc123def456\n", ""),
            "fetch origin main:refs/remotes/origin/main": (True, "", ""),
            "rev-parse refs/remotes/origin/main": (True, "def456abc123\n", ""),
            "rev-list --left-right --count HEAD...refs/remotes/origin/main": (True, "0\t1\n", ""),
            "log --format=%H %s -n 1 refs/remotes/origin/main..HEAD": (
                True, "abc123def456 one commit\n", ""
            ),
            "pull --ff-only": (True, "Fast-forward\n", ""),
            "rev-parse HEAD": (True, "def456abc123\n", ""),
        }
    )
    mock_runner._pip_result = CmdResult(False, "", "No such file: requirements-dev.txt", 1)

    with patch.object(SubprocessRunner, "run", tracking_run):
        result = apply_update(runner=mock_runner)

    assert result["ok"] is False
    assert result["stage"] == "pip"
    assert result["rolled_back"] is True
    assert any("reset" in c and "abc123" in c for c in reset_calls)


# ── marker tests ─────────────────────────────────────────────────────────────

def test_marker_roundtrip(tmp_path):
    marker_path = tmp_path / ".update-marker.json"
    with patch.object(updater_mod(), "_MARKER_PATH", marker_path):
        write_marker("ch123", "msg456", "tobias", "abc123def")
        m = read_marker()

    assert m is not None
    assert m.channel_id == "ch123"
    assert m.message_id == "msg456"
    assert m.requester == "tobias"
    assert m.target_sha == "abc123def"
    assert m.written_at

    with patch.object(updater_mod(), "_MARKER_PATH", marker_path):
        assert read_marker() is not None
        clear_marker()
        assert read_marker() is None


def test_read_marker_missing_returns_none(tmp_path):
    marker_path = tmp_path / "nonexistent.json"
    with patch.object(updater_mod(), "_MARKER_PATH", marker_path):
        assert read_marker() is None


# ── fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_runner():
    return MockRunner()
