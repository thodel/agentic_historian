"""#736 — `job_status` explains a SIGKILL from the cgroup's OOM counter.

On 25.10. the upload job `20261025T191451-cf6eeb` ended with `pagexml-hf exited
-9` and nothing more. The cause — `oom_memcg=/system.slice/atr-mcp.service`,
the service's `MemoryMax=200M` — stood only in the kernel log, which takes
sudo; a wrong cause (CIFS mmap) was fixed first. The counter is in
`memory.events` and readable without sudo. These pin:

* the runner writes the counters before and after the job, and hands the job
  its directory as AH_JOB_DIR;
* failed with -9, or with a child reported as killed: "killed: the service's
  memory cgroup ran out (oom_kill 3 → 4, memory.max 200 MiB)";
* an OOM kill without the cgroup reaching its limit is the host's, and says so;
* SIGKILL without an OOM kill says that, instead of blaming memory;
* a runner killed with its job reads the counter now and says so;
* other jobs at the same time are named — the counter is the service's;
* an unreadable counter is "not known", and a job from before #736 is silent.
"""

import importlib.util
import json
import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import host_resources                      # noqa: E402
from mcp_atr import jobs                   # noqa: E402

CGROUP = "/system.slice/atr-mcp.service"
MIB200 = 200 * 2 ** 20


def _runner():
    spec = importlib.util.spec_from_file_location("ah_run_736", PKG / "mcp_atr" / "_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cgroup(tmp_path, monkeypatch, oom=1, oom_kill=3, readable=True):
    proc = tmp_path / "proc_cgroup"
    proc.write_text(f"0::{CGROUP}\n")
    root = tmp_path / "cg"
    base = root / CGROUP.lstrip("/")
    base.mkdir(parents=True)
    (base / "memory.max").write_text(f"{MIB200}\n")
    if readable:
        _events(base, oom, oom_kill)
    monkeypatch.setattr(host_resources, "PROC_CGROUP", proc)
    monkeypatch.setattr(host_resources, "CGROUP_ROOT", root)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    return proc, root, base


def _events(base, oom, oom_kill):
    (base / "memory.events").write_text(
        f"low 0\nhigh 0\nmax 12\noom {oom}\noom_kill {oom_kill}\noom_group_kill 0\n")


def _run_job(tmp_path, monkeypatch, body, *, job_id="20261025T191451-cf6eeb",
             started_at=None):
    """Run a job through the real _run.py, its child a Python snippet."""
    proc, root, base = (tmp_path / "proc_cgroup", tmp_path / "cg",
                        tmp_path / "cg" / CGROUP.lstrip("/"))
    run = _runner()
    monkeypatch.setattr(run, "PROC_CGROUP", proc)
    monkeypatch.setattr(run, "CGROUP_ROOT", root)
    directory = jobs.jobs_root() / job_id
    directory.mkdir(parents=True)
    script = textwrap.dedent(body).replace("EVENTS", str(base / "memory.events"))
    meta = {"job_id": job_id, "kind": "upload-hf",
            "argv": [sys.executable, "-c", script],
            "started_at": started_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cwd": str(tmp_path), "pid": None}
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert run.main(["_run.py", str(directory)]) == 0
    return directory


KILLED_BY_LIMIT = """
    import os, signal
    open("EVENTS", "w").write("max 13\\noom 2\\noom_kill 4\\n")
    print("job dir", os.environ["AH_JOB_DIR"], flush=True)
    os.kill(os.getpid(), signal.SIGKILL)
"""


def test_a_job_killed_by_the_service_limit_says_so(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, KILLED_BY_LIMIT)

    record = json.loads((directory / "memcg.json").read_text())
    assert record["start"]["oom_kill"] == 3 and record["end"]["oom_kill"] == 4
    assert record["start"]["memory_max"] == MIB200 and record["start"]["cgroup"] == CGROUP
    assert f"job dir {directory}" in jobs.read_log(directory.name), "AH_JOB_DIR is the job's"

    job = jobs.status(directory.name)
    assert job.state == "failed" and job.exit_code == -9
    memory = job.as_dict()["memory"]
    assert memory["oom_killed"] is True and memory["at_limit"] is True
    assert memory["message"] == ("killed: the service's memory cgroup ran out "
                                 "(oom_kill 3 → 4, memory.max 200 MiB)")


def test_a_child_reported_killed_counts_as_a_sigkill(tmp_path, monkeypatch):
    """25.10.: the job exited 1 and its log said `pagexml-hf exited -9`."""
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, """
        import sys
        open("EVENTS", "w").write("oom 2\\noom_kill 4\\n")
        print("Error: pagexml-hf exited -9", flush=True)
        sys.exit(1)
    """)
    memory = jobs.status(directory.name).memory
    assert memory["sigkill"] is True
    assert memory["message"].startswith("killed: the service's memory cgroup ran out")


def test_a_failure_with_an_oom_elsewhere_in_the_service_does_not_claim_it(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, """
        import sys
        open("EVENTS", "w").write("oom 2\\noom_kill 4\\n")
        sys.exit(1)
    """)
    message = jobs.status(directory.name).memory["message"]
    assert message.startswith("an OOM kill hit this service while this job ran")
    assert "does not say it was this job's" in message


def test_an_oom_kill_below_the_limit_is_the_hosts(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, """
        import os, signal
        open("EVENTS", "w").write("oom 1\\noom_kill 4\\n")
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    memory = jobs.status(directory.name).memory
    assert memory["at_limit"] is False
    assert "the host ran out of memory, not the service's limit" in memory["message"]


def test_a_sigkill_without_an_oom_kill_does_not_blame_memory(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, """
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    memory = jobs.status(directory.name).memory
    assert memory["oom_killed"] is False
    assert memory["message"].startswith("SIGKILL, but no OOM kill in this service (oom_kill 3 → 3)")


def test_an_ordinary_failure_carries_no_memory_block(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, "import sys; sys.exit(2)")
    assert "memory" not in jobs.status(directory.name).as_dict()


def test_a_successful_job_is_not_judged(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = _run_job(tmp_path, monkeypatch, """
        open("EVENTS", "w").write("oom 2\\noom_kill 4\\n")
    """)
    assert jobs.status(directory.name).memory is None


def test_a_runner_killed_with_its_job_reads_the_counter_now(tmp_path, monkeypatch):
    """The server and its jobs share the cgroup: on 25.10. both went."""
    _, _, base = _cgroup(tmp_path, monkeypatch)
    directory = jobs.jobs_root() / "20261025T191451-aaaaaa"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps({
        "job_id": directory.name, "kind": "upload-hf", "argv": ["x"],
        "started_at": "2026-10-25T19:14:51+00:00", "pid": 2 ** 22 + 7}))
    (directory / "memcg.json").write_text(json.dumps({"start": {
        "cgroup": CGROUP, "memory_max": MIB200, "oom": 1, "oom_kill": 3}}))
    _events(base, 2, 5)

    job = jobs.status(directory.name)
    assert job.state == "vanished"
    memory = job.memory
    assert memory["read_now"] is True and memory["oom_kill"] == [3, 5]
    assert memory["message"].startswith(
        "killed: the service's memory cgroup ran out (oom_kill 3 → 5, the second "
        "read now: the job's runner died with it")


def test_other_jobs_at_the_same_time_are_named(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    root = jobs.jobs_root()
    now = datetime.now(timezone.utc)

    def other(name, started, finished=None):
        d = root / name
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({
            "job_id": name, "started_at": started.isoformat(timespec="seconds"),
            "pid": 2 ** 22 + 9}))
        if finished is not None:
            (d / "exit_code").write_text("0\n")
            os.utime(d / "exit_code", (finished.timestamp(), finished.timestamp()))

    other("20261025T190000-before", now - timedelta(hours=3), now - timedelta(hours=2))
    other("20261025T191000-during", now - timedelta(hours=1), now + timedelta(minutes=5))
    directory = _run_job(tmp_path, monkeypatch, KILLED_BY_LIMIT, job_id="20261025T191451-target",
                         started_at=(now - timedelta(minutes=1)).isoformat(timespec="seconds"))
    os.utime(directory / "exit_code", ((now + timedelta(minutes=1)).timestamp(),) * 2)

    memory = jobs.status(directory.name).memory
    assert memory["concurrent_jobs"] == ["20261025T191000-during"]
    assert "1 other job(s) ran in this service at the same time" in memory["message"]
    assert "does not say which one was killed" in memory["message"]


def test_an_unreadable_counter_is_not_known_not_zero(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch, readable=False)
    directory = _run_job(tmp_path, monkeypatch, """
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    memory = jobs.status(directory.name).memory
    assert memory["oom_killed"] is None and "is not known" in memory["message"]


def test_a_job_from_before_736_is_silent(tmp_path, monkeypatch):
    _cgroup(tmp_path, monkeypatch)
    directory = jobs.jobs_root() / "20261020T000000-old000"
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps({
        "job_id": directory.name, "started_at": "2026-10-20T00:00:00+00:00"}))
    (directory / "exit_code").write_text("-9\n")
    assert "memory" not in jobs.status(directory.name).as_dict()


def test_a_timestamp_without_a_zone_is_utc_not_a_type_error():
    naive, aware = jobs._parse_ts("2026-10-25T19:00:00"), jobs._parse_ts("2026-10-25T19:00:00+00:00")
    assert naive == aware and jobs._parse_ts(None) is None
