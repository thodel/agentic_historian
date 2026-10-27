"""
host_resources.py — disk, RAM and the service's memory cgroup, read without sudo.

Three uploads of the BLB tree (7.8 GB) failed on 25./26.10. before anybody knew
the three numbers that decided them: 22 GB free on `/` against ~25 GB needed,
3 GB of RAM on the host, and `MemoryMax=200M` on `atr-mcp.service` (#735). The
cause of the third was only in the kernel log, which takes sudo to read; a
wrong cause was fixed first (#736, LESSONS §40). Everything here is a
``statvfs`` or a small file under ``/proc`` and ``/sys/fs/cgroup``.

Unread is not zero: a value that could not be read is ``None``, and an
unlimited cgroup limit is the string ``"max"``, as the kernel writes it.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Optional

PROC_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")
MEMINFO = Path("/proc/meminfo")


def own_cgroup(proc_cgroup: Optional[Path] = None) -> Optional[str]:
    """This process's cgroup v2 path (``/system.slice/atr-mcp.service``), or None."""
    try:
        text = Path(proc_cgroup or PROC_CGROUP).read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return line.split("::", 1)[1].strip()
    return None


def _limit(raw: Optional[str]):
    if raw is None:
        return None
    raw = raw.strip()
    return "max" if raw == "max" else int(raw)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except (OSError, ValueError):
        return None


def memcg_state(cgroup: Optional[str] = None, root: Optional[Path] = None) -> dict:
    """``memory.max``, ``memory.high``, ``memory.current`` and two counters of
    ``memory.events`` for ``cgroup`` (default: this process's): ``oom`` — the
    cgroup reached its own limit — and ``oom_kill`` — a process in it was
    killed by any OOM killer, the host's included."""
    cgroup = cgroup if cgroup is not None else own_cgroup()
    out: dict = {"cgroup": cgroup, "max": None, "high": None,
                 "current": None, "oom": None, "oom_kill": None}
    if cgroup is None:
        return out
    base = Path(root or CGROUP_ROOT) / cgroup.lstrip("/")
    for name in ("max", "high", "current"):
        try:
            out[name] = _limit(_read(base / f"memory.{name}"))
        except ValueError:
            out[name] = None
    events = _read(base / "memory.events") or ""
    for line in events.splitlines():
        key, _, value = line.partition(" ")
        if key in ("oom", "oom_kill"):
            try:
                out[key] = int(value)
            except ValueError:
                pass
    return out


def meminfo(path: Optional[Path] = None) -> dict:
    """RAM and swap of the host, in bytes, from ``/proc/meminfo``."""
    keys = {"MemTotal": "ram_total", "MemAvailable": "ram_available",
            "SwapTotal": "swap_total", "SwapFree": "swap_free"}
    out: dict = dict.fromkeys(keys.values())
    for line in (_read(Path(path or MEMINFO)) or "").splitlines():
        name, _, rest = line.partition(":")
        if name in keys:
            try:
                out[keys[name]] = int(rest.split()[0]) * 1024
            except (IndexError, ValueError):
                pass
    return out


def disk(path) -> dict:
    """Free and total bytes on the filesystem that holds ``path``.

    A root that does not exist yet is measured on its nearest existing parent
    and says so (``measured_at``): where an upload *would* write is the
    question, and that filesystem exists. One ``statvfs``, never a walk.
    """
    if not path:
        return {"path": None, "configured": False}
    p = Path(path)
    probe = p
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    out: dict = {"path": str(p), "exists": p.exists(), "free": None, "total": None}
    if probe != p:
        out["measured_at"] = str(probe)
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return out
    out.update(free=usage.free, total=usage.total)
    return out


def dir_bytes(path, budget_s: float = 20.0) -> dict:
    """Bytes under ``path`` within a time budget; ``complete: false`` when the
    walk stopped early. Leftovers of aborted uploads are what this measures."""
    p = Path(path) if path else None
    if p is None or not p.is_dir():
        return {"path": str(p) if p else None, "exists": False, "bytes": 0, "complete": True}
    total, complete = 0, True
    deadline = time.monotonic() + budget_s
    for dirpath, _dirnames, filenames in os.walk(p):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
        if time.monotonic() > deadline:
            complete = False
            break
    return {"path": str(p), "exists": True, "bytes": total, "complete": complete}


def fmt_bytes(n) -> str:
    """``n`` bytes for a sentence: GB above a gigabyte, MiB below — the unit a
    cgroup limit (``200M`` = 200 MiB) is written in."""
    if n is None:
        return "?"
    if n == "max":
        return "unlimited"
    if n >= 10 ** 9:
        return f"{n / 10 ** 9:.1f} GB"
    return f"{n / 2 ** 20:.0f} MiB"
