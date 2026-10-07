"""#391 (R1): a corpus run that survives being interrupted.

Corpus state was a flat set of finished order ids plus one RunState per
document — enough for a serial worker, useless for resuming a 5,000-page run.
This is the checkpoint store R2 claims work from.

Four contracts, each with its own section:

**A claim is a transaction, and specifically BEGIN IMMEDIATE.** With a deferred
transaction two workers read the same pending row and both update it, so the
document runs twice. That is asserted against two real threads, and separately
against SQLite itself so the reason stays visible when the test fails.

**`running` can lie.** A worker killed mid-page leaves the assertion "someone is
on this" behind for ever, and a resume then skips exactly the document it exists
to pick up. Reclaiming is explicit, counts from the last heartbeat, and refuses
a timestamp it cannot read.

**The manifest does not replace RunState.** It carries doc_ids and no stage
information. Two stores that both claimed to know how far a document had got
would disagree the first time one was written and the other was not.

**Re-registering is the normal case.** A run is re-registered to resume it, and
a holding may have grown. Finished work keeps its status.

Offline, stdlib only. Run from the repo root:
    pytest agentic_historian/tests/test_ah_391_corpus_manifest.py
"""

import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                      # noqa: E402
import corpus_manifest as cm       # noqa: E402

RUN = "lassberg-2026-10"
DOCS = ["Lassberg_0012", "Lassberg_0013", "Lassberg_0014"]


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    cm.close()
    yield tmp_path
    cm.close()


def _registered(docs=DOCS, run_id=RUN, **kw):
    cm.register(run_id, docs, **kw)
    return run_id


def _ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


# ── registering ──────────────────────────────────────────────────────────────

def test_a_run_is_registered_with_its_documents():
    cm.register(RUN, DOCS, source="switchdrive:/Lassberg", label="Lassberg")
    prog = cm.progress(RUN)

    assert (prog.total, prog.pending) == (3, 3)
    assert [i.doc_id for i in cm.items(RUN)] == sorted(DOCS)
    assert cm.run(RUN).source == "switchdrive:/Lassberg"


def test_re_registering_keeps_finished_work():
    """A run is re-registered to resume it. Resetting finished rows to pending
    reprocesses a corpus somebody waited hours for."""
    _registered()
    cm.claim(RUN)
    cm.mark_done(RUN, sorted(DOCS)[0])

    cm.register(RUN, DOCS)                      # again, same list

    assert cm.item(RUN, sorted(DOCS)[0]).status == cm.DONE
    assert cm.progress(RUN).done == 1


def test_re_registering_adds_documents_that_appeared_since():
    _registered()
    cm.register(RUN, DOCS + ["Lassberg_0015"])

    assert cm.progress(RUN).total == 4
    assert cm.item(RUN, "Lassberg_0015").status == cm.PENDING


def test_a_duplicate_in_the_listing_is_one_row():
    """The source is a directory listing, and a listing that yields the same
    name twice must not become two rows that both get claimed."""
    cm.register(RUN, ["a", "a", " a ", "", "  ", "b"])

    assert [i.doc_id for i in cm.items(RUN)] == ["a", "b"]


def test_a_run_without_an_id_is_refused():
    with pytest.raises(ValueError):
        cm.register("", DOCS)


def test_registering_nothing_is_not_an_error():
    cm.register(RUN, [])

    assert cm.progress(RUN).total == 0
    assert cm.run(RUN) is not None


# ── the manifest does not replace RunState ───────────────────────────────────

def test_the_manifest_stores_no_stage_information():
    """RunState stays authoritative for gates and stages. Two stores that both
    claimed to know how far a document had got would disagree the first time one
    was written and the other was not."""
    _registered()
    columns = {r[1] for r in cm.connect().execute(
        "PRAGMA table_info(manifest_items)")}

    for stage_ish in ("stage", "stages", "stage_status", "artifacts",
                      "gate_decisions", "criteria", "transcription"):
        assert stage_ish not in columns, f"{stage_ish} belongs to RunState"
    assert "doc_id" in columns, "the reference to RunState is the doc_id"


# ── the picture survives a reopen ────────────────────────────────────────────

def test_the_picture_survives_closing_and_reopening():
    _registered()
    claimed = cm.claim(RUN, worker="w1")
    cm.mark_done(RUN, claimed[0].doc_id)
    cm.mark_failed(RUN, cm.claim(RUN, worker="w1")[0].doc_id, "gateway timed out")

    cm.close()                                   # as a killed process would

    prog = cm.progress(RUN)
    assert (prog.done, prog.failed, prog.pending) == (1, 1, 1)
    failed = cm.items(RUN, status=cm.FAILED)[0]
    assert failed.error == "gateway timed out"
    assert failed.attempts == 1


def test_a_second_connection_sees_committed_work():
    """WAL plus committed transactions: /status must be able to read while a
    worker writes."""
    _registered()
    cm.claim(RUN, worker="w1")

    other = sqlite3.connect(str(cm.db_path()))
    other.row_factory = sqlite3.Row
    rows = other.execute("SELECT status, COUNT(*) n FROM manifest_items "
                         "WHERE run_id=? GROUP BY status", (RUN,)).fetchall()
    other.close()

    assert {r["status"]: r["n"] for r in rows} == {cm.RUNNING: 1, cm.PENDING: 2}


def test_the_connection_follows_the_database_path(tmp_path, monkeypatch):
    """A cached connection that ignores the path it was opened for is the trap
    that makes a test silently read the previous test's database."""
    _registered()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "elsewhere")

    assert cm.progress(RUN).total == 0, "the old database was still open"
    assert cm.db_path().parent == tmp_path / "elsewhere"


# ── claiming is atomic ───────────────────────────────────────────────────────

def test_a_claim_moves_the_document_to_running_and_counts_the_attempt():
    _registered()
    got = cm.claim(RUN, worker="w1")

    assert len(got) == 1
    assert got[0].status == cm.RUNNING
    assert got[0].attempts == 1 and got[0].worker == "w1"
    assert got[0].claimed_at and got[0].started_at and got[0].heartbeat_at


def test_a_claim_never_hands_out_the_same_document_twice():
    _registered()
    first = cm.claim(RUN, worker="w1", limit=3)
    second = cm.claim(RUN, worker="w2", limit=3)

    assert len(first) == 3 and second == []


def test_two_threads_never_claim_the_same_document():
    """The acceptance test of #391.

    The barrier is inside the loop, not before it: syncing only the start lets
    the first thread drain the whole manifest before the second wakes, and a
    test with no overlap cannot catch a race. Here both threads call `claim`
    at the same instant, once per round, for as many rounds as there are pairs
    of documents — so a deferred transaction has every chance to hand the same
    row to both.
    """
    docs = [f"doc-{i:03d}" for i in range(60)]
    cm.register(RUN, docs)
    rounds = len(docs) // 2
    taken: dict[str, list] = {"w1": [], "w2": []}
    barrier = threading.Barrier(2)
    errors: list = []

    def worker(name):
        # Each thread opens its own connection (threading.local).
        try:
            for _ in range(rounds):
                barrier.wait(timeout=20)
                got = cm.claim(RUN, worker=name, limit=1)
                taken[name].extend(i.doc_id for i in got)
        except Exception as e:                   # noqa: BLE001
            errors.append(f"{name}: {e!r}")
            barrier.abort()

    threads = [threading.Thread(target=worker, args=(n,)) for n in taken]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    everything = taken["w1"] + taken["w2"]
    assert not errors, errors
    assert len(everything) == len(set(everything)), "a document was claimed twice"
    assert sorted(everything) == sorted(docs)
    assert taken["w1"] and taken["w2"], "no overlap — the test proves nothing"


def test_sqlite_itself_shows_why_begin_immediate_is_required():
    """Pinned against SQLite directly so the reason stays visible: with a
    deferred transaction both workers see the same pending row, and the second
    UPDATE succeeds, so the document is processed twice."""
    path = cm.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    a = sqlite3.connect(str(path), isolation_level=None)
    a.execute("PRAGMA journal_mode=WAL")
    a.execute("CREATE TABLE q(id INTEGER PRIMARY KEY, status TEXT)")
    a.execute("INSERT INTO q VALUES (1,'pending')")
    b = sqlite3.connect(str(path), isolation_level=None)
    b.execute("PRAGMA busy_timeout=50")

    a.execute("BEGIN")
    a.execute("SELECT id FROM q WHERE status='pending'").fetchall()
    both_saw_it = bool(b.execute("SELECT id FROM q WHERE status='pending'")
                       .fetchall())
    a.execute("ROLLBACK")

    a.execute("BEGIN IMMEDIATE")
    a.execute("SELECT id FROM q WHERE status='pending'").fetchall()
    with pytest.raises(sqlite3.OperationalError):
        b.execute("BEGIN IMMEDIATE")
    a.execute("ROLLBACK")
    a.close()
    b.close()

    assert both_saw_it, "deferred transactions no longer race — revisit claim()"


def test_a_retry_is_claimed_after_untouched_work():
    """Without ordering by attempts, one poisoned document is retried ahead of
    untouched work for the rest of the run."""
    _registered()
    poisoned = cm.claim(RUN, worker="w1")[0].doc_id
    cm.mark_failed(RUN, poisoned, "boom")

    order = [cm.claim(RUN, worker="w1")[0].doc_id for _ in range(3)]

    assert order[-1] == poisoned


def test_a_done_document_is_never_claimed_again():
    _registered()
    cm.mark_done(RUN, cm.claim(RUN)[0].doc_id)

    assert cm.progress(RUN).done == 1
    assert all(i.status != cm.DONE for i in cm.claim(RUN, limit=5))


def test_a_dead_document_is_never_claimed_again():
    """Otherwise the dead letter is not one."""
    _registered()
    doc = cm.claim(RUN)[0].doc_id
    cm.mark_failed(RUN, doc, "boom", max_attempts=1)

    assert cm.item(RUN, doc).status == cm.DEAD
    assert doc not in [i.doc_id for i in cm.claim(RUN, limit=5)]


def test_a_failed_document_is_claimed_again_because_that_is_retrying():
    _registered()
    doc = cm.claim(RUN)[0].doc_id
    cm.mark_failed(RUN, doc, "transient")

    assert cm.item(RUN, doc).status == cm.FAILED
    again = cm.claim(RUN, limit=5)

    assert doc in [i.doc_id for i in again], "a failed document was not retried"
    assert cm.item(RUN, doc).attempts == 2, "the retry was not counted"


def test_claiming_an_unknown_run_is_empty_not_an_error():
    assert cm.claim("no-such-run") == []
    assert cm.progress("no-such-run").total == 0


# ── the dead letter ──────────────────────────────────────────────────────────

def test_attempts_run_out_into_the_dead_letter():
    _registered(["one"])
    for expected in (cm.FAILED, cm.FAILED, cm.DEAD):
        assert cm.claim(RUN), "a claimable document stopped being claimable"
        assert cm.mark_failed(RUN, "one", "boom",
                              max_attempts=3).status == expected


def test_the_ceiling_is_the_callers_because_the_policy_is_r2s():
    _registered(["one"])
    cm.claim(RUN)

    assert cm.mark_failed(RUN, "one", "boom", max_attempts=1).status == cm.DEAD


def test_attempts_counts_starts_not_failures():
    """A row at attempts=3, status=pending has been handed out three times."""
    _registered(["one"])
    cm.claim(RUN)
    cm.mark_failed(RUN, "one", "boom")
    cm.claim(RUN)

    assert cm.item(RUN, "one").attempts == 2


def test_an_empty_error_is_stored_as_absence():
    """"No failure recorded" and "failed with an empty message" are different
    facts, and only the first is common."""
    _registered(["one"])
    cm.claim(RUN)

    assert cm.mark_failed(RUN, "one", "").error is None
    assert cm.mark_failed(RUN, "one", "   ").error is None


def test_succeeding_clears_an_earlier_error():
    _registered(["one"])
    cm.claim(RUN)
    cm.mark_failed(RUN, "one", "transient")
    cm.claim(RUN)

    assert cm.mark_done(RUN, "one").error is None


def test_marking_an_unknown_document_is_none_not_a_crash():
    _registered()

    assert cm.mark_failed(RUN, "nope", "boom") is None
    assert cm.mark_done(RUN, "nope") is None


def test_requeue_brings_back_failed_and_dead():
    """The way back from the dead letter — "resume after fixing the bug" is the
    most ordinary thing an operator will want."""
    _registered()
    docs = sorted(DOCS)
    cm.claim(RUN, limit=2)
    cm.mark_failed(RUN, docs[0], "boom", max_attempts=1)     # dead
    cm.mark_failed(RUN, docs[1], "boom")                     # failed

    back = cm.requeue(RUN)

    assert {i.doc_id for i in back} == set(docs)
    assert all(cm.item(RUN, d).attempts == 0 for d in docs[:2])
    assert cm.progress(RUN).pending == 3


def test_requeue_leaves_finished_work_alone_whatever_is_asked():
    """Re-running a finished document is a different request, and quietly
    granting it here would let a typo cost a corpus."""
    _registered()
    doc = cm.claim(RUN)[0].doc_id
    cm.mark_done(RUN, doc)

    cm.requeue(RUN, [doc])

    assert cm.item(RUN, doc).status == cm.DONE


def test_requeue_can_name_single_documents():
    _registered()
    docs = sorted(DOCS)
    cm.claim(RUN, limit=2)
    cm.mark_failed(RUN, docs[0], "boom")
    cm.mark_failed(RUN, docs[1], "boom")

    cm.requeue(RUN, [docs[0]])

    assert cm.item(RUN, docs[0]).status == cm.PENDING
    assert cm.item(RUN, docs[1]).status == cm.FAILED


def test_requeue_of_an_empty_list_touches_nothing():
    """`None` means "everything"; `[]` means "these, of which there are none".
    Collapsing them would make a caller with nothing to requeue requeue all."""
    _registered()
    cm.claim(RUN)
    cm.mark_failed(RUN, sorted(DOCS)[0], "boom")

    assert cm.requeue(RUN, []) == []
    assert cm.item(RUN, sorted(DOCS)[0]).status == cm.FAILED


# ── running can lie ──────────────────────────────────────────────────────────

def test_a_stale_claim_is_handed_back():
    """A worker killed mid-page leaves a running row that no resume would ever
    pick up — which defeats the one thing #391 exists for."""
    _registered(["one"])
    cm.claim(RUN, worker="dead-worker")

    reclaimed = cm.reclaim_stale(RUN, older_than=60,
                                 now=datetime.now(timezone.utc)
                                 + timedelta(seconds=120))

    assert [i.doc_id for i in reclaimed] == ["one"]
    assert cm.item(RUN, "one").status == cm.FAILED
    assert "stale" in cm.item(RUN, "one").error


def test_a_live_claim_is_left_alone():
    _registered(["one"])
    cm.claim(RUN, worker="w1")

    assert cm.reclaim_stale(RUN, older_than=3600) == []
    assert cm.item(RUN, "one").status == cm.RUNNING


def test_a_heartbeat_keeps_a_slow_document_alive():
    """A 42 s fusion page and a twenty-minute order are both normal. Measuring
    staleness from the claim rather than the last sign of life reclaims live
    work and runs the document twice."""
    _registered(["one"])
    cm.claim(RUN, worker="w1")
    cm.connect().execute(
        "UPDATE manifest_items SET claimed_at=?, started_at=? WHERE doc_id=?",
        (_ago(9000).isoformat(), _ago(9000).isoformat(), "one"))
    assert cm.heartbeat(RUN, "one", worker="w1")

    assert cm.reclaim_stale(RUN, older_than=600) == []
    assert cm.item(RUN, "one").status == cm.RUNNING


def test_a_heartbeat_on_a_reclaimed_claim_says_no():
    """How a worker finds out its claim was taken away while it was busy,
    instead of finishing over somebody else's work."""
    _registered(["one"])
    cm.claim(RUN, worker="w1")
    cm.reclaim_stale(RUN, older_than=0)

    assert cm.heartbeat(RUN, "one", worker="w1") is False


def test_a_heartbeat_from_another_worker_says_no():
    _registered(["one"])
    cm.claim(RUN, worker="w1")

    assert cm.heartbeat(RUN, "one", worker="w2") is False
    assert cm.heartbeat(RUN, "one", worker="w1") is True


def test_an_unreadable_timestamp_is_not_reclaimed():
    """Of the two errors available — one document stranded as running where an
    operator can see it, or the same document silently running twice — only the
    first is recoverable by a human."""
    _registered(["one"])
    cm.claim(RUN, worker="w1")
    cm.connect().execute(
        "UPDATE manifest_items SET claimed_at='gestern', heartbeat_at=NULL "
        "WHERE doc_id=?", ("one",))

    assert cm.reclaim_stale(RUN, older_than=0) == []
    assert cm.item(RUN, "one").status == cm.RUNNING


def test_a_repeatedly_stale_document_reaches_the_dead_letter():
    """A document that kills its worker every time must not loop for ever."""
    _registered(["one"])
    for _ in range(3):
        cm.claim(RUN, worker="w")
        cm.reclaim_stale(RUN, older_than=0, max_attempts=3)

    assert cm.item(RUN, "one").status == cm.DEAD


def test_reclaiming_spans_every_run_when_none_is_named():
    cm.register("run-a", ["a"])
    cm.register("run-b", ["b"])
    cm.claim("run-a")
    cm.claim("run-b")

    assert len(cm.reclaim_stale(older_than=0)) == 2


# ── progress ─────────────────────────────────────────────────────────────────

def test_the_parts_always_sum_to_the_whole():
    """A progress report whose parts do not sum to its total is quietly losing
    documents, and that is the defect a reader would never spot."""
    _registered(DOCS + ["d", "e"])
    cm.claim(RUN, limit=4)
    docs = sorted(DOCS + ["d", "e"])
    cm.mark_done(RUN, docs[0])
    cm.mark_failed(RUN, docs[1], "boom")
    cm.mark_failed(RUN, docs[2], "boom", max_attempts=1)
    prog = cm.progress(RUN)

    assert (prog.done, prog.failed, prog.dead, prog.running, prog.pending) == \
        (1, 1, 1, 1, 1)
    assert prog.counted == prog.total == 5


def test_an_unknown_status_shows_up_in_the_total_rather_than_vanishing():
    _registered(["one"])
    cm.connect().execute("UPDATE manifest_items SET status='haunted'")
    prog = cm.progress(RUN)

    assert prog.total == 1
    assert prog.counted == 0, "an unknown status must not be miscounted as known"


def test_terminal_and_outstanding_split_the_run():
    _registered()
    docs = sorted(DOCS)
    cm.claim(RUN, limit=2)
    cm.mark_done(RUN, docs[0])
    cm.mark_failed(RUN, docs[1], "boom", max_attempts=1)
    prog = cm.progress(RUN)

    assert prog.terminal == 2 and prog.outstanding == 1
    assert not prog.complete


def test_a_run_is_complete_when_nothing_is_outstanding():
    _registered(["one"])
    cm.claim(RUN)
    cm.mark_done(RUN, "one")

    assert cm.progress(RUN).complete
    assert cm.progress(RUN).percent == 1.0


def test_a_dead_document_counts_as_finished_for_completion():
    """Nothing will touch it again, so a run full of dead letters is over — and
    the report says `dead`, so it does not read as success."""
    _registered(["one"])
    cm.claim(RUN)
    cm.mark_failed(RUN, "one", "boom", max_attempts=1)
    prog = cm.progress(RUN)

    assert prog.complete and prog.dead == 1 and prog.done == 0


def test_percent_is_none_for_an_empty_manifest():
    """"Nothing is registered" and "nothing is finished" are different answers,
    and 0% of nothing reads as a stalled run."""
    cm.register(RUN, [])

    assert cm.progress(RUN).percent is None
    assert not cm.progress(RUN).complete


# ── timings ──────────────────────────────────────────────────────────────────

def test_an_unfinished_document_has_no_duration():
    """None rather than 0.0: it does not have a duration of nothing."""
    _registered(["one"])

    assert cm.item(RUN, "one").duration is None
    cm.claim(RUN)
    assert cm.item(RUN, "one").duration is None


def test_a_finished_document_is_timed():
    _registered(["one"])
    cm.claim(RUN)
    cm.connect().execute(
        "UPDATE manifest_items SET started_at=? WHERE doc_id=?",
        (_ago(12).isoformat(), "one"))
    cm.mark_done(RUN, "one")

    assert 11 <= cm.item(RUN, "one").duration <= 14


def test_a_retry_resets_the_clock_to_the_latest_attempt():
    """Only the latest attempt is timed; a per-attempt history would be another
    table and nothing in R2 asks for one."""
    _registered(["one"])
    cm.claim(RUN)
    cm.mark_failed(RUN, "one", "boom")
    assert cm.item(RUN, "one").finished_at

    cm.claim(RUN)

    assert cm.item(RUN, "one").finished_at is None
    assert cm.item(RUN, "one").duration is None


def test_an_unreadable_timestamp_gives_no_duration():
    _registered(["one"])
    cm.claim(RUN)
    cm.connect().execute(
        "UPDATE manifest_items SET started_at='irgendwann', finished_at=? "
        "WHERE doc_id=?", (_ago(1).isoformat(), "one"))

    assert cm.item(RUN, "one").duration is None


# ── runs ─────────────────────────────────────────────────────────────────────

def test_runs_are_listed_newest_first():
    cm.register("older", ["a"])
    cm.connect().execute(
        "UPDATE manifest_runs SET registered_at='2020-01-01T00:00:00+00:00' "
        "WHERE run_id='older'")
    cm.register("newer", ["b"])

    assert [r.run_id for r in cm.runs()] == ["newer", "older"]


def test_an_unknown_run_is_none():
    assert cm.run("nope") is None
    assert cm.items("nope") == []
