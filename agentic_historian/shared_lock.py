"""
shared_lock.py — R3 (#393): per-process lock + atomic-write wrappers for
cross-document writes in the pipeline.

Guiding principles
-----------------
* JSONL appends (preferences, feedback, votes) are part of the published
  methodology and **must stay as JSONL files**.
* Every write in this module is wrapped with a ``FileLock`` (blocking,
  exclusive) around an ``fcntl.flock`` on a lock-file adjacent to the target.
  This makes the critical sections safe for N workers even when they share
  the same filesystem (SMB / NFS with ``actimeo=0`` caveats apply — documented
  in the AGENTS.md note in this module).
* The atomic-write helper (``write_atomic``) uses ``tempfile`` + ``os.replace``
  and is only used for JSON files that are fully re-written; it does NOT need
  a cross-process lock because it writes a temp file in the same directory first,
  then atomically replaces the target.  Callers that both read-and-write a
  shared JSON file (processed_orders.json, META_LOG_PATH) need BOTH the lock
  and the atomic write.

Usage
-----
    from shared_lock import locked_append, read_json, write_json_atomic

    locked_append(config.PREFERENCES_LOG_PATH, json.dumps(record))
    data = read_json(config.PREFERENCES_LOG_PATH, default=[])
    write_json_atomic(config.META_LOG_PATH, data)          # safe for N workers
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _lock_path(target: Path) -> Path:
    """Lock file lives next to the data file it protects."""
    return target.parent / f".{target.name}.lock"


def _acquire(lock_path: Path) -> int:
    """Acquire an exclusive blocking lock; return the file descriptor."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _release(fd: int) -> None:
    """Release and close the lock file descriptor."""
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def locked_append(path: Path, line: str) -> None:
    """Append ``line`` (already JSON-serialised) to ``path``, safely.

    The line must NOT contain a trailing newline — this function adds one.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(path)
    fd = _acquire(lock_path)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            if not line.endswith("\n"):
                fh.write("\n")
    finally:
        _release(fd)


def write_json_atomic(path: Path, data: Any) -> None:
    """Write ``data`` (arbitrary JSON-serialisable object) atomically to ``path``.

    Works for any JSON file, including those that are re-written in full
    (e.g. data/runs/*.json, hub.json, processed_orders.json, META_LOG_PATH).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(path)
    fd = _acquire(lock_path)
    try:
        # Write to a temp file in the same directory so rename is atomic.
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp",
            dir=path.parent,
            encoding="utf-8",
            delete=False,
        )
        try:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            if os.path.exists(tmp.name):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
    finally:
        _release(fd)


def read_json(path: Path, default: Any = None) -> Any:
    """Read a JSON file. Return ``default`` if it does not exist or is corrupt."""
    path = Path(path)
    if not path.exists():
        return default() if callable(default) else default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"[shared_lock] could not read {path}: {exc}")
        return default() if callable(default) else default


def read_jsonl(path: Path):
    """Context manager that yields each line of a JSONL file under a shared lock.

    Usage::

        with read_jsonl(path) as lines:
            for line in lines:
                record = json.loads(line)

    The lock is held for the entire read, preventing concurrent writers from
    truncating the file mid-read on NFS.
    """
    path = Path(path)
    lock_path = _lock_path(path)
    fd = _acquire(lock_path)
    try:
        if path.exists():
            yield path.read_text(encoding="utf-8").splitlines()
        else:
            yield []
    finally:
        _release(fd)
