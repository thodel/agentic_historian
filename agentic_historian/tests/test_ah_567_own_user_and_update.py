"""#567, the two decisions of 17.10.2026 — and the name that is not `dh-bot`.

**`/update` reports and no longer pulls.** Pulling means writing the code this
process runs, and the confinement S2 asks for cannot allow that. Not through
`.git` either: it looks like the narrow option and is not, because
`.git/hooks/*` runs as the service user on the next git operation. So the read
side writes *nothing* — `git ls-remote`, not `git fetch` — and the price is
stated in the answer instead of hidden: without the objects locally, "behind"
and "diverged" cannot be told apart.

**The bot's own user is `agentic_historian`.** `dh-bot` is the *OpenClaw
agent's* Discord identity (`SEPARATION.md` S1, user id 1519706253314756758).
Naming the bot's own user, or its secrets file, after the thing it is being
separated from is how the two came to be conflated in the first place — so the
tests below pin which name belongs to which, in both directions.

Offline: the runner is injected, so no git command runs; the deploy files are
read as text.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[2]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import updater  # noqa: E402

DEPLOY = REPO / "deploy" / "systemd"
DROPINS = DEPLOY / "agentic-historian.service.d"
ENV_EXAMPLE = DEPLOY / "agentic_historian.env.example"
HARDENING = (DROPINS / "hardening.conf").read_text(encoding="utf-8")
USER_CONF = (DROPINS / "user.conf").read_text(encoding="utf-8")
SWITCH = (DEPLOY / "switch_to_own_user.sh").read_text(encoding="utf-8")

HEAD = "1234567890abcdef1234567890abcdef12345678"
REMOTE = "fedcba0987654321fedcba0987654321fedcba09"


class FakeRunner:
    """Records every git invocation and answers from a table."""

    def __init__(self, answers: dict[tuple[str, ...], tuple[bool, str]]):
        self.answers = answers
        self.calls: list[list[str]] = []

    def run(self, cmd, cwd=None):
        self.calls.append(list(cmd))
        # `_run_git` builds ["git", "-C", <repo>, *args]; the table is keyed on
        # the args alone.
        key = tuple(cmd[3:]) if cmd[:2] == ["git", "-C"] else tuple(cmd)
        ok, out = self.answers.get(key, (False, ""))
        return updater.CmdResult(ok=ok, stdout=out, returncode=0 if ok else 1,
                                 stderr="" if ok else "no such thing")

    @property
    def git_verbs(self) -> list[str]:
        """The git subcommand of each call — `rev-parse`, `ls-remote`, …"""
        verbs = []
        for cmd in self.calls:
            args = [a for a in (cmd[3:] if cmd[:2] == ["git", "-C"] else cmd[1:])
                    if not a.startswith("-")]
            if args:
                verbs.append(args[0])
        return verbs


def _runner(*, remote=REMOTE, head=HEAD, remote_ok=True):
    return FakeRunner({
        ("--no-optional-locks", "rev-parse", "HEAD"): (True, head + "\n"),
        ("ls-remote", "origin", updater.MAIN_REF):
            (remote_ok, f"{remote}\t{updater.MAIN_REF}\n" if remote_ok else ""),
    })


# ── the read side writes nothing ────────────────────────────────────────────

def test_the_status_check_never_fetches():
    """`git fetch` writes a ref under `.git`, and `.git` is not writable — nor
    should it be, because `.git/hooks/*` runs as the service user."""
    runner = _runner()

    updater.remote_status(runner=runner)

    assert "fetch" not in runner.git_verbs
    assert "pull" not in runner.git_verbs
    assert set(runner.git_verbs) == {"rev-parse", "ls-remote"}


def test_the_status_check_does_not_let_git_status_touch_the_index():
    """`git status` refreshes `.git/index` unless told not to, and a refresh is
    a write. The flag is the whole reason this is not a plain `rev-parse`."""
    runner = _runner()

    updater.remote_status(runner=runner)

    revparse = next(cmd for cmd in runner.calls if "rev-parse" in cmd)
    assert "--no-optional-locks" in revparse


def test_it_names_both_commits():
    status = updater.remote_status(runner=_runner())

    assert status["ok"] is True
    assert status["current_sha"] == HEAD[:12]
    assert status["target_sha"] == REMOTE[:12]
    assert status["writes_nothing"] is True


def test_the_same_commit_is_reported_as_the_same():
    status = updater.remote_status(runner=_runner(remote=HEAD))

    assert status["relation"] == "same"


def test_a_difference_is_not_called_behind():
    """The honest limit. Without the remote objects locally, a different hash
    could be ahead, behind or diverged, and picking one would be a guess printed
    as a fact."""
    status = updater.remote_status(runner=_runner())

    assert status["relation"] == "unknown"
    assert status["relation"] not in ("behind", "ahead", "diverged")


def test_a_full_ref_is_asked_for_not_a_branch_name():
    """`main` also matches a tag called `main`, and a tag is not what is
    deployed."""
    assert updater.MAIN_REF == "refs/heads/main"
    runner = _runner()
    updater.remote_status(runner=runner)
    assert any(updater.MAIN_REF in cmd for cmd in runner.calls)


def test_an_unreachable_remote_is_an_error_not_an_up_to_date():
    status = updater.remote_status(runner=_runner(remote_ok=False))

    assert status["ok"] is False
    assert status["relation"] == "unknown"
    assert status["target_sha"] is None


def _function_source(path: Path, name: str) -> str:
    """One function's source, by its name, from the AST.

    The first version of the check below sliced the file between `name="update"`
    and the next `@bot.slash_command` — and ran past the end of the command into
    the docstring of `_McpProposeView`, which mentions `_ConfirmView` while
    explaining something else. A question about one function is asked of that
    function; sixth time this shape has cost a wrong answer here.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"no function {name} in {path.name}")


def test_the_bot_command_no_longer_reaches_the_pulling_half():
    """`apply_update` still exists for whoever already owns the checkout. What
    must not exist is a path from Discord to it."""
    command = _function_source(PKG / "bot.py", "update_cmd")

    assert "remote_status" in command
    assert "apply_update" not in command
    assert "fetch_status" not in command
    assert "_ConfirmView" not in command, "the confirm button applied the update"


def test_apply_update_says_why_the_bot_no_longer_calls_it():
    """So the next reader does not wire it back as a convenience."""
    import inspect

    doc_and_body = inspect.getsource(updater.apply_update)
    assert "#567" in doc_and_body
    assert "no longer calls this" in doc_and_body


# ── the sandbox matches the decision ────────────────────────────────────────

def test_the_unit_grants_only_the_data_directories():
    granted = re.findall(r"^ReadWritePaths=(.+)$", HARDENING, re.M)

    assert granted, "no writable path at all — the bot cannot work"
    assert all(path.rstrip("/").endswith("data") for path in granted), (
        f"a path outside the data directories is writable again: {granted}")


def test_neither_the_checkout_nor_the_venv_is_writable():
    """The two that `/update` used to need. Granting them back is granting the
    process permission to rewrite its own code."""
    granted = re.findall(r"^ReadWritePaths=(.+)$", HARDENING, re.M)

    assert "/home/dh/agentic_historian" not in granted
    assert not any(path.endswith(".venv") or "/.venv" in path for path in granted)


def test_the_git_directory_is_not_granted_either():
    """It looks like the narrow option: `git fetch` would work and `git pull`
    would still fail. But `.git/hooks/*` runs as the service user on the next
    git operation, so write there is code execution there."""
    granted = re.findall(r"^ReadWritePaths=(.+)$", HARDENING, re.M)

    assert not any(".git" in path for path in granted)
    assert "hooks" in HARDENING, "the reason is not written down"


# ── the name: ours is agentic_historian, dh-bot is theirs ───────────────────

def test_the_dedicated_user_is_named_after_the_bot():
    assert re.search(r"^User=agentic_historian$", USER_CONF, re.M)
    assert re.search(r"^Group=agentic_historian$", USER_CONF, re.M)


def test_the_dedicated_user_is_not_named_after_the_openclaw_agent():
    """`dh-bot` is the OpenClaw agent's Discord identity. A user of that name
    would be named after the thing it is being separated from."""
    for name, text in (("user.conf", USER_CONF), ("switch_to_own_user.sh", SWITCH)):
        assert not re.search(r"^\s*(User|Group)=dh-bot", text, re.M), name
        assert not re.search(r"BOT_USER=dh-bot", text), name
        assert "dh-bot" in text, (
            f"{name} does not say which name it is avoiding, so the next reader "
            f"cannot know it was a decision")


def test_the_secrets_file_is_named_after_the_bot():
    assert ENV_EXAMPLE.exists(), "the example file was renamed without its new name"
    assert not (DEPLOY / "dh-bot.env.example").exists()
    body = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "/etc/agentic_historian.env" in body
    assert "dh-bot" in body, "the old path on tei is not mentioned at all"


def test_the_drop_in_points_at_the_new_env_file():
    secrets = (DROPINS / "secrets.conf").read_text(encoding="utf-8")

    assert "EnvironmentFile=/etc/agentic_historian.env" in secrets
    assert "AH_ENV_FILE=/etc/agentic_historian.env" in secrets


@pytest.mark.parametrize("path", sorted(DEPLOY.rglob("*")))
def test_no_deploy_file_still_installs_the_old_path(path):
    """A left-over `install … /etc/dh-bot.env` would create the old file beside
    the new one, and then two files hold the bot's secrets."""
    if not path.is_file() or path.suffix in (".pyc",):
        pytest.skip("not a text deploy file")
    body = path.read_text(encoding="utf-8", errors="replace")

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        assert "/etc/dh-bot.env" not in stripped, (
            f"{path.name} still installs or reads the old path: {stripped}")


# ── the switch script measures instead of claiming ──────────────────────────

def test_the_switch_script_reads_the_paths_from_the_configuration():
    """Typing the data directories into the script is the drift that put a
    non-existent `/srv` path into the unit in the first place."""
    assert "writable_paths.py" in SWITCH
    assert "--data-only" in SWITCH


def test_the_switch_script_refuses_without_the_paths():
    """An empty list would `chown` nothing and report success."""
    assert "could not read the data directories" in SWITCH
    assert "exit 3" in SWITCH


def _invokes(script: str, program: str) -> bool:
    """Does *script* execute *program*, rather than print its name?

    Two wrong answers came before this. Searching the whole script passed with
    the invocation deleted, because the name was still in a comment; dropping
    comment lines passed too, because the dry-run branch *says* `would run:
    …check_separation.py`, and a `say` is active code. So the question is asked
    of the command position: the first token of a line, which is what a shell
    executes.
    """
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = [t.strip('"\'') for t in stripped.split()]
        if tokens[0].endswith(program):
            return True
        # `"$VENV_PY" …/check_separation.py`: an interpreter in command position
        # and the program as its first argument is still an invocation.
        if len(tokens) > 1 and ("python" in tokens[0].lower() or tokens[0].endswith("_PY")) \
                and tokens[1].endswith(program):
            return True
    return False


def test_the_switch_script_verifies_with_the_checker():
    """It must *run* the checker, not mention it. The whole reason this script
    exists is that #567 was closed on intentions."""
    assert _invokes(SWITCH, "check_separation.py"), (
        "the script no longer runs the checker — only talks about it")
    assert "--openclaw-secrets" in SWITCH, (
        "it does not tell the operator how to answer the Definition of Done")


def test_the_switch_script_has_a_dry_run_and_needs_root_otherwise():
    assert "--dry-run" in SWITCH
    assert "needs root" in SWITCH


def test_the_switch_script_does_not_move_secrets_or_touch_openclaw():
    """Two things a script that prints what it does must not do."""
    assert "it does not create or fill the env file" in SWITCH
    assert "does not touch the OpenClaw agent" in SWITCH
    assert "DISCORD_BOT_TOKEN" not in SWITCH, "a secret name in a deploy script"


def test_the_data_only_mode_prints_bare_paths():
    """What the script parses. A commented report would break on the next
    comment, which is why this mode exists."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "writable_paths", DEPLOY / "writable_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    import io
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        module.main(["--data-only"])
    lines = [line for line in out.getvalue().splitlines() if line.strip()]

    assert lines, "nothing printed"
    assert all(line.startswith("/") for line in lines), lines
    assert not any(line.startswith("#") for line in lines), lines


# ── the switch script, run: it stops before a restart that would fail ───────
#
# The checks above read the script as text. These run it — `--dry-run`, in a
# copy whose `agentic_historian/` is a link to this checkout, so
# `writable_paths.py` reads the real configuration — because the first version
# of the script warned that the env file was missing and then restarted the
# unit anyway: as a user that cannot read `.env.gpustack` (0600 dh:dh), the bot
# would have come up with no token at all. A warning is not a stop.


def _switch_copy(tmp_path: Path, *, hardening: str | None = None) -> Path:
    """A repo-shaped copy with a hardening.conf that grants what config writes."""
    root = tmp_path / "repo"
    (root / "deploy").mkdir(parents=True)
    subprocess.run(["cp", "-r", str(DEPLOY), str(root / "deploy" / "systemd")], check=True)
    os.symlink(DEPLOY.parents[1] / "agentic_historian", root / "agentic_historian")
    if hardening is None:
        data = subprocess.run(
            [sys.executable, str(root / "deploy" / "systemd" / "writable_paths.py"), "--data-only"],
            capture_output=True, text=True, check=True).stdout.split()
        hardening = "".join(f"ReadWritePaths={p}\n" for p in data)
    (root / "deploy" / "systemd" / "agentic-historian.service.d" / "hardening.conf").write_text(
        hardening, encoding="utf-8")
    return root


def _dry_run(root: Path, env_file: Path, **env: str) -> subprocess.CompletedProcess:
    """Run the dry run. Unless a test brings its own PATH, it gets one with a
    `setfacl` on it (see `_with_a_setfacl`): pytest's tmp_path is 0700, so
    every one of these runs reaches the traverse step."""
    env.setdefault("PATH", _with_a_setfacl(root.parent))
    return subprocess.run(
        ["bash", str(root / "deploy" / "systemd" / "switch_to_own_user.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=120, check=False,
        env={**os.environ, "AH_ENV_FILE": str(env_file), **env})


def _with_a_setfacl(tmp_path: Path) -> str:
    """A PATH on which `setfacl` exists, whatever the host has.

    The dry run now stops where the real run would (exit 5) when the tool is
    missing — tei had no `acl` package on 22.10.2026 — so a test about the
    traverse logic must not depend on the host's packages.
    """
    bin_dir = tmp_path / "with-setfacl"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "setfacl"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    return f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"


def test_without_the_secrets_file_the_script_stops_before_the_restart(tmp_path):
    done = _dry_run(_switch_copy(tmp_path), tmp_path / "absent.env")

    assert done.returncode == 4, done.stderr
    assert "would stop here (exit 4)" in done.stderr
    assert "systemctl restart" not in done.stdout, "it restarted a bot with no secrets"
    # On a host where the user already exists (tei since 23.10.2026) the script
    # says so instead of running useradd; either way item 1 came before item 3.
    assert "useradd" in done.stdout or "exists — left as it is" in done.stdout, (
        "the user is created first; the env file is item 3")


def test_with_the_secrets_file_it_installs_all_three_drop_ins_then_restarts(tmp_path):
    env_file = tmp_path / "present.env"
    env_file.write_text("", encoding="utf-8")

    done = _dry_run(_switch_copy(tmp_path), env_file)

    assert done.returncode == 0, done.stderr
    out = done.stdout
    for conf in ("secrets.conf", "hardening.conf", "user.conf"):
        assert "would run: install -m 644 -D" in out and f"service.d/{conf}" in out, conf
    assert out.index("secrets.conf") < out.index("systemctl daemon-reload") < out.index("systemctl restart")
    assert f"chmod 640 {env_file}" in out and "chown root:agentic_historian" in out


def test_a_directory_the_new_user_cannot_pass_through_is_found_and_granted(tmp_path):
    """pytest's tmp_path is 0700: a user who is not its owner cannot traverse
    it — exactly what a stock 0750 /home/dh does to `agentic_historian`."""
    env_file = tmp_path / "present.env"
    env_file.write_text("", encoding="utf-8")
    os.chmod(tmp_path, 0o700)

    done = _dry_run(_switch_copy(tmp_path), env_file)

    assert done.returncode == 0, done.stderr
    assert f"{tmp_path} — agentic_historian cannot pass through" in done.stdout
    assert f"would run: setfacl -m u:agentic_historian:--x {tmp_path}" in done.stdout
    assert "o+rx" not in done.stdout.replace("chmod o+x", ""), "traverse for one user, not for all"


def test_a_data_directory_missing_from_hardening_conf_stops_everything(tmp_path):
    """With ProtectSystem=strict a directory not in ReadWritePaths is a write
    that fails at the first job (#643), so the script refuses before it
    creates anything."""
    env_file = tmp_path / "present.env"
    env_file.write_text("", encoding="utf-8")

    done = _dry_run(_switch_copy(tmp_path, hardening="ReadWritePaths=/nowhere\n"), env_file)

    assert done.returncode == 6, done.stderr
    assert "NOT in hardening.conf" in done.stdout
    assert "useradd" not in done.stdout and "systemctl" not in done.stdout


def test_a_missing_setfacl_stops_the_dry_run_too(tmp_path):
    """The dry run has to be honest about a tool the real run will stop on.

    The first version guarded the `setfacl` check with `[ "$DRY" = 0 ]`, so a
    dry run on a host without the `acl` package printed "would run: setfacl …"
    and exit 0, and the real run stopped with exit 5 two steps later. tei had
    no `acl` on 22.10.2026, and its dry run said nothing.
    """
    env_file = tmp_path / "present.env"
    env_file.write_text("", encoding="utf-8")
    os.chmod(tmp_path, 0o700)  # so the traverse step has something to grant
    # A PATH with everything the dry run executes and no setfacl.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "stat", "dirname", "grep", "id", "cat", "sh"):
        real = shutil.which(tool)
        assert real, f"precondition: {tool} on PATH"
        os.symlink(real, bin_dir / tool)
    os.symlink(sys.executable, bin_dir / "python3")
    root = _switch_copy(tmp_path)

    done = subprocess.run(
        ["bash", str(root / "deploy" / "systemd" / "switch_to_own_user.sh"), "--dry-run"],
        capture_output=True, text=True, timeout=120, check=False,
        env={**os.environ, "AH_ENV_FILE": str(env_file), "PATH": str(bin_dir)})

    assert done.returncode == 5, (done.stdout, done.stderr)
    assert "would stop here (exit 5)" in done.stderr
    assert "setfacl is not installed" in done.stderr
    assert "systemctl restart" not in done.stdout


@pytest.mark.parametrize("script", sorted(p for p in DEPLOY.iterdir()
                                          if p.suffix in (".py", ".sh")))
def test_every_deploy_script_with_a_shebang_is_executable(script):
    """`sudo deploy/systemd/check_separation.py` answered "command not found"
    on tei (23.10.2026): the file carried a shebang and mode 0644, and the
    switch script's own call to it died silently into `|| true`."""
    first = script.read_text(encoding="utf-8").splitlines()[0]
    if not first.startswith("#!"):
        pytest.skip("no shebang, not meant to be run directly")
    assert os.access(script, os.X_OK), f"{script.name} has a shebang but is not executable"


def test_the_switch_script_runs_the_checker_through_the_venv_python():
    """So the measurement does not depend on a file mode git may not carry."""
    line = next(l for l in SWITCH.splitlines()
                if "check_separation.py" in l and "--secrets" in l and not l.strip().startswith(("#", "say")))
    assert line.strip().startswith('"$VENV_PY"'), line
