"""
updater.py — UI-agnostic update core for the Agentic Historian bot.

Mirrors the logic of update.sh; all git/pip/python calls go through an injectable
subprocess runner so the module is fully offline-testable.

Public API
----------
fetch_status(runner=None) -> dict
    Run `git fetch`, then report status against origin/main.

apply_update(runner=None) -> dict
    Safe pipeline: abort if dirty/diverged → pull --ff-only → pip install -r requirements-dev.txt
    → smoke-test imports → on any failure roll back to pre_sha and return ok=False.

write_marker(channel_id, message_id, requester, target_sha) -> None
read_marker() -> UpdateMarker | None
clear_marker() -> None
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_MARKER_PATH = _REPO_ROOT / "data" / ".update-marker.json"

# Packages checked by the smoke test.
_SMOKE_COMMAND = (
    "python -c "
    + "import bot,config,orchestrator,ingest,agent_tools,nl_orchestrator,semantic;"
    + "from utils import publish_github,mcp_probe; from knowledge_hub import store; from eval import harness;"
    + "print('smoke-ok')"
)


# ── Subprocess runner protocol ───────────────────────────────────────────────

@dataclass
class CmdResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


class SubprocessRunner:
    """Real subprocess runner — shells out to the system."""

    def run(self, cmd: str | list[str], cwd: Path | None = None, check: bool = False) -> CmdResult:
        if isinstance(cmd, str):
            cmd = cmd.split()
        try:
            r = subprocess.run(cmd, cwd=cwd or _REPO_ROOT,
                               capture_output=True, text=True, timeout=300)
            return CmdResult(r.returncode == 0, r.stdout, r.stderr, r.returncode)
        except subprocess.TimeoutExpired:
            return CmdResult(False, "", "timeout", -1)
        except Exception as e:
            return CmdResult(False, "", str(e), -1)


# ── Git helpers ──────────────────────────────────────────────────────────────

def _git_key(subcmd: str, args: tuple[str, ...]) -> str:
    """Stable command key for runner sequence matching."""
    return subcmd + (" " + " ".join(args) if args else "")


def _run_git(runner: SubprocessRunner, *git_args: str) -> CmdResult:
    return runner.run(["git", "-C", str(_REPO_ROOT), *git_args])


def fetch_status(runner: SubprocessRunner | None = None) -> dict:
    """
    Run `git fetch origin main` and return a status dict:

    {
        "current_sha": str,
        "target_sha":  str | None,
        "ahead":       int,
        "behind":      int,
        "diverged":    bool,
        "dirty":       bool,
        "commits":     list[{"sha": str, "subject": str}],
        "ok":          bool,
        "error":       str | None,
    }
    """
    r = runner or SubprocessRunner()

    # git status for dirty flag
    status_r = _run_git(r, "status", "--porcelain", "-uno")
    dirty = bool(status_r.stdout.strip())

    # current SHA
    head_r = _run_git(r, "rev-parse", "HEAD")
    current_sha = head_r.stdout.strip()[:12] if head_r.ok else ""

    # fetch origin main
    fetch_r = _run_git(r, "fetch", "origin", "main:refs/remotes/origin/main")
    if not fetch_r.ok:
        return dict(current_sha=current_sha, target_sha=None, ahead=0,
                    behind=0, diverged=False, dirty=dirty, commits=[],
                    ok=False, error=f"git fetch failed: {fetch_r.stderr.strip()}")

    # target SHA
    target_r = _run_git(r, "rev-parse", "refs/remotes/origin/main")
    target_sha = target_r.stdout.strip()[:12] if target_r.ok else ""

    # ahead / behind
    revlist_r = _run_git(r, "rev-list", "--left-right", "--count",
                         "HEAD...refs/remotes/origin/main")
    if revlist_r.ok and "\t" in revlist_r.stdout:
        parts = revlist_r.stdout.strip().split("\t")
        behind, ahead = int(parts[0]), int(parts[1])
    else:
        behind = ahead = 0

    diverged = ahead > 0 and behind > 0

    # log of ahead commits
    commits: list[dict] = []
    if ahead > 0:
        log_r = _run_git(r, "log", "--format=%H %s", "-n", str(ahead),
                         "refs/remotes/origin/main..HEAD")
        for line in log_r.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split(" ", 1)
            commits.append({"sha": parts[0][:12], "subject": parts[1] if len(parts) > 1 else ""})

    return dict(
        current_sha=current_sha, target_sha=target_sha,
        ahead=ahead, behind=behind, diverged=diverged,
        dirty=dirty, commits=commits, ok=True, error=None,
    )


def apply_update(runner: SubprocessRunner | None = None) -> dict:
    """
    Safe update pipeline. Returns:

    {ok: True,  from_sha: str, to_sha: str}
    {ok: False, stage: str, error: str, rolled_back: True, pre_sha: str}
    """
    r = runner or SubprocessRunner()
    pre_sha = ""

    # ── pre-flight ────────────────────────────────────────────────────────────
    status = fetch_status(runner=r)
    if not status["ok"]:
        return dict(ok=False, stage="fetch", error=status["error"],
                    rolled_back=False, pre_sha="")

    if status["dirty"]:
        return dict(ok=False, stage="dirty",
                    error="Working tree is dirty — commit or stash first.",
                    rolled_back=False, pre_sha="")

    if status["diverged"]:
        return dict(ok=False, stage="diverged",
                    error="Local and origin/main have diverged — resolve manually.",
                    rolled_back=False, pre_sha="")

    pre_sha = status["current_sha"]

    # ── pull --ff-only ────────────────────────────────────────────────────────
    pull_r = _run_git(r, "pull", "--ff-only")
    if not pull_r.ok:
        reset_r = _run_git(r, "reset", "--hard", "origin/main")
        if not reset_r.ok:
            return dict(ok=False, stage="pull",
                        error=pull_r.stderr.strip() or reset_r.stderr.strip(),
                        rolled_back=True, pre_sha=pre_sha)
        to_sha = status["target_sha"] or ""
    else:
        head_r = _run_git(r, "rev-parse", "HEAD")
        to_sha = head_r.stdout.strip()[:12] if head_r.ok else ""

    # ── pip install -r requirements-dev.txt ───────────────────────────────────
    req_dev = _REPO_ROOT / "requirements-dev.txt"
    pip_r = r.run(["python", "-m", "pip", "install", "-r", str(req_dev)], cwd=_REPO_ROOT)
    if not pip_r.ok:
        _rollback(r, pre_sha)
        return dict(ok=False, stage="pip",
                    error=f"pip install failed: {pip_r.stderr.strip()[:200]}",
                    rolled_back=True, pre_sha=pre_sha)

    # ── smoke test ────────────────────────────────────────────────────────────
    smoke_r = r.run(_SMOKE_COMMAND, cwd=_REPO_ROOT)
    if not smoke_r.ok or "smoke-ok" not in smoke_r.stdout:
        _rollback(r, pre_sha)
        return dict(ok=False, stage="smoke",
                    error=f"smoke test failed: {smoke_r.stderr.strip()[:200]}",
                    rolled_back=True, pre_sha=pre_sha)

    return dict(ok=True, from_sha=pre_sha, to_sha=to_sha)


def _rollback(runner: SubprocessRunner, sha: str) -> None:
    """Hard-reset to sha (best-effort, synchronous)."""
    _run_git(runner, "reset", "--hard", sha)


# ── Marker file ──────────────────────────────────────────────────────────────

@dataclass
class UpdateMarker:
    channel_id: str
    message_id: str
    requester: str
    target_sha: str
    written_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


def write_marker(channel_id: str, message_id: str, requester: str, target_sha: str) -> None:
    _MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    marker = UpdateMarker(channel_id=channel_id, message_id=message_id,
                          requester=requester, target_sha=target_sha)
    with open(_MARKER_PATH, "w") as fh:
        json.dump(marker.__dict__, fh)


def read_marker() -> UpdateMarker | None:
    if not _MARKER_PATH.exists():
        return None
    try:
        return UpdateMarker(**json.loads(_MARKER_PATH.read_text()))
    except Exception:
        return None


def clear_marker() -> None:
    if _MARKER_PATH.exists():
        _MARKER_PATH.unlink()
