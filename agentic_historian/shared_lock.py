"""
shared_lock.py — R3 (#393): per-process lock + atomic-write wrappers for
cross-document writes in the pipeline.

Guiding principles
-----------------
* JSONL appends (preferences, feedback, votes) are part of the published
  methodology and **must stay as JSONL files**.
* Every write in this module is wrapped with a blocking, exclusive
  ``fcntl.flock`` on a lock-file adjacent to the target. That makes the
  critical sections safe for N workers of this host.

**Where this holds, and where it would not.** Every file routed through here
lives under ``BASE_DIR`` — ``DATA_DIR`` (``processed_orders.json``, the run
states, the JSONL logs) and ``KH_DIR`` (``hub.json``) are both local disk, so
``flock`` is doing what it says. It would **not** carry over to the SMB share
(``/mnt/wbkolleg_dh_1``): ``flock`` over CIFS depends on the mount and the
server, and nothing here should be taken as a promise about a file that lives
there. Nothing routed through this module does.

``flock`` is also held per *open file description*, which has two consequences
worth stating: two threads of one process do not serialise against each other
through it (the knowledge hub keeps a ``threading.Lock`` for that layer), and a
second ``os.open`` + ``LOCK_EX`` on the same file from inside a section this
module already holds blocks for ever — which is why the holders write through
the unlocked ``_write_atomic``.
* The atomic-write helper (``write_atomic``) uses ``tempfile`` + ``os.replace``
  and is only used for JSON files that are fully re-written; it does NOT need
  a cross-process lock because it writes a temp file in the same directory first,
  then atomically replaces the target.  Callers that both read-and-write a
  shared JSON file (processed_orders.json, META_LOG_PATH) need BOTH the lock
  and the atomic write.

Usage
-----
    from shared_lock import locked_append, locked_json, read_json, write_json_atomic

    locked_append(config.PREFERENCES_LOG_PATH, json.dumps(record))
    data = read_json(config.PREFERENCES_LOG_PATH, default=[])
    write_json_atomic(config.META_LOG_PATH, data)          # safe for N workers

    with locked_json(path, default=list) as rows:          # read-modify-write
        rows.append(record)

Which one to reach for
----------------------
* **Append a record** → ``locked_append``. The published JSONL formats stay
  JSONL; only the append is serialised.
* **Replace a whole file** with something computed from elsewhere →
  ``write_json_atomic``.
* **Read a file, change part of it, write it back** → ``locked_json``, and
  nothing else. The other two cannot do this between them: the gap between a
  ``read_json`` and a ``write_json_atomic`` is exactly where another worker's
  write gets overwritten. That gap was real in ``switchdrive.mark_processed``
  and in ``knowledge_hub.hub`` (#393).
* **A file only one document's worker ever touches** → nothing needed, and
  ``runstate.RunState`` is the example: its path is derived from the doc id, so
  two workers never address the same file. It still writes through
  ``write_json_atomic``, which costs nothing and means a crash mid-write cannot
  leave a half-written run.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

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

def _write_atomic(path: Path, data: Any) -> None:
    """Write ``data`` to ``path`` via a temp file in the same directory.

    **Takes no lock**, and that is why it exists separately: ``flock`` is held
    per open-file-description, so a second ``os.open`` + ``LOCK_EX`` on the same
    file from the same process blocks for ever. Calling the locking
    ``write_json_atomic`` from inside a critical section this module already
    holds would deadlock, so every holder of the lock writes through this.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp:
            json.dump(data, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


@contextlib.contextmanager
def locked_json(path: Path, default: Any = None, coerce: Any = None):
    """Read, mutate and write back one JSON file under **one** lock.

    The critical section this module described but did not provide. Its own
    docstring said callers that read *and* write a shared file "need BOTH the
    lock and the atomic write" — and there was no way to do that: ``read_json``
    takes no lock and ``write_json_atomic`` acquires and releases its own, so
    between a caller's read and its write another worker could land a write that
    the caller then overwrote. That is a **lost update**, and it is what
    ``mark_processed`` and the knowledge hub were both doing (#393).

    Usage::

        with locked_json(path, default=list) as data:
            data.append(record)          # written back atomically on exit

    The object yielded is whatever the file held (or ``default``); mutate it in
    place or use ``data[:]`` / ``data.clear()`` to replace its contents — the
    object that comes back is what gets written.

    **An exception inside the block writes nothing.** A half-applied mutation is
    worse than none: the caller will retry, and it cannot retry against a file
    that already holds half of the last attempt.

    ``coerce`` is how a caller says *what shape it needs* (#643). The object
    yielded here is the object written back, so the caller can change it but
    cannot **replace** it: a ``data = {}`` inside the block rebinds the caller's
    name and leaves the file untouched. That is not a hypothetical — it is what
    ``knowledge_hub.hub._mutate`` was doing on a hub whose file held readable
    JSON of the wrong type, and the file and the cache then diverged in silence.
    Pass a callable and it is applied to the loaded value before the yield; what
    it returns is both yielded and written::

        def _as_mapping(value):
            return value if isinstance(value, dict) else {}

        with locked_json(path, default=dict, coerce=_as_mapping) as data:
            data["persons"] = …          # a dict, whatever the file held

    Without it the caller has to trust a file that another worker wrote, which
    is the one thing this module exists not to do.
    """
    path = Path(path)
    lock_path = _lock_path(path)
    fd = _acquire(lock_path)
    try:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(f"[shared_lock] unreadable {path.name}: {exc}")
                data = default() if callable(default) else default
        else:
            data = default() if callable(default) else default
        if coerce is not None:
            data = coerce(data)
        yield data
        _write_atomic(path, data)
    finally:
        _release(fd)


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
    lock_path = _lock_path(path)
    fd = _acquire(lock_path)
    try:
        _write_atomic(path, data)
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


@contextlib.contextmanager
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
