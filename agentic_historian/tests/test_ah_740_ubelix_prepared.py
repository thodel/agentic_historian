"""#740 — UBELIX, read-only: what a prepare left for stage 2, and the trainer checkout.

26.10.: the trainer id stage 2 needs was read by hand from
`runs/prep-<slurm>.jobid`, the train line from the end of the prepare's log,
and the preflight refused twice on a checkout behind origin/main that nothing
on tei could see. Two verbs on the forced command, both read-only.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from mcp_atr import ubelix                       # noqa: E402

SCRIPT = PKG.parent / "deploy" / "ubelix-ro" / "atr-mcp-ro"
TRAINER_ID = "20261026T204200Z-qwen3.5-4b-german-kurrent-xvi-xix-blb-page-v1"


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@x", "PATH": "/usr/bin:/bin", "HOME": str(repo)})


@pytest.fixture
def login_node(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sacct").write_text(
        '#!/bin/sh\necho "sacct $*" >> "%s"\n'
        'echo "prep|COMPLETED|2026-10-26T21:41:48|2026-10-26T23:42:06|0:0"\n' % (tmp_path / "called"))
    (bin_dir / "sacct").chmod(0o755)
    runs, logs = tmp_path / "runs", tmp_path / "logs"
    (runs / "jobs" / TRAINER_ID).mkdir(parents=True)
    logs.mkdir()
    (runs / "prep-20143697.jobid").write_text(TRAINER_ID + "\n")
    (runs / "jobs" / TRAINER_ID / "job.json").write_text(json.dumps(
        {"id": TRAINER_ID, "status": "training", "stage": "train", "updated_at": "2026-10-26T22:42:00Z",
         "error": None, "request": {"not": "handed out"}}))
    (logs / "prep-20143697.out").write_text(
        "== job %s\n... artefact cache: stored a13c1a924f9a (63.4 GB in 2 min)\n\n"
        "== corpus ready. Stage 2:\n   JOB_ID=%s ubelix/submit.sh ubelix/train.sbatch -- "
        "--partition=gpu --qos=job_gratis --cpus-per-task=12 --time=15:00:00\n" % (TRAINER_ID, TRAINER_ID))
    # the trainer checkout and its origin
    origin, repo = tmp_path / "origin.git", tmp_path / "training-atr-models"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a").write_text("1")
    _git(repo, "add", "a"); _git(repo, "commit", "-q", "-m", "one")
    _git(repo, "remote", "add", "origin", str(origin)); _git(repo, "push", "-q", "origin", "main")

    def run(request):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "USER": "th19c587", "HOME": str(tmp_path),
               "ATR_MCP_RO_JOBS": str(runs / "jobs"), "ATR_MCP_RO_RUNS": str(runs),
               "ATR_MCP_RO_LOGS": str(logs), "ATR_MCP_RO_REPO": str(repo),
               "SSH_ORIGINAL_COMMAND": request}
        done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                              text=True, timeout=60)
        return done

    run.repo, run.origin, run.runs, run.logs = repo, origin, runs, logs
    return run


# ── prepared ─────────────────────────────────────────────────────────────────

def test_prepared_hands_out_the_trainer_id_and_the_next_line(login_node):
    done = login_node("prepared 20143697")
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["trainer_id"] == TRAINER_ID
    assert out["slurm"]["state"] == "COMPLETED"
    assert out["record"] == {"status": "training", "stage": "train",
                             "updated_at": "2026-10-26T22:42:00Z", "error": None}
    assert out["next"].startswith(f"JOB_ID={TRAINER_ID} ubelix/submit.sh ubelix/train.sbatch")
    assert "a13c1a924f9a" in out["artefact"]
    assert "request" not in done.stdout


def test_a_prepare_that_has_not_started_has_no_id_and_says_nothing_false(login_node):
    out = json.loads(login_node("prepared 99999999").stdout)
    assert out["trainer_id"] is None and out["next"] is None and out["record"] is None


def test_an_id_file_with_something_else_in_it_is_not_handed_out(login_node):
    (login_node.runs / "prep-1.jobid").write_text("../../etc/passwd\n")
    out = json.loads(login_node("prepared 1").stdout)
    assert out["trainer_id"] is None and "not a trainer id" in out["error"]


@pytest.mark.parametrize("request_", ["prepared", "prepared x", "prepared 1 2", "prepared ../1",
                                      "prepared 1;id", "checkout now", "checkout ../x"])
def test_anything_else_is_refused(login_node, request_):
    done = login_node(request_)
    assert done.returncode == 2 and "refused" in done.stderr


# ── checkout ─────────────────────────────────────────────────────────────────

def test_an_up_to_date_checkout_says_so(login_node):
    out = json.loads(login_node("checkout").stdout)
    assert out["behind"] == 0 and out["uncommitted"] == 0 and out["up_to_date"] is True
    assert out["head"] == out["origin_main"] and out["subject"].endswith("one")


def test_a_checkout_behind_origin_is_counted_when_fetched_and_named_when_not(login_node, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(login_node.origin), str(other)], check=True)
    (other / "b").write_text("2")
    _git(other, "add", "b"); _git(other, "commit", "-q", "-m", "two"); _git(other, "push", "-q", "origin", "main")
    out = json.loads(login_node("checkout").stdout)
    assert out["behind"] is None and "not fetched" in out["note"] and out["up_to_date"] is False
    _git(login_node.repo, "fetch", "-q", "origin")
    out = json.loads(login_node("checkout").stdout)
    assert out["behind"] == 1


def test_uncommitted_files_are_counted(login_node):
    (login_node.repo / "a").write_text("changed")
    out = json.loads(login_node("checkout").stdout)
    assert out["uncommitted"] == 1 and out["up_to_date"] is False


def test_checkout_fetches_nothing(login_node, tmp_path):
    other = tmp_path / "other2"
    subprocess.run(["git", "clone", "-q", str(login_node.origin), str(other)], check=True)
    (other / "c").write_text("3")
    _git(other, "add", "c"); _git(other, "commit", "-q", "-m", "three"); _git(other, "push", "-q", "origin", "main")
    before = subprocess.run(["git", "-C", str(login_node.repo), "rev-parse", "refs/remotes/origin/main"],
                            capture_output=True, text=True).stdout
    login_node("checkout")
    after = subprocess.run(["git", "-C", str(login_node.repo), "rev-parse", "refs/remotes/origin/main"],
                           capture_output=True, text=True).stdout
    assert before == after, "ls-remote, not fetch"


# ── tei ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_HOST", "th19c587@submit02.unibe.ch")
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_KEY", str(tmp_path / "k"))


def test_tei_sends_one_fixed_request_and_parses_the_answer(configured, monkeypatch):
    seen = []

    def fake_run(argv, **kw):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
            {"slurm_id": "20143697", "trainer_id": TRAINER_ID, "next": "JOB_ID=…"}), stderr="")

    monkeypatch.setattr(ubelix.subprocess, "run", fake_run)
    out = ubelix.prepared("20143697")
    assert seen[0][-2:] == ["prepared", "20143697"] and out["trainer_id"] == TRAINER_ID
    ubelix.checkout()
    assert seen[1][-1] == "checkout"


@pytest.mark.parametrize("bad", ["1; id", "$(id)", "", "../1", "-o x"])
def test_a_bad_slurm_id_never_reaches_ssh(configured, monkeypatch, bad):
    monkeypatch.setattr(ubelix.subprocess, "run", lambda *a, **k: pytest.fail("ssh was called"))
    assert ubelix.prepared(bad)["ok"] is False
