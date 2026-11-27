"""#911: files two users share must be created with a mode both can use.

On tei the bot writes as its service user and the CLI as ``dh``, into the same
``data/`` directories. ``tempfile.mkstemp`` creates 0600 and ``os.open`` without
a mode creates 0644, so every run state the CLI wrote was unreadable to the bot
(``/campaign`` saw no pending page of a document it listed as present) and the
hub's lock file was not writable for the CLI (``Permission denied``, Agent C).

Asserted here: a JSON file written atomically and a lock file both get the mode
a plain ``open()`` would give — ``0o666 & ~umask`` — whatever the umask is.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_911_shared_file_modes.py
"""

from __future__ import annotations

import json
import os
import stat
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import shared_lock


@contextmanager
def _umask(value: int):
    old = os.umask(value)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.parametrize("umask, expected", [(0o022, 0o644), (0o002, 0o664),
                                             (0o077, 0o600)])
def test_an_atomically_written_file_has_the_mode_a_plain_open_would_give(
        tmp_path, umask, expected):
    target = tmp_path / "runs" / "doc.json"
    with _umask(umask):
        shared_lock.write_json_atomic(target, {"doc_id": "doc"})

    assert _mode(target) == expected, oct(_mode(target))
    assert json.loads(target.read_text()) == {"doc_id": "doc"}


def test_a_rewrite_keeps_the_shared_mode(tmp_path):
    """Every save replaces the file; the second must not fall back to 0600."""
    target = tmp_path / "doc.json"
    with _umask(0o002):
        shared_lock.write_json_atomic(target, {"n": 1})
        shared_lock.write_json_atomic(target, {"n": 2})

    assert _mode(target) == 0o664 and json.loads(target.read_text()) == {"n": 2}


def test_the_lock_file_is_shared_too(tmp_path):
    """The lock the other user cannot open is the lock they cannot take."""
    target = tmp_path / "hub.json"
    with _umask(0o002):
        shared_lock.write_json_atomic(target, {})

    lock = shared_lock._lock_path(target)
    assert lock.exists(), "no lock file was created"
    assert _mode(lock) == 0o664, oct(_mode(lock))


def test_the_umask_is_read_without_being_changed():
    with _umask(0o027):
        assert shared_lock._current_umask() == 0o027
        assert shared_lock._current_umask() == 0o027, "reading it changed it"
        assert shared_lock._shared_mode() == 0o640
