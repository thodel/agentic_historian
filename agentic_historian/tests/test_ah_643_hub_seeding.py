"""#643: the hub's constructor wrote blind over whatever was in the file.

#393 put the hub's **mutators** into a critical section and left the
**constructor** out of it. `_load` ended in an unconditional
`self._write(copy.deepcopy(DEFAULTS))` whenever the file was absent, so two
hubs built moments apart against a fresh path raced — and the second one's
constructor erased what the first had already added. `write_json_atomic` made
that write atomic, which is a different property from correct: an atomic write
that does not know what it is overwriting still loses the other worker's work.

The #393 suite caught it as a ~40% flake (47–49 of 50 persons). These tests
catch it deterministically, which is the difference between a test that fails
sometimes and one that says what is broken.
"""

import json
import pathlib
import sys
import threading
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub import hub as hubmod       # noqa: E402
from shared_lock import locked_json           # noqa: E402


def _hub(path):
    return hubmod.KnowledgeHub(path=Path(path))


def _persons(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {p["id"] for p in data.get("persons", [])}


# ── Part 1: the constructor must not erase what is already there ─────────────

def test_a_new_hub_keeps_the_persons_already_in_the_file(tmp_path):
    """The race, made deterministic: the second hub is simply built later.

    No threads — the bug does not need them. A worker that starts a second later
    than another has exactly this ordering, every time.
    """
    path = tmp_path / "hub.json"
    first = _hub(path)
    first.add_person({"id": "p-already-here", "name": "Anna"})
    assert "p-already-here" in _persons(path), "precondition"

    _hub(path)                                   # a second worker starts up

    assert "p-already-here" in _persons(path), (
        "the constructor wrote DEFAULTS over the file that already had content")


def test_a_new_hub_keeps_every_other_collection_too(tmp_path):
    """Not only persons: the blind write replaced the whole document."""
    path = tmp_path / "hub.json"
    first = _hub(path)
    first.add_person({"id": "p-1", "name": "Anna"})
    first.add_place({"id": "l-1", "name": "Bern"})
    first.add_keyword("Fürsorge")

    _hub(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert {p["id"] for p in data["persons"]} >= {"p-1"}
    assert {p["id"] for p in data["places"]} >= {"l-1"}
    assert "Fürsorge" in json.dumps(data["controlled_vocabulary"],
                                     ensure_ascii=False)


def test_many_hubs_built_in_a_row_keep_everything(tmp_path):
    """Each constructor is one more chance to overwrite."""
    path = tmp_path / "hub.json"
    for i in range(8):
        _hub(path).add_person({"id": f"p-{i}", "name": f"P{i}"})
    assert _persons(path) >= {f"p-{i}" for i in range(8)}


def test_constructors_racing_on_a_fresh_path_lose_nothing(tmp_path):
    """The original symptom, with the threads it took to see it."""
    path = tmp_path / "hub.json"

    def work(worker):
        hub = _hub(path)
        for index in range(10):
            hub.add_person({"id": f"p-{worker}-{index}", "name": f"P{worker}"})

    threads = [threading.Thread(target=work, args=(w,)) for w in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    mine = {p for p in _persons(path) if p.startswith("p-")}
    assert len(mine) == 50, f"lost {50 - len(mine)} of 50 persons"


def test_the_constructor_never_writes_without_reading_under_the_lock(
        tmp_path, monkeypatch):
    """The invariant, not the symptom.

    Every timing-based test here can be satisfied by moving the blind write
    behind an ``if not self.path.exists()``: the window between that check and
    the write is microseconds wide, so a thread test passes almost always — and
    a test that passes almost always is worse than none. A mutation doing
    exactly that survived the whole suite until this test existed.

    So state the rule instead: ``_write`` replaces the file from an in-memory
    dict **without reading it**, which is the one thing the constructor must not
    do. Whatever it writes, it has to have read under the same lock.
    """
    path = tmp_path / "hub.json"
    blind: list = []
    monkeypatch.setattr(hubmod.KnowledgeHub, "_write",
                        lambda self, data: blind.append(data))

    _hub(path)

    assert blind == [], (
        "the constructor used _write, which overwrites whatever is in the file "
        "without having read it — atomic, and still a lost update")


def test_the_constructor_reads_through_the_critical_section(tmp_path, monkeypatch):
    """The other half: not writing blind is no good if it does not read at all."""
    import shared_lock

    seen: list = []
    real = shared_lock.locked_json

    def spy(path, default=None, coerce=None):
        seen.append(pathlib.Path(path).name)
        return real(path, default=default, coerce=coerce)

    monkeypatch.setattr(shared_lock, "locked_json", spy)
    _hub(tmp_path / "hub.json")

    assert "hub.json" in seen, (
        "the constructor did not go through locked_json")


def test_the_seed_still_creates_a_usable_hub_on_a_fresh_path(tmp_path):
    """The counter-test: seeding has to keep working, not just stop writing."""
    path = tmp_path / "hub.json"
    hub = _hub(path)
    assert path.exists(), "a fresh hub must still be written to disk"
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in hubmod.DEFAULTS:
        assert key in data, f"the seed left out {key!r}"
    hub.add_person({"id": "p-1", "name": "Anna"})
    assert "p-1" in _persons(path)


def test_a_missing_top_level_key_is_filled_in_without_touching_the_rest(tmp_path):
    """What the seed is actually for, once it can no longer overwrite."""
    path = tmp_path / "hub.json"
    path.write_text(json.dumps({"persons": [{"id": "p-1", "name": "Anna"}]}),
                    encoding="utf-8")

    _hub(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert {p["id"] for p in data["persons"]} == {"p-1"}, "existing key changed"
    for key in hubmod.DEFAULTS:
        assert key in data, f"missing key {key!r} was not filled in"


# ── Part 2: readable JSON of the wrong shape ─────────────────────────────────

WRONG_SHAPES = ['["not", "an", "object"]', '"a string"', '42', 'null', 'true']


@pytest.mark.parametrize("content", WRONG_SHAPES)
def test_a_hub_file_of_the_wrong_shape_does_not_raise(tmp_path, content):
    """``except (JSONDecodeError, OSError)`` never caught this: the file parses,
    and then ``data.setdefault`` raises ``AttributeError`` out of the
    constructor — taking down everything that builds a hub."""
    path = tmp_path / "hub.json"
    path.write_text(content, encoding="utf-8")
    hub = _hub(path)                             # must not raise
    assert isinstance(hub._data, dict)


@pytest.mark.parametrize("content", WRONG_SHAPES)
def test_a_hub_file_of_the_wrong_shape_is_repaired_on_disk(tmp_path, content):
    path = tmp_path / "hub.json"
    path.write_text(content, encoding="utf-8")
    _hub(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "the wrong shape survived on disk"
    for key in hubmod.DEFAULTS:
        assert key in data


def test_mutating_a_wrong_shape_file_writes_the_dict_not_the_original(tmp_path):
    """The rebinding bug: ``data = {}`` inside ``locked_json`` changed only the
    caller's name, so the file kept its corrupt content while the cache moved
    on — and nothing said the two had parted ways."""
    path = tmp_path / "hub.json"
    hub = _hub(path)
    path.write_text('["clobbered by another worker"]', encoding="utf-8")

    hub.add_person({"id": "p-1", "name": "Anna"})

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict), "the file still holds the wrong shape"
    assert "p-1" in {p["id"] for p in on_disk["persons"]}, (
        "the person went to the cache but not to the file")
    assert "p-1" in {p["id"] for p in hub._data["persons"]}
    assert on_disk["persons"] == hub._data["persons"], (
        "file and cache disagree after the mutation")


# ── Part 3: the coerce hook, which is what made part 2 fixable ───────────────

def test_coerce_result_is_what_gets_yielded(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('[1, 2, 3]', encoding="utf-8")
    with locked_json(path, default=dict, coerce=lambda v: {"from": v}) as data:
        assert data == {"from": [1, 2, 3]}


def test_coerce_result_is_what_gets_written(tmp_path):
    """The half that matters: a caller cannot replace the yielded object, so if
    coerce did not also change what is written, it would change nothing."""
    path = tmp_path / "x.json"
    path.write_text('[1, 2, 3]', encoding="utf-8")
    with locked_json(path, default=dict, coerce=lambda v: {"shape": "dict"}) as data:
        data["added"] = True
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "shape": "dict", "added": True}


def test_coerce_also_applies_to_the_default_for_a_missing_file(tmp_path):
    path = tmp_path / "missing.json"
    seen = []
    with locked_json(path, default=list, coerce=lambda v: seen.append(v) or {}) as data:
        data["ok"] = 1
    assert seen == [[]], "coerce must see the default when the file is absent"
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": 1}


def test_without_coerce_the_behaviour_is_unchanged(tmp_path):
    """Every existing caller passes no coerce and must be unaffected."""
    path = tmp_path / "x.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with locked_json(path, default=dict) as data:
        data["b"] = 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}


def test_an_exception_still_writes_nothing_with_coerce(tmp_path):
    """coerce must not have moved the write out of the happy path."""
    path = tmp_path / "x.json"
    path.write_text('{"a": 1}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        with locked_json(path, default=dict, coerce=lambda v: {"replaced": True}) as data:
            data["b"] = 2
            raise RuntimeError("no")
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}, (
        "a failed block wrote anyway")


# ── Part 4: the same hole in switchdrive ─────────────────────────────────────

def test_mark_processed_survives_a_file_of_the_wrong_shape(tmp_path, monkeypatch):
    """``done[:] = sorted(ids)`` needs a list; the file is written by every
    worker, so its shape is not this function's to assume."""
    from utils import switchdrive

    path = tmp_path / "processed_orders.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    monkeypatch.setattr(switchdrive, "_PROCESSED_FILE", path)

    switchdrive.mark_processed("order-1")        # must not raise

    assert json.loads(path.read_text(encoding="utf-8")) == ["order-1"]


def test_mark_processed_still_accumulates(tmp_path, monkeypatch):
    from utils import switchdrive

    path = tmp_path / "processed_orders.json"
    monkeypatch.setattr(switchdrive, "_PROCESSED_FILE", path)
    for order in ("b", "a", "c"):
        switchdrive.mark_processed(order)
    assert json.loads(path.read_text(encoding="utf-8")) == ["a", "b", "c"]


# ── Part 5: the claim in the docstring ───────────────────────────────────────

def test_flock_does_serialise_two_descriptions_in_one_process(tmp_path):
    """`_mutate`'s docstring said ``flock`` cannot serialise two threads of one
    process "because they share the open-file description". They do not share
    one: ``_acquire`` opens the lock file per call. The claim was wrong, and a
    wrong reason in a docstring is how the next person removes the right code.
    """
    import fcntl
    import os

    import shared_lock

    target = tmp_path / "d.json"
    lock_path = shared_lock._lock_path(target)
    first = shared_lock._acquire(lock_path)
    try:
        second = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(second)
    finally:
        shared_lock._release(first)


def test_the_hubs_thread_lock_is_per_instance(tmp_path):
    """Which is why it could not have covered the constructor race: the threads
    in that test each built their own hub, so they shared no lock."""
    path = tmp_path / "hub.json"
    assert _hub(path)._lock is not _hub(path)._lock
