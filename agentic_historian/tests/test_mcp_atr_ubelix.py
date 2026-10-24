"""The UBELIX tools (#674): what each side refuses, and what the script runs.

Two halves, matching the two machines:

* **tei** (`mcp_atr/ubelix.py`) — a refused request never reaches `subprocess`,
  and an accepted one becomes a fixed ssh argv with this key and nothing else.
* **UBELIX** (`deploy/ubelix-ro/atr-mcp-ro`) — run here as sshd would run it, with
  the request in ``SSH_ORIGINAL_COMMAND`` and ``squeue``/``sacct`` replaced by
  stubs that print their argv. Anything outside the three verbs exits 2 having
  run nothing.

Offline. No ssh, no Slurm. Run from the repo root::

    pytest agentic_historian/tests/test_mcp_atr_ubelix.py
"""

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
from mcp_atr import jobs, ubelix                 # noqa: E402

SCRIPT = PKG.parent / "deploy" / "ubelix-ro" / "atr-mcp-ro"
RECORD = "20261019T180118Z-olmocr2-7b-medieval-page-px4m-native-r16"

HOSTILE = [
    "1; rm -rf ~", "$(id)", "`id`", "1 && id", "1|id", "../../etc/passwd",
    "1\nid", "-o ProxyCommand=id", " ", "",
]


@pytest.fixture
def configured(monkeypatch, tmp_path):
    key = tmp_path / "ubelix_mcp"
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_HOST", "th19c587@submit02.unibe.ch")
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_KEY", str(key))
    return key


@pytest.fixture
def calls(monkeypatch):
    """Record every subprocess.run and answer like a script that succeeded."""
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(ubelix.subprocess, "run", fake_run)
    return seen


# ── tei: refusal before ssh ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", HOSTILE + ["18929698x", "1" * 13, "-1"])
def test_a_slurm_id_that_is_not_digits_never_reaches_ssh(configured, calls, bad):
    result = ubelix.job(bad)
    assert result["ok"] is False and "Slurm job id" in result["error"]
    assert calls == []


@pytest.mark.parametrize("bad", HOSTILE + [
    "olmocr2-7b-medieval", "20261019T180118Z-", "20261019T180118Z-A",
    "20261019T180118Z-x/../../y", RECORD + " 40",
])
def test_a_record_id_that_is_not_one_never_reaches_ssh(configured, calls, bad):
    result = ubelix.record(bad)
    assert result["ok"] is False and "record id" in result["error"]
    assert calls == []


def test_lines_that_are_not_a_number_never_reach_ssh(configured, calls):
    assert ubelix.record(RECORD, "40; id")["ok"] is False
    assert calls == []


def test_without_configuration_the_tools_say_so_and_do_nothing(monkeypatch, calls):
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_HOST", "")
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_KEY", "")
    for result in (ubelix.queue(), ubelix.job("1"), ubelix.record(RECORD)):
        assert result["ok"] is False and "not configured" in result["error"]
    assert calls == []


@pytest.mark.parametrize("host", ["-oProxyCommand=id", "a b", "x;id", "a@b@c"])
def test_a_host_ssh_would_read_as_an_option_is_refused(monkeypatch, configured, calls, host):
    monkeypatch.setattr(config, "ATR_UBELIX_SSH_HOST", host)
    assert "not an ssh destination" in ubelix.queue()["error"]
    assert calls == []


# ── tei: what is sent ────────────────────────────────────────────────────────

def test_each_tool_is_one_fixed_ssh_argv_with_this_key_only(configured, calls):
    ubelix.queue()
    ubelix.job("18929698")
    ubelix.record(RECORD, 80)
    head = ["ssh", "-T", "-i", str(configured), "-o", "BatchMode=yes",
            "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10",
            "th19c587@submit02.unibe.ch"]
    assert calls == [head + ["queue"], head + ["job", "18929698"],
                     head + ["record", RECORD, "80"]]


def test_lines_are_clamped_not_passed_through(configured, calls):
    ubelix.record(RECORD, 10_000)
    ubelix.record(RECORD, -5)
    assert [c[-1] for c in calls] == ["200", "1"]


def test_the_ssh_timeout_stays_inside_the_brokers_patience():
    assert ubelix.SSH_TIMEOUT_S < jobs.BROKER_TIMEOUT_S


def test_a_refusal_on_ubelix_is_named_as_one(configured, monkeypatch):
    monkeypatch.setattr(ubelix.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 2, stdout="", stderr="refused: x"))
    result = ubelix.queue()
    assert result["ok"] is False and "refused by atr-mcp-ro" in result["error"]


def test_an_ssh_failure_is_not_mistaken_for_an_answer(configured, monkeypatch):
    monkeypatch.setattr(ubelix.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 255, stdout="",
                                                    stderr="Permission denied (publickey)."))
    result = ubelix.job("1")
    assert result["ok"] is False and "ssh failed" in result["error"]
    assert "publickey" in result["stderr"]


def test_a_hanging_login_node_is_an_error_not_a_timeout(configured, monkeypatch):
    def hang(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    monkeypatch.setattr(ubelix.subprocess, "run", hang)
    assert "timed out" in ubelix.queue()["error"]


def test_a_record_comes_back_parsed(configured, monkeypatch):
    payload = {"id": RECORD, "status": "training", "stage": "train", "log": None}
    monkeypatch.setattr(ubelix.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload),
                                                    stderr=""))
    result = ubelix.record(RECORD)
    assert result["ok"] is True and result["record"] == payload
    assert "output" not in result


def test_a_long_answer_is_cut_and_says_so(configured, monkeypatch):
    monkeypatch.setattr(ubelix.subprocess, "run", lambda argv, **kw:
                        subprocess.CompletedProcess(argv, 0, stdout="x" * 50_000, stderr=""))
    out = ubelix.queue()["output"]
    assert len(out) < 21_000 and out.endswith(f"cut at {ubelix.MAX_OUTPUT_CHARS} characters")


def test_both_sides_use_the_same_patterns():
    """Two copies of a pattern that drift are how a refusal becomes a hole."""
    text = SCRIPT.read_text()
    for name in ("SLURM_ID_RE", "RECORD_RE"):
        ours = getattr(ubelix, name).pattern
        theirs = re.search(name + r' = re\.compile\(r"([^"]+)"\)', text).group(1)
        assert ours == theirs, name


# ── UBELIX: the forced command ───────────────────────────────────────────────

@pytest.fixture
def ubelix_host(tmp_path):
    """A login node in a tmp dir: stub squeue/sacct that log their argv, and a
    jobs root with one record."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    called = tmp_path / "called"
    for tool in ("squeue", "sacct"):
        stub = bin_dir / tool
        stub.write_text(f'#!/bin/sh\necho "{tool} $*" >> "{called}"\necho "{tool} ran"\n')
        stub.chmod(0o755)
    root = tmp_path / "jobs"
    rec = root / RECORD
    (rec / "logs").mkdir(parents=True)
    (rec / "job.json").write_text(json.dumps({
        "id": RECORD, "status": "training", "stage": "train", "host": "ubelix",
        "updated_at": "2026-10-20T08:58:07Z", "request": {"secret": "not handed out"},
        "metrics": {"cer": 0.2659, "wer": 0.61, "samples": 120},
    }))
    (rec / "logs" / "train.log").write_text("".join(f"step {i}\n" for i in range(500)))

    def run(request):
        env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "USER": "th19c587",
               "ATR_MCP_RO_JOBS": str(root)}
        if request is not None:
            env["SSH_ORIGINAL_COMMAND"] = request
        done = subprocess.run([sys.executable, str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=30)
        ran = called.read_text().splitlines() if called.exists() else []
        return done, ran

    run.root = root
    return run


@pytest.mark.parametrize("request_", [
    None, "", "bash", "scancel 18929698", "sbatch x.sbatch", "queue now",
    "job", "job 1 2", "job 1;id", "job $(id)", "job ../1",
    "record", "record ../../etc", "record " + RECORD + " 40;id",
    "record " + RECORD + " 1 2", "record " + RECORD + " -1",
    "QUEUE", "queue; id", "rm -rf ~",
])
def test_anything_but_the_three_requests_exits_2_and_runs_nothing(ubelix_host, request_):
    done, ran = ubelix_host(request_)
    assert done.returncode == 2, (request_, done.stdout, done.stderr)
    assert "refused" in done.stderr
    assert ran == []


def test_queue_runs_squeue_for_this_user_only(ubelix_host):
    done, ran = ubelix_host("queue")
    assert done.returncode == 0
    assert len(ran) == 2 and all(r.startswith("squeue -u th19c587 ") for r in ran)
    assert "--start" in ran[1]
    # Through ssh stdout is a pipe, so Python buffers its own lines while
    # squeue writes straight to the fd: unflushed, the header came out last.
    assert done.stdout.splitlines() == ["squeue ran", "== start estimates", "squeue ran"]


def test_job_runs_sacct_with_the_requeue_history(ubelix_host):
    done, ran = ubelix_host("job 18929698")
    assert done.returncode == 0
    assert ran[0].startswith("sacct -j 18929698 -u th19c587 -D -X ")


def test_record_hands_out_the_status_fields_and_the_stage_log_tail(ubelix_host):
    done, ran = ubelix_host(f"record {RECORD} 3")
    assert done.returncode == 0 and ran == []
    out = json.loads(done.stdout)
    assert out["status"] == "training" and out["stage"] == "train"
    assert "request" not in out
    # The CER is what a finished record is asked for; on 24.10.2026 it was
    # filtered out and read as "no CER yet".
    assert out["metrics"] == {"cer": 0.2659, "wer": 0.61, "samples": 120}
    assert out["log"]["path"].endswith("logs/train.log")
    assert out["log"]["tail"] == ["step 497", "step 498", "step 499"]


def test_record_lines_are_capped_on_ubelix_too(ubelix_host):
    done, _ = ubelix_host(f"record {RECORD} 999")
    assert len(json.loads(done.stdout)["log"]["tail"]) == 200


def test_an_in_process_stage_falls_back_to_runner_log(ubelix_host):
    rec = ubelix_host.root / RECORD
    data = json.loads((rec / "job.json").read_text())
    data["stage"] = "prepare"
    (rec / "job.json").write_text(json.dumps(data))
    (rec / "logs" / "runner.log").write_text("prepared\n")
    out = json.loads(ubelix_host(f"record {RECORD}")[0].stdout)
    assert out["log"]["path"].endswith("logs/runner.log")


def test_a_stage_name_from_the_record_cannot_climb_out_of_logs(ubelix_host):
    rec = ubelix_host.root / RECORD
    data = json.loads((rec / "job.json").read_text())
    data["stage"] = "../../../etc/passwd"
    (rec / "job.json").write_text(json.dumps(data))
    out = json.loads(ubelix_host(f"record {RECORD}")[0].stdout)
    assert out["log"] is None


def test_an_unknown_record_is_an_answer_not_a_traceback(ubelix_host):
    done, _ = ubelix_host("record 20261019T180118Z-nothing-here")
    assert done.returncode == 1
    assert json.loads(done.stdout)["error"] == "no such record"


def test_the_script_is_executable():
    assert os.access(SCRIPT, os.X_OK)
