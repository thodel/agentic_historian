"""#824 — clean-up plans on UBELIX, readable from a session; deleting stays with a person.

training-atr-models#387 turned the 06.11. clean-up into a plan
(``ubelix/plan_purge.py --write``) and one line to run it
(``ubelix/apply_purge.sh``). Two verbs on the forced command read what lies in
between: what a plan would take (``purge-candidates``, ``--json`` and never
``--write``) and a written plan with the logs of its runs (``plan``). Neither
verb nor tool writes, deletes or submits — the last test runs both and finds
the login node's files exactly as they were.
"""

import hashlib
import json
import os
import re
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
PLAN = "purge-20261106T091233Z"

#: A stand-in for training-atr-models' plan_purge.py: notes its argv outside
#: the login node's tree and answers like the real one.
PLANNER = r'''
import json, os, sys
from pathlib import Path
with open(os.path.join(sys.argv[0] + ".argv"), "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if "--verify" in args:
    path = Path(args[args.index("--verify") + 1])
    plan = json.loads(path.read_text())
    if plan.get("tampered"):
        print("refused: die Einträge passen nicht zu entries_sha256", file=sys.stderr)
        sys.exit(1)
    print("cafe  %d Einträge" % len(plan["entries"]))
    sys.exit(0)
root = Path(args[args.index("--jobs-root") + 1])
if not root.is_dir():
    print("refused: kein Jobs-Root unter %s" % root, file=sys.stderr)
    sys.exit(1)
print(json.dumps({"kind": "atr-purge-plan", "jobs_root": str(root),
                  "entries": [{"id": "20260807T153335Z-kraken-thun-missiven-v1"}],
                  "protected": [{"id": "20260905T190759Z-kraken-german-eval-pool-v1",
                                 "why": ["source_job von german-medieval-v1"]}],
                  "total_bytes": 40000000000}, indent=2))
'''


@pytest.fixture
def login_node(tmp_path):
    node = tmp_path / "node"
    repo = node / "training-atr-models"
    (repo / "ubelix").mkdir(parents=True)
    planner_dir = tmp_path / "outside"
    planner_dir.mkdir()
    # The planner sits in the checkout; its argv log lands outside the node.
    planner = repo / "ubelix" / "plan_purge.py"
    planner.write_text(PLANNER.replace('sys.argv[0] + ".argv"',
                                       repr(str(planner_dir / "argv"))))
    share = node / "share" / "jobs"
    share.mkdir(parents=True)
    plans = node / "plans"
    plans.mkdir()
    plan_file = plans / (PLAN + ".json")
    plan_file.write_text(json.dumps({"kind": "atr-purge-plan", "entries": [{"id": "a"}, {"id": "b"}],
                                     "protected": [], "entries_sha256": "x"}))
    logs = node / "logs"
    logs.mkdir()
    (logs / "purge-21400001.out").write_text(
        f"== 2026-11-06T09:20:00+01:00 Plan {plan_file}\n   sha256 abc\n== df vorher\n"
        "removed  a  40 GB\nskip     b  Status jetzt queued, im Plan failed\n"
        "== 1 gelöscht (40 GB laut Inventar), 1 übersprungen\n")
    (logs / "purge-21400002.out").write_text("== 2026-11-07 Plan /elsewhere/other.json\n")
    (logs / "prep-1.out").write_text(f"Plan {plan_file}\n")

    def run(request):
        env = {"PATH": "/usr/bin:/bin", "USER": "th19c587", "HOME": str(node),
               "ATR_MCP_RO_JOBS": str(node / "scratch-jobs"),
               "ATR_MCP_RO_SHARE_JOBS": str(share), "ATR_MCP_RO_PLANS": str(plans),
               "ATR_MCP_RO_LOGS": str(logs), "ATR_MCP_RO_REPO": str(repo)}
        if request is not None:
            env["SSH_ORIGINAL_COMMAND"] = request
        return subprocess.run([sys.executable, str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=30)

    def argvs():
        path = planner_dir / "argv"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    run.node, run.argvs, run.plan_file, run.share = node, argvs, plan_file, share
    return run


# ── UBELIX: the forced command ───────────────────────────────────────────────

@pytest.mark.parametrize("request_", [
    "purge-candidates", "purge-candidates share", "purge-candidates /storage failed",
    "purge-candidates ../share failed", "purge-candidates share failed,training",
    "purge-candidates share running", "purge-candidates share failed --write",
    "purge-candidates share failed 2026-10-01 --write", "purge-candidates share failed 01.10.2026",
    "purge-candidates share failed;id", "purge-candidates share failed,,cancelled",
    "plan", "plan ../" + PLAN, "plan " + PLAN + ".json", "plan purge-x", "plan a/b",
    "plan ..", "plan " + PLAN + " x", "plan /etc/passwd", "plan purge-20261106T091233Z/..",
    "apply " + PLAN, "write-plan share",
])
def test_anything_else_exits_2_and_runs_nothing(login_node, request_):
    done = login_node(request_)
    assert done.returncode == 2, (request_, done.stdout, done.stderr)
    assert "refused" in done.stderr
    assert login_node.argvs() == []


def test_candidates_run_the_planner_with_json_and_never_write(login_node):
    done = login_node("purge-candidates share failed,cancelled 2026-10-01")
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["root"] == "share"
    assert answer["candidates"]["protected"][0]["why"] == ["source_job von german-medieval-v1"]
    (argv,) = login_node.argvs()
    assert argv == ["--json", "--jobs-root", str(login_node.share), "--status",
                    "failed,cancelled", "--ended-before", "2026-10-01"]
    assert "--write" not in argv


def test_candidates_on_scratch_take_the_runs_jobs_root(login_node):
    done = login_node("purge-candidates scratch completed")
    # The stand-in's scratch root does not exist: the planner refuses, and that is the answer.
    assert done.returncode == 1
    assert "kein Jobs-Root" in json.loads(done.stdout)["error"]
    (argv,) = login_node.argvs()
    assert argv[argv.index("--jobs-root") + 1].endswith("scratch-jobs")


def test_candidates_without_the_planner_say_which_checkout(login_node):
    (login_node.node / "training-atr-models" / "ubelix" / "plan_purge.py").unlink()
    done = login_node("purge-candidates share failed")
    assert done.returncode == 1 and "#387" in json.loads(done.stdout)["error"]


def test_a_plan_comes_with_its_checksum_verification_and_runs(login_node):
    done = login_node("plan " + PLAN)
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["sha256"] == hashlib.sha256(login_node.plan_file.read_bytes()).hexdigest()
    assert answer["plan"]["entries"] == [{"id": "a"}, {"id": "b"}]
    assert answer["verify"] == {"ok": True, "output": "cafe  2 Einträge"}
    (run,) = answer["runs"]       # not the other plan's run, not a prepare log
    assert run["log"].endswith("purge-21400001.out")
    assert "skip     b  Status jetzt queued, im Plan failed" in run["tail"]
    assert login_node.argvs() == [["--verify", str(login_node.plan_file)]]


def test_a_tampered_plan_says_so(login_node):
    raw = json.loads(login_node.plan_file.read_text())
    raw["tampered"] = True
    login_node.plan_file.write_text(json.dumps(raw))
    answer = json.loads(login_node("plan " + PLAN).stdout)
    assert answer["verify"]["ok"] is False and "entries_sha256" in answer["verify"]["output"]


def test_a_missing_plan_is_an_answer(login_node):
    done = login_node("plan purge-20000101T000000Z")
    assert done.returncode == 1
    assert json.loads(done.stdout) == {"name": "purge-20000101T000000Z", "error": "no such plan"}


def _snapshot(root: Path) -> dict:
    out = {}
    for path in sorted(root.rglob("*")):
        stat = path.lstat()
        out[str(path)] = (stat.st_mode, stat.st_size, stat.st_mtime_ns,
                          hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
    return out


def test_no_verb_changes_anything_on_the_login_node(login_node):
    before = _snapshot(login_node.node)
    for request in ("purge-candidates share failed,cancelled",
                    "purge-candidates share completed 2026-10-01",
                    "purge-candidates scratch failed", "plan " + PLAN,
                    "plan purge-20000101T000000Z"):
        login_node(request)
    assert _snapshot(login_node.node) == before
    assert all("--write" not in argv for argv in login_node.argvs())


def test_the_script_mentions_no_writing_flag_of_the_planner():
    """The planner writes only with --write and deletes only with --apply; neither
    word appears in the forced command at all."""
    text = SCRIPT.read_text()
    assert '"--write"' not in text and '"--apply"' not in text
    code = re.sub(r'""".*?"""|#[^\n]*', "", text, flags=re.DOTALL)
    assert "def purge_candidates" in code      # the strip left the code
    assert "apply_purge.sh" not in code


# ── tei: refusal before ssh, one fixed argv ──────────────────────────────────

@pytest.fixture
def configured(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_HOST", "th19c587@submit02.unibe.ch")
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_KEY", str(tmp_path / "ubelix_mcp"))


@pytest.fixture
def calls(monkeypatch):
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(
            {"root": "share", "candidates": {"entries": []}}), stderr="")

    monkeypatch.setattr(ubelix.subprocess, "run", fake_run)
    return seen


@pytest.mark.parametrize("root, statuses, before", [
    ("/storage/research", "failed", None), ("share;id", "failed", None),
    ("share", "training", None), ("share", "failed --write", None),
    ("share", "failed", "1.10.2026"), ("share", "failed", "2026-10-01 --write"),
    ("share", "", None), ("", "failed", None),
])
def test_bad_candidates_never_reach_ssh(configured, calls, root, statuses, before):
    result = ubelix.purge_candidates(root, statuses, before)
    assert result["ok"] is False and calls == []


@pytest.mark.parametrize("name", ["../" + PLAN, "purge-x", "a/b", "", "..",
                                  PLAN + " x", "/etc/passwd", PLAN + ".json/..", "plan"])
def test_bad_plan_names_never_reach_ssh(configured, calls, name):
    result = ubelix.purge_plan(name)
    assert result["ok"] is False and "plan name" in result["error"] and calls == []


def test_each_tool_is_one_fixed_argv_and_comes_back_parsed(configured, calls):
    result = ubelix.purge_candidates("share", "failed, cancelled", "2026-10-01")
    assert result["ok"] is True and result["candidates"] == {"entries": []}
    ubelix.purge_plan(PLAN + ".json")
    tail = ["th19c587@submit02.unibe.ch"]
    assert calls[0][-5:] == tail + ["purge-candidates", "share", "failed,cancelled", "2026-10-01"]
    assert calls[1][-3:] == tail + ["plan", PLAN]
    for argv in calls:
        assert argv[:2] == ["ssh", "-T"] and "IdentitiesOnly=yes" in argv


def test_a_long_plan_is_not_cut_where_a_queue_would_be(configured, monkeypatch):
    big = json.dumps({"name": PLAN, "plan": {"entries": [{"id": "x" * 100}] * 500}})
    assert len(big) > ubelix.MAX_OUTPUT_CHARS
    monkeypatch.setattr(ubelix.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, stdout=big, stderr=""))
    assert len(ubelix.purge_plan(PLAN)["plan"]["entries"]) == 500


def test_both_sides_use_the_same_purge_patterns():
    text = SCRIPT.read_text()
    for name in ("PURGE_ROOT_RE", "STATUSES_RE", "DATE_RE", "PLAN_RE"):
        ours = getattr(ubelix, name).pattern
        theirs = re.search(name + r' = re\.compile\(r"([^"]+)"\)', text).group(1)
        assert ours == theirs, name


def test_the_server_offers_both_tools_and_says_they_only_read():
    source = (PKG / "mcp_atr" / "server.py").read_text()
    for tool in ("ubelix_purge_candidates", "ubelix_purge_plan"):
        assert f"def {tool}(" in source
    assert "none of them can submit, cancel or delete" in source
    assert os.access(SCRIPT, os.X_OK)
