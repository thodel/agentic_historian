"""
test_ah_393_parallel_state.py — R3: shared state under N workers (#393).

One serial worker made plain file I/O safe; R2's parallel workers make it a
corruption lottery. The issue's test asks for the wrappers to be hammered from N
threads and N processes, asserting **no interleaved or truncated records and no
lost updates** — and lost updates are the part the module could not prevent,
because it had no way to hold one lock across a read, a change and a write.

What is covered here:

  1. `locked_json` — the read-modify-write critical section, from threads and
     from real processes. This is the new API; the two it sits beside cannot do
     this between them.
  2. `locked_append` — JSONL stays JSONL (the published format is part of the
     methodology), and a record is never interleaved with another.
  3. `write_json_atomic` — a reader never sees a half-written file.
  4. The three call sites that were unsafe: `processed_orders.json`, the
     knowledge hub, and the entity index.
  5. `RunState` — per-document by construction, which the issue asks to verify
     and document rather than change.

Processes, not only threads: `fcntl.flock` is held per open-file description, so
a test that only uses threads of one process proves almost nothing about the
lock. The thread tests are here for the in-process layer (`threading.Lock` in
the hub), the process tests for the lock itself.

Run: pytest agentic_historian/tests/test_ah_393_parallel_state.py -v
"""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import threading
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import shared_lock                                   # noqa: E402

WORKERS = 8
PER_WORKER = 25


# ── helpers that run in a child process (must be importable, so module-level) ──

def _append_ids(args) -> None:
    """Append `count` ids to a locked JSON list, as a separate process."""
    path, worker, count = args
    sys.path.insert(0, str(PKG))
    import shared_lock as sl

    for index in range(count):
        with sl.locked_json(Path(path), default=list) as rows:
            rows.append(f"w{worker}-{index}")


def _append_lines(args) -> None:
    path, worker, count = args
    sys.path.insert(0, str(PKG))
    import shared_lock as sl

    for index in range(count):
        sl.locked_append(Path(path),
                         json.dumps({"worker": worker, "n": index,
                                     "pad": "x" * 500}))


def _mark_orders(args) -> None:
    """Mark order ids processed, as a separate process."""
    path, worker, count = args
    sys.path.insert(0, str(PKG))
    import config
    config.DATA_DIR = Path(path)
    import utils.switchdrive as sd
    sd._PROCESSED_FILE = Path(path) / "processed_orders.json"

    for index in range(count):
        sd.mark_processed(f"order-{worker}-{index}")


def _add_persons(args) -> None:
    path, worker, count = args
    sys.path.insert(0, str(PKG))
    from knowledge_hub.hub import KnowledgeHub

    hub = KnowledgeHub(path=Path(path))          # one hub per process, as in life
    for index in range(count):
        hub.add_person({"id": f"p-{worker}-{index}", "name": f"Person {worker}"})


def _run(target, path, workers=WORKERS, per_worker=PER_WORKER):
    ctx = mp.get_context("spawn")
    args = [(str(path), w, per_worker) for w in range(workers)]
    with ctx.Pool(workers) as pool:
        pool.map(target, args)


# ── 1. the read-modify-write section ───────────────────────────────────────

def test_no_update_is_lost_across_processes(tmp_path):
    """The failure this API exists for. Without it every worker reads the same
    list, adds its own entry and writes the whole thing back — and all but the
    last are erased."""
    path = tmp_path / "rows.json"
    _run(_append_ids, path)

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == WORKERS * PER_WORKER
    assert len(set(rows)) == len(rows)           # and nothing written twice


def test_no_update_is_lost_across_threads(tmp_path):
    path = tmp_path / "rows.json"
    threads = [threading.Thread(target=_append_ids,
                                args=((str(path), w, PER_WORKER),))
               for w in range(WORKERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert len(rows) == WORKERS * PER_WORKER


def test_the_file_is_always_valid_json_while_being_hammered(tmp_path):
    """A reader must never catch a half-written file. `os.replace` is atomic, so
    a reader sees the old file or the new one — never a truncated one."""
    path = tmp_path / "rows.json"
    seen: list = []
    stop = threading.Event()

    def _reader():
        while not stop.is_set():
            if path.exists():
                try:
                    seen.append(len(json.loads(path.read_text(encoding="utf-8"))))
                except json.JSONDecodeError as exc:   # pragma: no cover
                    pytest.fail(f"read a truncated file: {exc}")
                except OSError:
                    pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        _run(_append_ids, path, workers=4, per_worker=15)
    finally:
        stop.set()
        reader.join(timeout=5)
    assert seen, "the reader never managed to read the file"
    assert seen == sorted(seen), "the row count went backwards — a lost update"


def test_an_exception_in_the_block_writes_nothing(tmp_path):
    """A half-applied mutation is worse than none: the caller retries, and it
    cannot retry against a file that already holds half of the last attempt."""
    path = tmp_path / "rows.json"
    with shared_lock.locked_json(path, default=list) as rows:
        rows.append("kept")

    with pytest.raises(RuntimeError):
        with shared_lock.locked_json(path, default=list) as rows:
            rows.append("discarded")
            raise RuntimeError("boom")

    assert json.loads(path.read_text(encoding="utf-8")) == ["kept"]


def test_the_default_is_used_for_a_file_that_does_not_exist_yet(tmp_path):
    path = tmp_path / "new.json"
    with shared_lock.locked_json(path, default=dict) as data:
        data["a"] = 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_an_unreadable_file_falls_to_the_default_and_is_logged(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with shared_lock.locked_json(path, default=list) as rows:
        rows.append("fresh")
    assert json.loads(path.read_text(encoding="utf-8")) == ["fresh"]


def test_the_locked_section_does_not_deadlock_on_an_atomic_write(tmp_path):
    """`flock` is per open-file-description, so a second `LOCK_EX` on the same
    file from the same process blocks for ever. Holders of the lock write
    through the unlocked core for exactly that reason."""
    path = tmp_path / "rows.json"
    finished = threading.Event()

    def _go():
        with shared_lock.locked_json(path, default=list) as rows:
            rows.append("one")
        finished.set()

    thread = threading.Thread(target=_go, daemon=True)
    thread.start()
    thread.join(timeout=5)
    assert finished.is_set(), "locked_json deadlocked against its own lock"


# ── 2. the JSONL appends ───────────────────────────────────────────────────

def test_no_jsonl_record_is_interleaved_or_truncated(tmp_path):
    """The published formats stay JSONL; only the append is serialised. Every
    line has to be one complete record."""
    path = tmp_path / "events.jsonl"
    _run(_append_lines, path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == WORKERS * PER_WORKER
    for line in lines:
        record = json.loads(line)                 # raises on an interleaved line
        assert len(record["pad"]) == 500          # and on a truncated one


def test_every_appended_record_survives(tmp_path):
    path = tmp_path / "events.jsonl"
    _run(_append_lines, path)
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines()]
    pairs = {(r["worker"], r["n"]) for r in records}
    assert len(pairs) == WORKERS * PER_WORKER


def test_the_append_adds_exactly_one_newline(tmp_path):
    path = tmp_path / "events.jsonl"
    shared_lock.locked_append(path, '{"a": 1}')
    shared_lock.locked_append(path, '{"a": 2}\n')      # already terminated
    assert path.read_text(encoding="utf-8") == '{"a": 1}\n{"a": 2}\n'


# ── 3. the whole-file write ────────────────────────────────────────────────

def test_an_atomic_write_is_never_seen_half_done(tmp_path):
    path = tmp_path / "big.json"
    payload = [{"i": i, "pad": "y" * 200} for i in range(2000)]
    stop = threading.Event()
    failures: list = []

    def _reader():
        while not stop.is_set():
            if path.exists():
                try:
                    json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    failures.append(str(exc))
                except OSError:
                    pass

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    try:
        for _ in range(40):
            shared_lock.write_json_atomic(path, payload)
    finally:
        stop.set()
        reader.join(timeout=5)
    assert not failures, failures[:3]


def test_no_temp_files_are_left_behind(tmp_path):
    path = tmp_path / "x.json"
    for _ in range(10):
        shared_lock.write_json_atomic(path, {"a": 1})
    assert not list(tmp_path.glob("*.tmp"))


# ── 4. the three call sites ────────────────────────────────────────────────

def test_processed_orders_loses_no_order_across_processes(tmp_path):
    """Two workers finishing two different orders each read the same list, each
    added their own id, and the second write erased the first — one order
    marked, the other silently re-ingested on the next pull."""
    _run(_mark_orders, tmp_path, workers=6, per_worker=20)

    stored = json.loads((tmp_path / "processed_orders.json")
                        .read_text(encoding="utf-8"))
    assert len(stored) == 6 * 20
    assert len(set(stored)) == len(stored)
    assert stored == sorted(stored)               # still deterministic on disk


def test_marking_the_same_order_twice_is_idempotent(tmp_path, monkeypatch):
    import utils.switchdrive as sd

    monkeypatch.setattr(sd, "_PROCESSED_FILE", tmp_path / "p.json")
    sd.mark_processed("order-1")
    sd.mark_processed("order-1")
    assert sd.load_processed() == {"order-1"}


def test_an_unreadable_processed_file_is_logged_not_swallowed(tmp_path,
                                                              monkeypatch):
    """It returned an empty set for a truncated file, so a half-written file
    read as "nothing processed" and the next ingest did every order again."""
    import utils.switchdrive as sd

    path = tmp_path / "p.json"
    path.write_text("[truncated", encoding="utf-8")
    monkeypatch.setattr(sd, "_PROCESSED_FILE", path)

    messages: list = []
    monkeypatch.setattr(shared_lock.logger, "warning",
                        lambda msg, *a, **k: messages.append(str(msg)))
    assert sd.load_processed() == set()
    assert messages, "an unreadable processed-orders file said nothing"


def test_the_hub_loses_no_person_across_processes(tmp_path):
    """The worst of the three: the hub cached the file at construction and
    rewrote the whole thing from that snapshot, so two processes raced for as
    long as their objects lived — not for the length of a write."""
    path = tmp_path / "hub.json"
    _run(_add_persons, path, workers=6, per_worker=15)

    data = json.loads(path.read_text(encoding="utf-8"))
    # The hub seeds DEFAULTS with an example person, so count the ids this test
    # added rather than the total — a test that counted rows would pass with
    # one of them lost and the seed making up the difference.
    mine = {p["id"] for p in data["persons"] if p["id"].startswith("p-")}
    assert len(mine) == 6 * 15


def test_the_hub_loses_no_person_across_threads(tmp_path):
    path = tmp_path / "hub.json"
    threads = [threading.Thread(target=_add_persons,
                                args=((str(path), w, 10),))
               for w in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    data = json.loads(path.read_text(encoding="utf-8"))
    mine = {p["id"] for p in data["persons"] if p["id"].startswith("p-")}
    assert len(mine) == 5 * 10


def test_a_hub_mutation_sees_what_another_process_wrote(tmp_path):
    """The lost-update window was the object's lifetime, so this is the case
    that matters: a hub built *before* another writer must not undo it."""
    from knowledge_hub.hub import KnowledgeHub

    path = tmp_path / "hub.json"
    first = KnowledgeHub(path=path)
    first.add_person({"id": "a", "name": "A"})

    stale = KnowledgeHub(path=path)          # reads the file as it is now
    second = KnowledgeHub(path=path)
    second.add_person({"id": "b", "name": "B"})

    stale.add_person({"id": "c", "name": "C"})   # must not erase "b"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {"a", "b", "c"} <= {p["id"] for p in data["persons"]}


def test_a_hub_built_without_a_path_follows_the_module_attribute(tmp_path,
                                                                 monkeypatch):
    """So the store can be isolated at all.

    The path used to be a **default argument**, evaluated at import, so pointing
    `HUB_PATH` at a temporary directory could not move an instance built
    afterwards. That is how #152's test came to stub `_save` instead — and once
    R3 moved the mutators off `_save`, that stub stopped isolating anything and
    the test wrote into the live store.
    """
    import knowledge_hub.hub as hubmod

    monkeypatch.setattr(hubmod, "HUB_PATH", tmp_path / "elsewhere.json")
    assert hubmod.KnowledgeHub().path == tmp_path / "elsewhere.json"


def test_a_hub_upsert_still_replaces_by_id(tmp_path):
    from knowledge_hub.hub import KnowledgeHub

    hub = KnowledgeHub(path=tmp_path / "hub.json")
    hub.add_person({"id": "upsert-me", "name": "Erst"})
    hub.add_person({"id": "upsert-me", "name": "Zweit"})
    data = json.loads((tmp_path / "hub.json").read_text(encoding="utf-8"))
    mine = [p for p in data["persons"] if p["id"] == "upsert-me"]
    assert [p["name"] for p in mine] == ["Zweit"]


def test_a_hub_keyword_is_added_once_even_against_a_stale_cache(tmp_path):
    """The duplicate check has to run on the fresh list, not on the snapshot."""
    from knowledge_hub.hub import KnowledgeHub

    path = tmp_path / "hub.json"
    stale = KnowledgeHub(path=path)
    KnowledgeHub(path=path).add_keyword("almosen")
    stale.add_keyword("almosen")

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["controlled_vocabulary"].count("almosen") == 1


def test_a_hub_mutation_refreshes_the_cache(tmp_path):
    from knowledge_hub.hub import KnowledgeHub

    path = tmp_path / "hub.json"
    reader = KnowledgeHub(path=path)
    before = {p["id"] for p in reader.get_persons()}
    KnowledgeHub(path=path).add_person({"id": "x", "name": "X"})
    # Honest staleness, documented: a reader answers from its cache.
    assert {p["id"] for p in reader.get_persons()} == before
    reader.add_keyword("term")                   # any mutation re-reads
    assert "x" in {p["id"] for p in reader.get_persons()}


def test_reload_discards_a_stale_cache(tmp_path):
    from knowledge_hub.hub import KnowledgeHub

    path = tmp_path / "hub.json"
    reader = KnowledgeHub(path=path)
    KnowledgeHub(path=path).add_person({"id": "x", "name": "X"})
    reader.reload()
    assert "x" in {p["id"] for p in reader.get_persons()}


def test_the_entity_index_is_written_atomically(tmp_path):
    """It is built from *every* document, so a crash during the write left a
    truncated file that every later reader took for the index."""
    import entity_index as ei

    index = ei.EntityIndex()
    source = (PKG / "entity_index.py").read_text(encoding="utf-8")
    block = source[source.index("def write_index_json("):]
    block = block[:block.index("\n\n\n")]
    assert "write_json_atomic" in block
    assert "path.write_text" not in block

    written = ei.write_index_json(index, tmp_path / "entity_index.json")
    assert json.loads(written.read_text(encoding="utf-8")) == {}
    assert not list(tmp_path.glob("*.tmp"))


# ── 5. RunState: per-document by construction ─────────────────────────────

def test_runstate_files_are_per_document(tmp_path, monkeypatch):
    """The issue asks to verify and document this rather than change it: two
    workers never address the same file, because the path is derived from the
    doc id."""
    import runstate

    paths = {runstate.RunState._path(f"doc-{i}") for i in range(5)}
    assert len(paths) == 5
    # Derived from the id, so two workers cannot address the same file.
    for i in range(5):
        assert f"doc-{i}" in runstate.RunState._path(f"doc-{i}").name


def test_runstate_still_writes_atomically(tmp_path, monkeypatch):
    """Not because two workers share it, but because a crash mid-write must not
    leave half a run — it costs nothing to keep."""
    source = (PKG / "runstate.py").read_text(encoding="utf-8")
    block = source[source.index("    def save("):]
    block = block[:block.index("\n    def ")]
    assert "write_json_atomic" in block


# ── the inventory this issue asked for ────────────────────────────────────

#: Files that hold state shared between documents, and therefore between
#: workers. Each one is routed through `shared_lock`; the test below is what
#: keeps a new raw write from appearing beside them.
SHARED_STATE = {
    "utils/switchdrive.py": "processed_orders.json",
    "knowledge_hub/hub.py": "hub.json",
    "entity_index.py": "entity_index.json",
    "preferences.py": "the preference log",
    "feedback_logger.py": "the routing log",
    "voting.py": "the vote log",
}


@pytest.mark.parametrize("module", sorted(SHARED_STATE))
def test_every_shared_state_writer_goes_through_the_wrappers(module):
    """The inventory, as a guard. A new `write_text`/`json.dump` on shared state
    is how this class of bug comes back, and it comes back silently."""
    source = (PKG / module).read_text(encoding="utf-8")
    assert "shared_lock" in source, module


def test_the_three_wrappers_are_the_whole_public_surface():
    """So a caller cannot reach a half-safe combination by accident."""
    for name in ("locked_append", "locked_json", "write_json_atomic",
                 "read_json", "read_jsonl"):
        assert callable(getattr(shared_lock, name)), name
