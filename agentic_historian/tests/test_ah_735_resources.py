"""#735 — `server_status` reports space and memory: disk per root, host RAM,
the service's MemoryMax.

Three uploads of the BLB tree (7.8 GB) failed on 25./26.10. before three numbers
were known: 22 GB free on `/` against ~25 GB needed, 3 GB of RAM, and
`MemoryMax=200M` on `atr-mcp.service`. Each would have saved a failed attempt.
All of it reads without sudo: `statvfs`, `/proc/meminfo`, the own cgroup.
These pin the readings, three-valued (unread is None, unlimited is "max"), and
that cache sizes — a walk — come only with `counts`.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import host_resources as hr                # noqa: E402
from mcp_atr import jobs                   # noqa: E402

CGROUP = "/system.slice/atr-mcp.service"
MEMINFO = """MemTotal:        3013656 kB
MemFree:          120000 kB
MemAvailable:     812345 kB
SwapCached:            0 kB
SwapTotal:       2097148 kB
SwapFree:        1048572 kB
"""


def _host(tmp_path, monkeypatch, memory_max="209715200"):
    proc = tmp_path / "proc_cgroup"
    proc.write_text(f"0::{CGROUP}\n")
    base = tmp_path / "cg" / CGROUP.lstrip("/")
    base.mkdir(parents=True)
    (base / "memory.max").write_text(memory_max + "\n")
    (base / "memory.high").write_text("max\n")
    (base / "memory.current").write_text("150994944\n")
    (base / "memory.events").write_text("low 0\nhigh 0\nmax 40\noom 3\noom_kill 3\n")
    (tmp_path / "meminfo").write_text(MEMINFO)
    monkeypatch.setattr(hr, "PROC_CGROUP", proc)
    monkeypatch.setattr(hr, "CGROUP_ROOT", tmp_path / "cg")
    monkeypatch.setattr(hr, "MEMINFO", tmp_path / "meminfo")


def _roots(tmp_path, monkeypatch):
    data = tmp_path / "data"
    datasets = tmp_path / "datasets"
    (datasets / "_hf_scratch" / "run1").mkdir(parents=True)
    (datasets / "_hf_scratch" / "run1" / "shard.parquet").write_bytes(b"x" * 3000)
    arrow = data / "hf_arrow_cache"
    (arrow / "run1").mkdir(parents=True)
    (arrow / "run1" / "data.arrow").write_bytes(b"x" * 5000)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", datasets)
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", None)
    monkeypatch.setattr(config, "ATR_HF_ARROW_CACHE", arrow)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", data / "vlm_test")     # not there yet


# ── the readers ──────────────────────────────────────────────────────────────

def test_the_services_cgroup_is_read_three_valued(tmp_path, monkeypatch):
    _host(tmp_path, monkeypatch)
    state = hr.memcg_state()
    assert state == {"cgroup": CGROUP, "max": 209715200, "high": "max",
                     "current": 150994944, "oom": 3, "oom_kill": 3}


def test_no_cgroup_is_unread_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(hr, "PROC_CGROUP", tmp_path / "missing")
    state = hr.memcg_state()
    assert state["cgroup"] is None and state["max"] is None and state["oom_kill"] is None


def test_meminfo_in_bytes(tmp_path, monkeypatch):
    _host(tmp_path, monkeypatch)
    assert hr.meminfo() == {"ram_total": 3013656 * 1024, "ram_available": 812345 * 1024,
                            "swap_total": 2097148 * 1024, "swap_free": 1048572 * 1024}


def test_a_root_not_there_yet_is_measured_where_it_would_be(tmp_path):
    entry = hr.disk(tmp_path / "not" / "yet")
    assert entry["exists"] is False and entry["measured_at"] == str(tmp_path)
    assert isinstance(entry["free"], int) and entry["total"] >= entry["free"]
    assert hr.disk(None) == {"path": None, "configured": False}


def test_sizes_read_in_the_unit_they_are_set_in():
    assert hr.fmt_bytes(209715200) == "200 MiB", "MemoryMax=200M is 200 MiB"
    assert hr.fmt_bytes(22 * 10 ** 9) == "22.0 GB"
    assert hr.fmt_bytes("max") == "unlimited" and hr.fmt_bytes(None) == "?"


# ── in server_status ─────────────────────────────────────────────────────────

def test_server_status_carries_disk_ram_and_the_limit(tmp_path, monkeypatch):
    _host(tmp_path, monkeypatch)
    _roots(tmp_path, monkeypatch)
    res = jobs._resources(counts=False)

    assert set(res["disk"]) == {"root", "data_dir", "datasets_dir", "hf_arrow_cache",
                                "hf_scratch", "vlm_test_root"}
    assert res["disk"]["hf_scratch"]["path"] == str(tmp_path / "datasets" / "_hf_scratch")
    assert res["disk"]["vlm_test_root"]["measured_at"] == str(tmp_path / "data")
    assert all(isinstance(d["free"], int) for d in res["disk"].values())
    assert res["host"]["ram_total"] == 3013656 * 1024
    assert res["service"]["max"] == 209715200 and res["service"]["oom_kill"] == 3
    assert res["caches"] is None, "a walk only with counts"
    summary = "\n".join(res["summary"])
    assert "service memory.max 200 MiB, in use 144 MiB, oom_kill 3" in summary
    assert "RAM 793 MiB available of 3.1 GB, swap 1.1 GB free of 2.1 GB" in summary
    assert summary.count(" free of ") == 7, "six roots and the swap"


def test_counts_sizes_the_upload_caches(tmp_path, monkeypatch):
    _host(tmp_path, monkeypatch)
    _roots(tmp_path, monkeypatch)
    caches = jobs._resources(counts=True)["caches"]
    assert caches["hf_arrow_cache"]["bytes"] == 5000
    assert caches["hf_scratch"]["bytes"] == 3000
    assert caches["hf_scratch"]["complete"] is True


def test_an_explicit_scratch_wins_and_no_datasets_dir_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", tmp_path / "s")
    assert jobs._hf_scratch_dir() == tmp_path / "s"
    monkeypatch.setattr(config, "ATR_HF_SCRATCH", None)
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", None)
    assert jobs._hf_scratch_dir() is None


def test_the_block_is_in_server_status(tmp_path, monkeypatch):
    _host(tmp_path, monkeypatch)
    _roots(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "_checkout_dir", lambda: tmp_path)
    status = jobs.server_status()
    assert status["resources"]["service"]["max"] == 209715200
