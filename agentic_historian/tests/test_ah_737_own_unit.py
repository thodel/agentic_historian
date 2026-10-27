"""#737 — a heavy job runs in its own systemd user unit, or is refused with the
commands to run it by hand.

On 25.10. `atr-mcp.service` ran with MemoryMax=200M; the third BLB upload
(7.8 GB) pushed the cgroup over, and the kernel killed `pagexml-hf` and the MCP
server with it. The fourth ran from a shell. Decided (way B, C as fallback):

* a real `upload_hf` and a real `oai_gt_import` build start through
  `systemd-run --user` in a unit `ah-job-<id>` with MemoryMax from
  ATR_JOB_MEMORY_MAX and OOMPolicy=continue — out of the server's cgroup;
* the server's environment goes by **name** (`--setenv=NAME`), never a value;
* dry runs and light jobs start as before;
* without a reachable user manager the job is refused — not run under the
  200 MB — and the refusal carries the nohup command built from the job's own
  argv and the `loginctl enable-linger` line;
* status asks the unit (`systemctl --user is-active`), says "unknown" rather
  than "vanished" when it cannot ask, and shows whether the limit took;
* stop signals the unit's main process, which forwards TERM to its child.

`systemd-run` and `systemctl` are fakes on PATH; the job itself runs through
the real `_run.py`.
"""

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402

FAKE_RUN = r'''#!{python}
"""Fake systemd-run: record argv, start the command detached, keep its pid."""
import json, os, subprocess, sys
units = os.environ["FAKE_UNITS"]
args = sys.argv[1:]
with open(os.path.join(units, "argv.json"), "w") as f:
    json.dump(args, f)
if os.environ.get("FAKE_RUN_FAIL"):
    print("Failed to start transient service unit: Access denied", file=sys.stderr)
    sys.exit(1)
unit, cwd, i = None, None, 0
while i < len(args):
    a = args[i]
    if a == "-p":
        i += 2
        continue
    if a.startswith("--unit="):
        unit = a.split("=", 1)[1]
    elif a.startswith("--working-directory="):
        cwd = a.split("=", 1)[1]
    elif not a.startswith("-"):
        break
    i += 1
proc = subprocess.Popen(args[i:], cwd=cwd, start_new_session=True,
                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL)
open(os.path.join(units, unit + ".pid"), "w").write(str(proc.pid))
'''

FAKE_CTL = r'''#!{python}
"""Fake systemctl --user: is-system-running, is-active <unit>, kill <unit>."""
import os, signal, sys
units = os.environ["FAKE_UNITS"]
args = [a for a in sys.argv[1:] if a != "--user"]
open(os.path.join(units, "ctl.log"), "a").write(" ".join(args) + "\n")
if args[0] == "is-system-running":
    print(os.environ.get("FAKE_MANAGER", "running"))
    sys.exit(0)
unit = args[-1]
try:
    pid = int(open(os.path.join(units, unit + ".pid")).read())
except OSError:
    print("inactive"); sys.exit(3)
try:
    os.kill(pid, 0)
    alive = True
except ProcessLookupError:
    alive = False
if args[0] == "is-active":
    print("active" if alive else "inactive"); sys.exit(0 if alive else 3)
if args[0] == "kill":
    os.kill(pid, signal.SIGTERM); sys.exit(0)
sys.exit(1)
'''


@pytest.fixture
def host(tmp_path, monkeypatch):
    """A user manager that answers, fake systemd tools on PATH, a data dir."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, text in (("systemd-run", FAKE_RUN), ("systemctl", FAKE_CTL)):
        tool = bin_dir / name
        tool.write_text(text.replace("{python}", sys.executable))
        tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    units = tmp_path / "units"
    units.mkdir()
    runtime = tmp_path / "run_user"
    (runtime / str(os.getuid())).mkdir(parents=True)
    (runtime / str(os.getuid()) / "bus").write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_UNITS", str(units))
    monkeypatch.setenv("HF_TOKEN", "hf_this_must_not_appear")
    monkeypatch.setattr(jobs, "USER_RUNTIME", runtime)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "ATR_JOB_MEMORY_MAX", "2G")
    return units


def _wait(job_id, until=("done", "failed"), budget=20.0):
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        job = jobs.status(job_id)
        if job.state in until:
            return job
        time.sleep(0.1)
    return jobs.status(job_id)


ARGV_OK = [sys.executable, "-c", "import os; print('in', os.environ['AH_JOB_DIR'])"]


def test_a_heavy_job_runs_in_its_own_unit(host):
    job = jobs.start("upload-hf", ARGV_OK, own_unit=True)
    assert job.unit == {"name": f"ah-job-{job.job_id}", "memory_max": "2G"}

    argv = json.loads((host / "argv.json").read_text())
    assert argv[:5] == ["--user", f"--unit=ah-job-{job.job_id}", "--collect", "--quiet", "-p"]
    assert "MemoryMax=2G" in argv and "OOMPolicy=continue" in argv
    assert argv[-2].endswith("_run.py") and argv[-1].endswith(job.job_id)

    done = _wait(job.job_id)
    assert done.state == "done" and done.exit_code == 0
    assert done.as_dict()["unit"]["name"] == f"ah-job-{job.job_id}"
    assert "in " in jobs.read_log(job.job_id), "the real _run.py ran the job"
    meta = json.loads((jobs.jobs_root() / job.job_id / "meta.json").read_text())
    assert meta["unit"] == f"ah-job-{job.job_id}" and meta.get("pid") is None


def test_the_environment_goes_by_name_never_by_value(host, monkeypatch):
    monkeypatch.setenv("INVOCATION_ID", "0123abcd")     # the server unit's, not the job's
    jobs.start("upload-hf", ARGV_OK, own_unit=True)
    argv = json.loads((host / "argv.json").read_text())
    assert "--setenv=HF_TOKEN" in argv
    assert not any("hf_this_must_not_appear" in a for a in argv)
    assert not any(a.startswith("--setenv=INVOCATION_ID") for a in argv)


def test_without_a_user_manager_it_is_refused_with_both_ways_out(host, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "USER_RUNTIME", tmp_path / "nobody_logged_in")
    monkeypatch.setenv("USER", "dh")
    before = sorted(p.name for p in jobs.jobs_root().iterdir())
    argv = [sys.executable, "-m", "agentic_historian", "upload-hf",
            "--tree", "/mnt/x/datasets/blb gt", "--repo-id", "dh-unibe/x"]

    with pytest.raises(jobs.JobError) as err:
        jobs.start("upload-hf", argv, own_unit=True)

    message = str(err.value)
    assert message.startswith("not started: `upload-hf` runs in its own systemd unit "
                              "with MemoryMax=2G")
    assert "no systemd user manager for uid" in message
    assert "PYTHONUNBUFFERED=1 nohup " in message
    assert "upload-hf --tree '/mnt/x/datasets/blb gt' --repo-id dh-unibe/x" in message, \
        "the job's own argv, quoted for a shell"
    assert "sudo loginctl enable-linger dh" in message
    assert sorted(p.name for p in jobs.jobs_root().iterdir()) == before, "no job was made"
    assert not (host / "argv.json").exists(), "nothing was started"


def test_a_manager_that_is_not_running_is_refused(host, monkeypatch):
    monkeypatch.setenv("FAKE_MANAGER", "offline")
    assert "says 'offline'" in jobs.user_manager()
    with pytest.raises(jobs.JobError):
        jobs.start("upload-hf", ARGV_OK, own_unit=True)


def test_a_failing_systemd_run_is_refused_and_leaves_no_job(host, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_FAIL", "1")
    with pytest.raises(jobs.JobError) as err:
        jobs.start("upload-hf", ARGV_OK, own_unit=True)
    assert "systemd-run failed: Failed to start transient service unit" in str(err.value)
    assert "nohup" in str(err.value)
    assert list(jobs.jobs_root().iterdir()) == []


def test_a_limit_systemd_cannot_read_is_refused(host, monkeypatch):
    monkeypatch.setattr(config, "ATR_JOB_MEMORY_MAX", "2 GB; rm -rf /")
    assert "is not a size systemd reads" in jobs.user_manager()


def test_a_light_job_still_starts_as_before(host):
    job = jobs.start("upload-hf", ARGV_OK)
    assert job.unit is None and job.pid
    assert not (host / "argv.json").exists()
    assert _wait(job.job_id).state == "done"


def test_running_is_asked_of_the_unit_and_stop_reaches_the_child(host):
    argv = [sys.executable, "-c", "import time\nfor _ in range(600): time.sleep(0.05)"]
    job = jobs.start("upload-hf", argv, own_unit=True)
    assert _wait(job.job_id, until=("running",)).state == "running"

    stopped = jobs.stop(job.job_id)
    assert stopped.state in ("running", "failed")
    done = _wait(job.job_id, until=("failed",))
    assert done.state == "failed" and done.exit_code == -15, "TERM, forwarded by the runner"
    log = (host / "ctl.log").read_text()
    assert f"kill --kill-whom=main --signal=SIGTERM ah-job-{job.job_id}" in log


def test_a_unit_gone_without_an_exit_code_is_vanished(host):
    job = jobs.start("upload-hf", ARGV_OK, own_unit=True)
    _wait(job.job_id)
    (jobs.jobs_root() / job.job_id / "exit_code").unlink()
    (host / f"ah-job-{job.job_id}.pid").write_text(str(2 ** 22 + 11))
    assert jobs.status(job.job_id).state == "vanished"


def test_a_unit_that_cannot_be_asked_is_unknown_not_vanished(host, monkeypatch):
    job = jobs.start("upload-hf", ARGV_OK, own_unit=True)
    _wait(job.job_id)
    (jobs.jobs_root() / job.job_id / "exit_code").unlink()
    monkeypatch.setattr(jobs, "_systemctl", lambda *a, **k: None)
    assert jobs.status(job.job_id).state == "unknown"


def test_status_shows_whether_the_limit_took(host):
    job = jobs.start("upload-hf", ARGV_OK, own_unit=True)
    directory = jobs.jobs_root() / job.job_id
    _wait(job.job_id)
    (directory / "memcg.json").write_text(json.dumps(
        {"start": {"cgroup": "/user.slice/x", "memory_max": "max", "oom_kill": 0}}))
    assert jobs.status(job.job_id).unit["applied"] == "max", \
        "requested 2G, found unlimited: the memory controller is not delegated"
    (directory / "memcg.json").write_text(json.dumps(
        {"start": {"memory_max": 2 * 1024 ** 3}}))
    assert jobs.status(job.job_id).unit["applied"] == 2 * 1024 ** 3


def test_an_oom_in_the_unit_is_the_units_and_names_no_neighbours(host):
    job = jobs.start("upload-hf", ARGV_OK, own_unit=True)
    directory = jobs.jobs_root() / job.job_id
    _wait(job.job_id)
    (directory / "exit_code").write_text("-9\n")
    (directory / "memcg.json").write_text(json.dumps({
        "start": {"cgroup": "/u", "memory_max": 2 * 1024 ** 3, "oom": 0, "oom_kill": 0},
        "end": {"cgroup": "/u", "memory_max": 2 * 1024 ** 3, "oom": 1, "oom_kill": 1}}))
    other = jobs.jobs_root() / "20000101T000000-other0"
    other.mkdir()
    (other / "meta.json").write_text(json.dumps(
        {"job_id": other.name, "started_at": "2000-01-01T00:00:00+00:00", "pid": os.getpid()}))
    memory = jobs.status(job.job_id).memory
    assert memory["message"] == "killed: the job's own unit ran out (oom_kill 0 → 1, memory.max 2.1 GB)"
    assert "concurrent_jobs" not in memory


@pytest.mark.parametrize("dry_run,own", [(True, False), (False, True)])
def test_only_a_real_upload_asks_for_a_unit(monkeypatch, tmp_path, dry_run, own):
    seen = {}
    monkeypatch.setattr(jobs, "resolve_tree", lambda tree: tmp_path)
    monkeypatch.setattr(jobs, "upload_hf_argv", lambda *a, **k: ["x"])
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.update(kw) or {})
    jobs.upload_hf_job("t", dry_run=dry_run)
    assert seen.get("own_unit") is own


@pytest.mark.parametrize("step,dry_run,own", [
    ("inventory", True, False), ("inventory", False, False),
    ("build", True, False), ("build", False, True)])
def test_only_a_real_build_asks_for_a_unit(monkeypatch, step, dry_run, own):
    import oai_gt
    seen = {}
    source = sorted(oai_gt.SOURCES)[0]
    monkeypatch.setattr(jobs, "oai_gt_import_argv", lambda *a, **k: ["x"])
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.update(kw) or {})
    jobs.oai_gt_import_job(source, step, dry_run=dry_run)
    assert seen.get("own_unit") is own
