"""#392 (R2): process a corpus outside the Discord queue.

At 2–5 minutes a page, 5,000 pages through one serialised queue are one to three
weeks of uninterruptible runtime, and a restart halfway through used to mean
starting over. R1 (#391) gave the corpus a manifest that survives being
interrupted; this claims from it with N workers.

Four contracts, each with its own section:

**The same code path as `/run`.** Documents go through `run_full_pipeline`,
orders through `run_full_pipeline_group` — the functions the bot calls. A second
pipeline would drift, and the drift would show as a corpus processed differently
from the pages anybody had looked at.

**One flaky document never stalls the rest**, and a document that exhausts its
attempts goes to the dead letter while the run carries on.

**Resumable.** A re-run processes only `pending`/`failed`, and the orphaned
claims of a killed run are handed back once, at the start.

**The mode is derived, and the ambiguous case is refused.** A folder with both
loose pages and subfolders of pages would silently lose one or the other.

Offline — the pipeline is a stub, no engines, no Discord. Run from the repo root:
    pytest agentic_historian/tests/test_ah_392_batch_runner.py
"""

import sys
import threading
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import batch_runner as br      # noqa: E402
import config                  # noqa: E402
import corpus_manifest as cm   # noqa: E402

RUN = "lassberg"


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    cm.close()
    yield tmp_path
    cm.close()


def _pages(root, names=("001r.jpg", "002v.jpg", "010r.jpg")):
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        (root / name).write_bytes(b"\xff\xd8\xff")
    return root


def _orders(root, orders=("saa-0428", "saa-0429")):
    root.mkdir(parents=True, exist_ok=True)
    for order in orders:
        _pages(root / order, ("001r.jpg", "002v.jpg"))
    return root


def _runner(source, *, pipeline, workers=2, **kw):
    kw.setdefault("sleep", lambda _s: None)
    kw.setdefault("heartbeat_every", 0.01)
    return br.run_batch(RUN, source, workers=workers, pipeline=pipeline, **kw)


# ── the mode is derived, and the ambiguous case is refused ───────────────────

def test_loose_pages_are_one_document_each(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "flat"))

    assert source.mode == br.PAGES
    assert source.doc_ids == ["001r", "002v", "010r"]
    assert source.pages == 3
    assert "no subfolder" in source.why


def test_subfolders_are_one_document_each(tmp_path):
    source = br.inspect_source(_orders(tmp_path / "bestand"))

    assert source.mode == br.ORDERS
    assert source.doc_ids == ["saa-0428", "saa-0429"]
    assert source.pages == 4


def test_pages_within_an_order_are_in_natural_order(tmp_path):
    root = tmp_path / "b"
    _pages(root / "o", ("p2.jpg", "p10.jpg", "p1.jpg"))
    source = br.inspect_source(root)

    assert [p.name for p in source.paths["o"]] == ["p1.jpg", "p2.jpg", "p10.jpg"]


def test_a_folder_that_is_both_is_refused(tmp_path):
    """Processing it as pages would ignore the folders; as orders it would
    ignore the loose pages. Both silent, so neither is allowed."""
    root = _orders(tmp_path / "mixed")
    (root / "stray.jpg").write_bytes(b"\xff\xd8\xff")

    with pytest.raises(br.AmbiguousSource) as got:
        br.inspect_source(root)

    assert "--mode" in str(got.value)


def test_an_explicit_mode_settles_it(tmp_path):
    root = _orders(tmp_path / "mixed")
    (root / "stray.jpg").write_bytes(b"\xff\xd8\xff")

    assert br.inspect_source(root, mode=br.PAGES).doc_ids == ["stray"]
    assert br.inspect_source(root, mode=br.ORDERS).doc_ids == ["saa-0428",
                                                               "saa-0429"]


def test_an_unknown_mode_is_refused(tmp_path):
    with pytest.raises(RuntimeError):
        br.inspect_source(_pages(tmp_path / "f"), mode="seiten")


def test_a_folder_of_nothing_ingestible_is_empty_not_an_error(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    (root / "notes.txt").write_text("x")
    source = br.inspect_source(root)

    assert source.doc_ids == [] and source.pages == 0


def test_a_missing_folder_says_so(tmp_path):
    with pytest.raises(RuntimeError):
        br.inspect_source(tmp_path / "nope")


def test_the_derivation_is_reported(tmp_path):
    """A surprising run has to be diagnosable from the log."""
    assert br.inspect_source(_pages(tmp_path / "f")).why
    assert br.inspect_source(_pages(tmp_path / "g"), mode=br.PAGES).why == \
        "--mode pages"


# ── the same code path as /run ───────────────────────────────────────────────

def test_the_default_pipeline_calls_the_bots_own_entry_points(monkeypatch):
    """A second pipeline for batch work would drift from the interactive one."""
    import orchestrator
    seen = []
    monkeypatch.setattr(orchestrator, "run_full_pipeline",
                        lambda p, *a, **k: seen.append(("single", p)))
    monkeypatch.setattr(orchestrator, "run_full_pipeline_group",
                        lambda d, p, *a, **k: seen.append(("group", d, len(p))))

    br._default_pipeline("doc", [Path("/x/001r.jpg")], br.PAGES)
    br._default_pipeline("order", [Path("/x/a.jpg"), Path("/x/b.jpg")], br.ORDERS)

    assert seen == [("single", "/x/001r.jpg"), ("group", "order", 2)]


# ── draining, concurrently ───────────────────────────────────────────────────

def test_workers_drain_the_manifest_and_each_document_runs_once(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", tuple(
        f"p{i:03d}.jpg" for i in range(40))))
    ran: list = []
    guard = threading.Lock()

    def pipeline(doc_id, paths, mode):
        with guard:
            ran.append(doc_id)

    summary = _runner(source, pipeline=pipeline, workers=4)

    assert sorted(ran) == sorted(source.doc_ids)
    assert len(ran) == len(set(ran)), "a document was processed twice"
    assert summary.progress.done == 40 and summary.progress.complete


def test_more_than_one_worker_actually_works(tmp_path):
    """Vacuity guard: if one worker drained everything, the concurrency is
    untested and so is every claim above."""
    source = br.inspect_source(_pages(tmp_path / "f", tuple(
        f"p{i:03d}.jpg" for i in range(30))))
    workers: set = set()
    guard = threading.Lock()
    gate = threading.Barrier(3, timeout=20)

    def pipeline(doc_id, paths, mode):
        with guard:
            workers.add(threading.current_thread().name)
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass

    _runner(source, pipeline=pipeline, workers=3)

    assert len(workers) == 3, f"only {workers} did any work"


def test_the_summary_records_who_finished_what(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", ("a.jpg", "b.jpg")))
    summary = _runner(source, pipeline=lambda *a: None, workers=1)

    assert set(summary.done) == {"a", "b"}
    assert all(w.startswith("w") for w in summary.done.values())
    assert summary.seconds >= 0 and summary.mode == br.PAGES


# ── one flaky document never stalls the rest ─────────────────────────────────

def test_a_failing_document_does_not_stop_the_run(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", (
        "good1.jpg", "bad.jpg", "good2.jpg")))

    def pipeline(doc_id, paths, mode):
        if doc_id == "bad":
            raise RuntimeError("corrupt image")

    summary = _runner(source, pipeline=pipeline, workers=1, max_attempts=1)

    assert set(summary.done) == {"good1", "good2"}
    assert "bad" in summary.failed and "corrupt image" in summary.failed["bad"]
    assert cm.item(RUN, "bad").status == cm.DEAD


def test_an_exhausted_document_goes_dead_with_its_error_kept(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", ("bad.jpg",)))

    def pipeline(doc_id, paths, mode):
        raise ValueError("gateway said no")

    _runner(source, pipeline=pipeline, workers=1, max_attempts=2)
    item = cm.item(RUN, "bad")

    assert item.status == cm.DEAD and item.attempts == 2
    assert "gateway said no" in item.error and "ValueError" in item.error


def test_a_document_that_fails_once_is_retried_and_can_succeed(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", ("flaky.jpg",)))
    calls = {"n": 0}

    def pipeline(doc_id, paths, mode):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient")

    _runner(source, pipeline=pipeline, workers=1, max_attempts=3)

    assert calls["n"] == 2
    assert cm.item(RUN, "flaky").status == cm.DONE


def test_the_worker_backs_off_by_the_documents_attempt_count(tmp_path):
    """Otherwise a worker burns every attempt in milliseconds against a gateway
    that is down."""
    source = br.inspect_source(_pages(tmp_path / "f", ("bad.jpg",)))
    slept: list = []

    def pipeline(doc_id, paths, mode):
        raise RuntimeError("down")

    _runner(source, pipeline=pipeline, workers=1, max_attempts=3,
            sleep=slept.append)

    assert slept == [br.backoff(1), br.backoff(2), br.backoff(3)]
    assert slept == [30.0, 60.0, 120.0]


def test_the_backoff_is_capped():
    assert br.backoff(99) == br.BACKOFF_CAP_S
    assert br.backoff(0) == 0.0


def test_a_keyboard_interrupt_hands_the_claim_back_and_stops(tmp_path):
    """A killed run must not leave a document looking like work in progress
    that nothing will pick up."""
    source = br.inspect_source(_pages(tmp_path / "f", ("a.jpg", "b.jpg")))

    def pipeline(doc_id, paths, mode):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _runner(source, pipeline=pipeline, workers=1)

    assert cm.progress(RUN).running == 0, "a claim was left in flight"


# ── resumable ────────────────────────────────────────────────────────────────

def test_a_rerun_skips_what_is_done(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", ("a.jpg", "b.jpg")))
    _runner(source, pipeline=lambda *a: None, workers=1)

    again: list = []
    _runner(source, pipeline=lambda d, *a: again.append(d), workers=1)

    assert again == [], "finished documents were processed again"
    assert cm.progress(RUN).done == 2


def test_a_rerun_picks_up_what_failed(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", ("a.jpg", "bad.jpg")))
    fail = {"on": True}

    def pipeline(doc_id, paths, mode):
        if doc_id == "bad" and fail["on"]:
            raise RuntimeError("transient")

    _runner(source, pipeline=pipeline, workers=1, max_attempts=1)
    assert cm.item(RUN, "bad").status == cm.DEAD

    fail["on"] = False
    cm.requeue(RUN)
    _runner(source, pipeline=pipeline, workers=1)

    assert cm.progress(RUN).done == 2


def test_orphaned_claims_of_a_killed_run_are_handed_back_at_the_start(tmp_path):
    """Without this, a document a killed worker was holding is `running` for
    ever and no resume picks it up — which defeats what R1 exists for."""
    source = br.inspect_source(_pages(tmp_path / "f", ("a.jpg",)))
    cm.register(RUN, source.doc_ids)
    cm.claim(RUN, worker="a-worker-that-died")
    cm.connect().execute(
        "UPDATE manifest_items SET claimed_at='2000-01-01T00:00:00+00:00', "
        "heartbeat_at=NULL WHERE doc_id='a'")
    said: list = []

    _runner(source, pipeline=lambda *a: None, workers=1, announce=said.append)

    assert cm.progress(RUN).done == 1
    assert any("zurückgeholt" in s for s in said)


def test_registering_is_additive_so_a_grown_bestand_resumes(tmp_path):
    root = _pages(tmp_path / "f", ("a.jpg",))
    _runner(br.inspect_source(root), pipeline=lambda *a: None, workers=1)

    (root / "b.jpg").write_bytes(b"\xff\xd8\xff")
    done: list = []
    _runner(br.inspect_source(root), pipeline=lambda d, *a: done.append(d),
            workers=1)

    assert done == ["b"]
    assert cm.progress(RUN).done == 2


# ── the heartbeat keeps a slow document alive ────────────────────────────────

def test_a_slow_document_is_not_reclaimed_while_it_is_being_worked_on(tmp_path):
    """A worker inside the pipeline cannot beat its own heartbeat — the call
    blocks for minutes — so one daemon thread beats for every claim in flight.
    Without it the runner is correct until the first order over 30 minutes."""
    source = br.inspect_source(_pages(tmp_path / "f", ("slow.jpg",)))
    beats: list = []
    started = threading.Event()

    def pipeline(doc_id, paths, mode):
        started.set()
        for _ in range(200):
            if len(beats) >= 2:
                return
            time.sleep(0.01)

    real = cm.heartbeat

    def spy(run_id, doc_id, **kw):
        ok = real(run_id, doc_id, **kw)
        beats.append((doc_id, kw.get("worker"), ok))
        return ok

    cm.heartbeat = spy
    try:
        _runner(source, pipeline=pipeline, workers=1, heartbeat_every=0.02)
    finally:
        cm.heartbeat = real

    assert started.is_set()
    assert len(beats) >= 2, "the heartbeat never fired"
    assert all(ok for _d, _w, ok in beats), "a heartbeat was refused"
    assert {d for d, _w, _o in beats} == {"slow"}


# ── progress, at batch granularity ───────────────────────────────────────────

def test_progress_is_announced_every_n_documents_not_every_step(tmp_path):
    source = br.inspect_source(_pages(tmp_path / "f", tuple(
        f"p{i:03d}.jpg" for i in range(20))))
    said: list = []

    _runner(source, pipeline=lambda *a: None, workers=1, announce=said.append,
            progress_every=5)

    middles = [s for s in said if "/20" in s]
    assert 2 <= len(middles) <= 5, f"{len(middles)} progress lines for 20 docs"
    assert any("20/20" in s for s in said)


def test_the_opening_line_names_the_work_and_how_the_mode_was_decided(tmp_path):
    source = br.inspect_source(_orders(tmp_path / "b"))
    said: list = []

    _runner(source, pipeline=lambda *a: None, workers=2, announce=said.append)

    assert "2 Dokument(e), 4 Seite(n)" in said[0]
    assert "2 Worker" in said[0] and source.why in said[0]


def test_the_closing_line_separates_failed_from_given_up(tmp_path):
    """`failed` is retryable and `dead` is not; one number for both would hide
    which of the two a re-run would fix."""
    source = br.inspect_source(_pages(tmp_path / "f", ("ok.jpg", "bad.jpg")))
    said: list = []

    def pipeline(doc_id, paths, mode):
        if doc_id == "bad":
            raise RuntimeError("x")

    _runner(source, pipeline=pipeline, workers=1, max_attempts=1,
            announce=said.append)

    assert "1 verarbeitet" in said[-1] and "1 aufgegeben" in said[-1]


# ── the write R2 makes dangerous ─────────────────────────────────────────────
#
# `RunState.save` wrote to a FIXED temp name, `<doc_id>.json.tmp`. That is per
# document and therefore safe between workers on *different* documents — and not
# safe between two processes on the *same* one. The bot and this runner are
# exactly that pair: a gate click on a document the runner is working through.
# R3's `shared_lock.write_json_atomic` names `data/runs/*.json` in its own
# docstring and this writer had not been moved over.

def test_runstate_save_goes_through_the_shared_lock(monkeypatch, tmp_path):
    """The claim this change makes, asserted. Reverting the writer to its own
    unlocked temp file passes every other test in this file."""
    import runstate
    import shared_lock
    seen: list = []
    real = shared_lock.write_json_atomic

    def spy(path, data):
        seen.append(Path(path).name)
        real(path, data)

    monkeypatch.setattr(shared_lock, "write_json_atomic", spy)
    runstate.RunState(doc_id="d-392").save(tmp_path / "d-392.json")

    assert seen == ["d-392.json"], "save did not use the locked writer"


def test_two_writers_on_the_same_document_never_tear_the_file(tmp_path):
    """The hazard itself: a reader must never see a half-written state, and the
    last writer must win cleanly."""
    import json
    import runstate

    target = tmp_path / "same.json"
    errors: list = []
    barrier = threading.Barrier(4, timeout=20)

    def writer(n):
        state = runstate.RunState(doc_id="same")
        state.artifacts["vlm"] = f"x{n}" * 2000      # big enough to tear
        try:
            barrier.wait()
            for _ in range(15):
                state.save(target)
                loaded = json.loads(target.read_text(encoding="utf-8"))
                assert loaded["doc_id"] == "same"
        except Exception as e:                        # noqa: BLE001
            errors.append(repr(e))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=30)

    assert not errors, errors
    final = json.loads(target.read_text(encoding="utf-8"))
    assert final["doc_id"] == "same"


def test_the_fixed_temp_name_is_gone(tmp_path):
    """`<doc_id>.json.tmp` was the shared path two processes collided on."""
    import runstate

    target = tmp_path / "d.json"
    runstate.RunState(doc_id="d").save(target)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]

    assert leftovers == []
    assert not (tmp_path / "d.json.tmp").exists()


def test_the_on_disk_format_is_still_the_models_own(tmp_path):
    """Re-encoding from `model_dump()` instead of `model_dump_json()` would
    quietly change how datetimes and enums land on disk."""
    import runstate

    target = tmp_path / "d.json"
    state = runstate.RunState(doc_id="d")
    state.invalidate("century", value=16, user="u")
    state.save(target)

    back = runstate.RunState.load("d", target)
    assert back.criteria["century"] == 16
    assert back.human_overrides[0]["user"] == "u"
    assert back.dirty_stages() == state.dirty_stages()


# ── the CLI ──────────────────────────────────────────────────────────────────

def _cli():
    """The package's `__main__`, loaded by path.

    `import __main__` inside pytest resolves to **pytest's** `__main__`, not
    this package's — which is what the first version of these tests did, and it
    failed with an ImportError that named the wrong file.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("ah_cli", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_subcommand_is_registered():
    args = _cli().build_parser().parse_args(
        ["batch", "/some/folder", "--workers", "3"])

    assert args.workers == 3 and args.folder == "/some/folder"
    assert args.mode is None and args.dry_run is False


def test_a_dry_run_registers_and_claims_nothing(tmp_path, capsys):
    cli = _cli()

    root = _pages(tmp_path / "missiven", ("a.jpg", "b.jpg"))
    args = cli.build_parser().parse_args(["batch", str(root), "--dry-run"])
    rc = args.func(args)
    out = capsys.readouterr().out

    assert rc == 0
    assert "2 document(s), 2 page(s)" in out and "mode=pages" in out
    assert cm.progress("missiven").pending == 2, "nothing was registered"
    assert cm.progress("missiven").running == 0, "a dry run claimed work"


def test_the_publishing_plan_is_stated_before_the_run(tmp_path, monkeypatch,
                                                      capsys):
    """#392 refused to run with ENABLE_GITHUB_PUBLISH on, because the pipeline
    would commit each document. #394 made that a working path — the runner calls
    the pipeline with publish=False and commits N at a time — so the refusal
    became a statement of which way it will publish.

    `--dry-run` keeps this offline: without it the CLI would run the real
    pipeline and the real GitHub API, which the suite may not do.
    """
    cli = _cli()
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", True)
    monkeypatch.setattr(config, "BATCH_PUBLISH_EVERY", 0)
    root = _pages(tmp_path / "f", ("a.jpg",))

    args = cli.build_parser().parse_args(["batch", str(root), "--dry-run"])
    assert args.func(args) == 0
    assert "once at the end of the run" in capsys.readouterr().out

    args = cli.build_parser().parse_args(
        ["batch", str(root), "--dry-run", "--publish-every", "25"])
    assert args.func(args) == 0
    assert "every 25 document(s)" in capsys.readouterr().out

    args = cli.build_parser().parse_args(
        ["batch", str(root), "--dry-run", "--allow-per-doc-publish"])
    assert args.func(args) == 0
    assert "one commit per document" in capsys.readouterr().out


def test_the_publish_flag_is_silent_when_publishing_is_off(tmp_path,
                                                           monkeypatch, capsys):
    """A flag whose help promises something it cannot do is worse than no flag."""
    cli = _cli()
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", False)
    root = _pages(tmp_path / "f", ("a.jpg",))

    args = cli.build_parser().parse_args(
        ["batch", str(root), "--dry-run", "--publish-every", "5"])
    args.func(args)

    assert "no effect" in capsys.readouterr().out


def test_an_ambiguous_folder_exits_two_with_the_remedy(tmp_path, capsys):
    cli = _cli()

    root = _orders(tmp_path / "mixed")
    (root / "stray.jpg").write_bytes(b"\xff\xd8\xff")
    args = cli.build_parser().parse_args(["batch", str(root)])
    rc = args.func(args)

    assert rc == 2 and "--mode" in capsys.readouterr().out


def test_a_dead_letter_is_a_nonzero_exit_and_a_mere_failure_is_not(tmp_path,
                                                                   monkeypatch):
    """Only a dead letter needs somebody to look; a `failed` document is picked
    up by the next run."""
    cli = _cli()
    import batch_runner

    root = _pages(tmp_path / "f", ("bad.jpg",))

    def run_batch(run_id, source, **kw):
        cm.register(run_id, source.doc_ids)
        cm.claim(run_id)
        cm.mark_failed(run_id, "bad", "x", max_attempts=99)   # failed, not dead
        return batch_runner.Summary(run_id=run_id)

    monkeypatch.setattr(batch_runner, "run_batch", run_batch)
    args = cli.build_parser().parse_args(["batch", str(root)])

    assert args.func(args) == 0, "a retryable failure must not look like a crash"
