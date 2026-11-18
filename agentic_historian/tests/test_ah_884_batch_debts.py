"""#884 — a batch run that dies may not leave work behind that no later run picks up.

Observed on tei (``lassberg-proof``, ``lassberg-proof-2``, 16./17.11.2026):

1. a session that went away left three ``done`` documents whose outputs nobody
   committed: the closing publish lived in memory and never ran, and the resume
   published only what *it* finished;
2. Ctrl-C left two documents ``done`` whose ``pipeline.json`` had no reading,
   only ``cannot schedule new futures after shutdown`` per page — so no later run
   would read them again;
3. and that happened because Ctrl-C lands in the main thread, which left
   ``run_batch`` while the workers carried on in a dying interpreter.

Pinned here: the manifest records the publish, a resume pays what an earlier run
owed, a result without a reading is a failure, and an interrupted run waits for
its documents in flight, publishes them, and only then re-raises.

Offline: the pipeline and the publish are stubs. Run from the repo root:
    pytest agentic_historian/tests/test_ah_884_batch_debts.py
"""

from __future__ import annotations

import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import batch_runner as br          # noqa: E402
import config                      # noqa: E402
import corpus_manifest as cm       # noqa: E402
from utils.publish_github import BatchPublish   # noqa: E402


@pytest.fixture(autouse=True)
def _engines_are_up(monkeypatch):
    """The runner refuses to start without engines (#599); these tests are
    about the runner, so the preflight is answered rather than skipped."""
    import atr_engines
    monkeypatch.setattr(atr_engines, "check_sync",
                        lambda needed=None, **kw: atr_engines.Preflight(
                            needed=tuple(needed or atr_engines.PLANNED_ENGINES)))


@pytest.fixture(autouse=True)
def _data(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "d")
    cm.close()
    yield
    cm.close()


def _source(tmp_path, n):
    root = tmp_path / "f"
    root.mkdir()
    for i in range(n):
        (root / f"p{i}.jpg").write_bytes(b"\xff\xd8\xff")
    return br.inspect_source(root)


class _Publish:
    """A publish that commits whatever it is given, and remembers the calls."""

    def __init__(self, outcome="published"):
        self.calls: list = []
        self.outcome = outcome

    def __call__(self, ids, label):
        self.calls.append(list(ids))
        # As `publish_docs` does: `published` is filled while collecting, so a
        # commit that then fails still lists them. `outcome` is the judge.
        return BatchPublish(outcome=self.outcome, url="u" if self.outcome == "published" else None,
                            published=list(ids))


def _run(source, publish, *, pipeline=lambda *a: None, run_id="run", **kw):
    return br.run_batch(run_id, source, workers=1, pipeline=pipeline, publish=publish,
                        publish_every=0, sleep=lambda _s: None, heartbeat_every=0.01, **kw)


# ── the manifest records the publish ─────────────────────────────────────────

def test_published_at_is_set_by_a_publish_and_cleared_when_the_outputs_change():
    cm.register("r", ["a", "b", "c"])
    for d in ("a", "b", "c"):
        cm.claim("r", worker="w")
    cm.mark_done("r", "a")
    cm.mark_done("r", "b")
    assert [i.doc_id for i in cm.unpublished("r")] == ["a", "b"]   # c is not done

    assert cm.mark_published("r", ["a", "c", "a", ""]) == 1        # c is not done: untouched
    assert cm.item("r", "a").published_at is not None
    assert cm.item("r", "c").published_at is None
    assert [i.doc_id for i in cm.unpublished("r")] == ["b"]

    cm.requeue("r", ["a"])                                          # processed again …
    cm.claim("r", worker="w")
    cm.mark_done("r", "a")                                          # … new outputs
    assert cm.item("r", "a").published_at is None
    assert [i.doc_id for i in cm.unpublished("r")] == ["a", "b"]


def test_a_manifest_from_before_the_column_gets_it_and_owes_its_done_rows(tmp_path):
    path = cm.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(str(path))
    old.executescript("""
        CREATE TABLE manifest_runs (run_id TEXT PRIMARY KEY, source TEXT, label TEXT,
            registered_at TEXT NOT NULL, schema_version TEXT NOT NULL);
        CREATE TABLE manifest_items (run_id TEXT NOT NULL, doc_id TEXT NOT NULL,
            status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, worker TEXT,
            error TEXT, registered_at TEXT NOT NULL, claimed_at TEXT, heartbeat_at TEXT,
            started_at TEXT, finished_at TEXT, PRIMARY KEY (run_id, doc_id));
        INSERT INTO manifest_items (run_id, doc_id, status, attempts, registered_at)
            VALUES ('old', 'x', 'done', 1, '2026-11-16T00:00:00+00:00');
    """)
    old.commit()
    old.close()
    cm.close()
    assert cm.item("old", "x").published_at is None
    assert [i.doc_id for i in cm.unpublished("old")] == ["x"]


# ── a resume pays what the dead run owed ─────────────────────────────────────

def test_a_resume_publishes_what_a_killed_run_finished_and_never_published(tmp_path):
    source = _source(tmp_path, 5)
    ids = sorted(source.doc_ids)
    # The killed run: three finished, two still claimed, no publish ever ran.
    cm.register("run", ids)
    for d in ids:
        cm.claim("run", worker="dead", limit=1)
    for d in ids[:3]:
        cm.mark_done("run", d)
    # Its claims went stale, as they do after STALE_AFTER without a heartbeat.
    cm.connect().execute("UPDATE manifest_items SET heartbeat_at=NULL, "
                         "claimed_at='2000-01-01T00:00:00+00:00' WHERE status='running'")

    publish = _Publish()
    _run(source, publish)
    assert len(publish.calls) == 1, "one commit for the two new and the three owed"
    assert sorted(publish.calls[0]) == ids
    assert cm.unpublished("run") == []

    again = _Publish()
    _run(source, again)
    assert again.calls == [], "nothing is owed any more, so nothing is published"


def test_a_failed_publish_stays_owed_until_one_succeeds(tmp_path):
    source = _source(tmp_path, 2)
    failing = _Publish("failed")
    _run(source, failing)
    assert len(failing.calls) == 1
    assert len(cm.unpublished("run")) == 2

    later = _Publish("unchanged")          # the successful retry finds it all there
    _run(source, later)
    assert sorted(later.calls[0]) == sorted(source.doc_ids)
    assert cm.unpublished("run") == []


def test_only_what_the_publish_committed_is_marked(tmp_path):
    source = _source(tmp_path, 3)
    first, *rest = sorted(source.doc_ids)

    def publish(ids, label):
        return BatchPublish(outcome="published", url="u", published=[first],
                            unreadable={d: "nichts" for d in rest})
    _run(source, publish)
    assert sorted(i.doc_id for i in cm.unpublished("run")) == rest


def test_per_doc_publish_owes_nothing_to_the_batch_publish(tmp_path):
    source = _source(tmp_path, 2)
    publish = _Publish()
    _run(source, publish, per_doc_publish=True)
    assert publish.calls == []


# ── a result without a reading is a failure ──────────────────────────────────

INTERRUPTED = {"doc_id": "d", "transcription": "", "recognitions": [], "errors": [
    {"agent": "A", "page": "p_001.jpg", "error": "cannot schedule new futures after shutdown"},
    {"agent": "A", "page": "p_002.jpg",
     "error": "cannot schedule new futures after interpreter shutdown"}]}


@pytest.mark.parametrize("result, why", [
    (INTERRUPTED, "erster Fehler: cannot schedule new futures after shutdown"),
    ({"transcription": " ", "recognitions": [{"text": "", "error": "timeout"}], "errors": []},
     "1 Erkennung(en) ohne Text"),
])
def test_no_reading_is_named(result, why):
    got = br.no_reading(result)
    assert got and got.startswith("keine Lesung") and why in got


@pytest.mark.parametrize("result", [
    None,                                                       # a custom pipeline, no claim
    "text",
    {"transcription": "Lieber Freund", "errors": [{"error": "x"}]},
    {"transcription": "", "recognitions": [{"text": "", "error": "x"}, {"text": "Ein Wort"}]},
    {"transcription": "", "recognitions": [], "errors": []},    # nothing attempted
    {},
])
def test_a_reading_or_no_claim_is_not_no_reading(result):
    assert br.no_reading(result) is None


def test_a_document_without_a_reading_is_a_failure_and_not_published(tmp_path):
    """Retried within the run like any failure, and dead-lettered when it never
    yields a reading — but never `done`, which no later run would touch."""
    source = _source(tmp_path, 2)
    bad, good = sorted(source.doc_ids)

    def pipeline(doc_id, paths, mode):
        return INTERRUPTED if doc_id == bad else {"transcription": "Text", "errors": []}
    publish = _Publish()
    summary = _run(source, publish, pipeline=pipeline, max_attempts=3)

    row = cm.item("run", bad)
    assert row.status == cm.DEAD and row.attempts == 3
    assert row.error.startswith("NoReading: keine Lesung")
    assert bad in summary.failed and good in summary.done
    assert publish.calls == [[good]]


def test_a_document_without_a_reading_is_read_again(tmp_path):
    """The interrupted ensemble's document: the next attempt reads it."""
    source = _source(tmp_path, 1)
    (doc,) = source.doc_ids
    results = [INTERRUPTED, {"transcription": "Lieber Freund", "errors": []}]
    calls: list = []

    def pipeline(doc_id, paths, mode):
        calls.append(doc_id)
        return results[len(calls) - 1]
    publish = _Publish()
    _run(source, publish, pipeline=pipeline, max_attempts=3)
    assert calls == [doc, doc]
    assert cm.item("run", doc).status == cm.DONE
    assert publish.calls == [[doc]]


def test_run_batch_judges_what_its_default_pipeline_returns(tmp_path, monkeypatch):
    """Without a pipeline argument: the closure must pass the result on."""
    monkeypatch.setattr(br, "_default_pipeline", lambda d, p, m, **kw: INTERRUPTED)
    source = _source(tmp_path, 1)
    br.run_batch("run", source, workers=1, publish=_Publish(), max_attempts=1,
                 sleep=lambda _s: None, heartbeat_every=0.01)
    (doc,) = source.doc_ids
    assert cm.item("run", doc).status == cm.DEAD


def test_the_default_pipeline_hands_the_result_back(monkeypatch):
    import orchestrator
    monkeypatch.setattr(orchestrator, "run_full_pipeline", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(orchestrator, "run_full_pipeline_group", lambda *a, **k: {"y": 2})
    assert br._default_pipeline("d", [Path("/x/a.jpg")], br.PAGES) == {"x": 1}
    assert br._default_pipeline("d", [Path("/x/a.jpg")], br.ORDERS) == {"y": 2}


# ── Ctrl-C waits for the documents in flight ─────────────────────────────────

def test_ctrl_c_in_the_main_thread_waits_for_the_workers_then_publishes(tmp_path):
    """The interrupt is delivered to the main thread, which sits in ``join``.
    Before #884 it left ``run_batch`` at once: the worker finished in a dying
    interpreter, nothing was published, and the result was re-raised over a
    document still ``running``."""
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    source = _source(tmp_path, 3)
    first = sorted(source.doc_ids)[0]
    main = threading.main_thread().ident
    finished: list = []
    said: list = []

    def pipeline(doc_id, paths, mode):
        time.sleep(0.05)                                  # main is in join() by now
        signal.pthread_kill(main, signal.SIGINT)
        time.sleep(0.3)                                   # the document takes a while
        finished.append(doc_id)
        return {"transcription": "Text"}

    publish = _Publish()
    try:
        with pytest.raises(KeyboardInterrupt):
            _run(source, publish, pipeline=pipeline, announce=said.append)
        assert finished == [first], "run_batch re-raised before the worker was done"
    finally:
        signal.signal(signal.SIGINT, previous)

    assert cm.item("run", first).status == cm.DONE
    assert cm.progress("run").pending == 2, "nothing was claimed after the interrupt"
    assert publish.calls == [[first]], "the finished document was published before re-raising"
    assert cm.unpublished("run") == []
    assert any("abgebrochen" in s and first in s for s in said)
    assert not any("fertig nach" in s for s in said), "an interrupted run did not finish"
