"""Tests for shared_lock module (#393)."""
import json
import multiprocessing
import threading
from pathlib import Path

from shared_lock import locked_append, read_json, read_jsonl, write_json_atomic

N_WORKERS = 8
PER_WORKER = 50


def test_locked_append_and_read(tmp_path: Path):
    f = tmp_path / "data.jsonl"
    locked_append(f, json.dumps({"a": 1}))
    locked_append(f, json.dumps({"b": 2}))
    lines = f.read_text(encoding="utf-8").splitlines()
    assert lines == [json.dumps({"a": 1}), json.dumps({"b": 2})]


def test_write_json_atomic_and_read(tmp_path: Path):
    f = tmp_path / "data.json"
    data = {"x": [1, 2, 3], "y": "test"}
    write_json_atomic(f, data)
    assert read_json(f) == data
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_read_json_default_when_absent_or_corrupt(tmp_path: Path):
    assert read_json(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert read_json(bad, default=list) == []


def test_read_jsonl_is_a_context_manager(tmp_path: Path):
    f = tmp_path / "data2.jsonl"
    locked_append(f, json.dumps({"c": 3}))
    with read_jsonl(f) as lines:
        assert lines == [json.dumps({"c": 3})]
    with read_jsonl(tmp_path / "absent.jsonl") as lines:
        assert lines == []


def _append_many(path: str, worker: int) -> None:
    for i in range(PER_WORKER):
        # Long payload: an unlocked append of this size can interleave.
        locked_append(Path(path), json.dumps({"w": worker, "i": i, "pad": "x" * 2000}))


def _assert_complete(path: Path) -> None:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == N_WORKERS * PER_WORKER
    assert {(r["w"], r["i"]) for r in records} == {
        (w, i) for w in range(N_WORKERS) for i in range(PER_WORKER)
    }


def test_locked_append_from_many_threads(tmp_path: Path):
    f = tmp_path / "threads.jsonl"
    threads = [threading.Thread(target=_append_many, args=(str(f), w)) for w in range(N_WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _assert_complete(f)


def test_locked_append_from_many_processes(tmp_path: Path):
    f = tmp_path / "procs.jsonl"
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_append_many, args=(str(f), w)) for w in range(N_WORKERS)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    assert all(p.exitcode == 0 for p in procs)
    _assert_complete(f)


def _rewrite_many(path: str, worker: int) -> None:
    for i in range(PER_WORKER):
        write_json_atomic(Path(path), {"w": worker, "i": i, "pad": "y" * 5000})


def test_write_json_atomic_never_leaves_a_torn_file(tmp_path: Path):
    f = tmp_path / "state.json"
    write_json_atomic(f, {"w": -1, "i": -1})
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_rewrite_many, args=(str(f), w)) for w in range(4)]
    for p in procs:
        p.start()
    torn = 0
    while any(p.is_alive() for p in procs):
        if read_json(f, default=None) is None:
            torn += 1
    for p in procs:
        p.join()
    assert all(p.exitcode == 0 for p in procs)
    assert torn == 0
    assert read_json(f)["i"] == PER_WORKER - 1
    assert [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_writer_waits_for_a_held_lock(tmp_path: Path):
    """The lock really excludes: a held lock blocks an append until released.

    Concurrent O_APPEND writes are atomic on a local filesystem, so the hammer
    tests above cannot tell a working lock from none; this one can.
    """
    import shared_lock

    f = tmp_path / "held.jsonl"
    fd = shared_lock._acquire(shared_lock._lock_path(f))
    done = threading.Event()

    def writer():
        locked_append(f, json.dumps({"late": True}))
        done.set()

    t = threading.Thread(target=writer)
    t.start()
    assert not done.wait(0.3), "append went through while the lock was held"
    shared_lock._release(fd)
    assert done.wait(5)
    t.join()
    assert f.read_text(encoding="utf-8").splitlines() == [json.dumps({"late": True})]
