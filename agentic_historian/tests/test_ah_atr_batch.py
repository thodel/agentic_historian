"""Multi-model batch recognition (atr_batch.py).

Offline: the recogniser is a stub, so nothing here reaches the ATR gateway. Run
from the repo root::

    pytest agentic_historian/tests/test_ah_atr_batch.py
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                                  # noqa: E402
from agent_a.kraken_client import KrakenClientError        # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def make_corpus(root: Path, names=("letter-01/001.jpg", "letter-01/002.jpg", "b.png")):
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"image-bytes-" + name.encode())
    return root


def reading(text="hello", lines=2, ms=1200, engine="vllm"):
    return SimpleNamespace(
        text=text, confidence=0.9, model_used="m", service_version="1.2.3",
        lines=[{"order": i, "text": f"line{i}"} for i in range(lines)],
        engine=engine, segmented_by="kraken:blla", timing_ms=ms, second_opinion=None,
    )


class Recorder:
    """A recogniser that records every call and replays a script of outcomes.

    Keyed by the page's batch key (``letter-01__001``), not by its filename stem:
    two documents in a share routinely both contain ``001.jpg``, and a stub that
    could not tell them apart would quietly script both.
    """

    def __init__(self, script=None, default=None, root: Path | None = None):
        self.calls: list[tuple[str, str]] = []          # (model, page key)
        self.script = dict(script or {})                # (model, key) -> result|exc
        self.default = default if default is not None else reading()
        self.root = root

    def _key(self, image: Path) -> str:
        if self.root is None:
            return image.stem
        return batch._key_for(image.resolve().relative_to(Path(self.root).resolve()))

    def __call__(self, image: Path, model: str):
        key = self._key(image)
        self.calls.append((model, key))
        outcome = self.script.get((model, key), self.default)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome() if callable(outcome) else outcome

    @property
    def models_in_call_order(self) -> list[str]:
        seen: list[str] = []
        for model, _ in self.calls:
            if not seen or seen[-1] != model:
                seen.append(model)
        return seen


def gateway_error(status):
    return KrakenClientError(f"Kraken service {status}", status_code=status)


# ── the corpus ───────────────────────────────────────────────────────────────

def test_discover_pages_is_recursive_sorted_and_keyed_by_path(tmp_path):
    make_corpus(tmp_path)
    pages = batch.discover_pages(tmp_path)
    assert [p.key for p in pages] == ["b", "letter-01__001", "letter-01__002"]
    assert [p.doc_id for p in pages] == ["", "letter-01", "letter-01"]


def test_a_page_is_an_image_and_a_pdf_is_not(tmp_path):
    """The recognisers take one image per call. A PDF handed to them fails once
    per page instead of being converted, so it is not discovered as a page."""
    make_corpus(tmp_path, ("a.jpg", "scan.pdf", "notes.txt", "c.TIF"))
    assert [p.key for p in batch.discover_pages(tmp_path)] == ["a", "c"]


def test_limit_takes_the_first_pages_of_the_stable_order(tmp_path):
    make_corpus(tmp_path)
    assert [p.key for p in batch.discover_pages(tmp_path, limit=2)] == ["b", "letter-01__001"]


# ── what counts as already done ──────────────────────────────────────────────

def test_is_complete_rejects_missing_unparsable_and_foreign_results(tmp_path):
    assert batch.is_complete(tmp_path / "nope.json") is False

    half = tmp_path / "half.json"
    half.write_text('{"schema": "atr-batch/1", "text": "abc')   # interrupted write
    assert batch.is_complete(half) is False, "a truncated result must not be skipped"

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"schema": "atr-batch/0", "text": "abc"}))
    assert batch.is_complete(old) is False, "a different schema is not this run's output"

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"schema": batch.SCHEMA, "text": ""}))
    assert batch.is_complete(good) is True, "an empty page is a result, not a gap"


# ── failure classification ───────────────────────────────────────────────────

@pytest.mark.parametrize("exc,retryable,fatal", [
    (KrakenClientError("unreachable"), True, False),      # no status: never answered
    (gateway_error(500), True, False),
    (gateway_error(502), True, False),
    (gateway_error(404), False, True),                    # unknown model id
    (gateway_error(401), False, True),
    (gateway_error(403), False, True),
    (gateway_error(422), False, False),                   # this page, not this model
    (gateway_error(400), False, False),
])
def test_classify_failure(exc, retryable, fatal):
    assert batch.classify_failure(exc) == (retryable, fatal)


# ── one model over the corpus ────────────────────────────────────────────────

def test_a_model_writes_text_and_json_per_page(tmp_path):
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    pages = batch.discover_pages(src)
    result = batch.run_model(pages, "model-a", "run1", out, Recorder())

    assert (result.done, result.failed, result.skipped) == (3, 0, 0)
    assert (out / "model-a" / "letter-01__001.txt").read_text() == "hello"
    payload = json.loads((out / "model-a" / "letter-01__001.json").read_text())
    assert payload["schema"] == batch.SCHEMA
    assert payload["model"] == "model-a"
    assert payload["doc_id"] == "letter-01"
    assert payload["engine"] == "vllm"
    assert len(payload["lines"]) == 2
    assert payload["timing_ms"] == 1200
    assert len(payload["source"]["sha256"]) == 64
    assert not list((out / "model-a").glob("*.tmp")), "temp files left behind"


def test_the_source_hash_identifies_the_bytes_that_were_read(tmp_path):
    """A comparison outlives the staging directory it was run from. Only the hash
    can say afterwards which image produced a reading — filenames survive
    re-scanning and re-cropping unchanged."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    batch.run_model(batch.discover_pages(src), "m", "run1", out, Recorder())
    first = json.loads((out / "m" / "a.json").read_text())["source"]["sha256"]

    (src / "a.jpg").write_bytes(b"a different scan")
    (out / "m" / "a.json").unlink()
    batch.run_model(batch.discover_pages(src), "m", "run1", out, Recorder())
    assert json.loads((out / "m" / "a.json").read_text())["source"]["sha256"] != first


def test_a_rerun_skips_pages_that_are_already_read(tmp_path):
    """What makes a multi-hour run restartable: the output is the state."""
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    pages = batch.discover_pages(src)
    batch.run_model(pages, "m", "run1", out, Recorder())

    rec = Recorder()
    again = batch.run_model(pages, "m", "run1", out, rec)
    assert (again.done, again.skipped) == (0, 3)
    assert rec.calls == [], "re-read a page that was already on disk"


def test_a_page_failure_is_recorded_and_the_model_goes_on(tmp_path):
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    pages = batch.discover_pages(src)
    rec = Recorder(script={("m", "letter-01__001"): gateway_error(422)}, root=src)

    result = batch.run_model(pages, "m", "run1", out, rec, retries=0)
    assert (result.done, result.failed) == (2, 1)
    assert not result.aborted
    assert "letter-01__001" in result.errors[0]
    assert not (out / "m" / "letter-01__001.json").exists()


def test_an_unknown_model_id_ends_that_model_at_the_first_page(tmp_path):
    """404 is true of every page. Five hundred identical failures is not more
    information than one, and the time they cost is the rest of the run."""
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    rec = Recorder(default=gateway_error(404))

    result = batch.run_model(batch.discover_pages(src), "typo", "run1", out, rec, retries=3)
    assert result.aborted.startswith("fatal on")
    assert len(rec.calls) == 1, "kept calling after a failure that cannot change"


def test_a_run_of_failures_ends_the_model_before_it_eats_the_night(tmp_path):
    src = make_corpus(tmp_path / "src", tuple(f"p{i:02d}.jpg" for i in range(20)))
    out = tmp_path / "out"
    rec = Recorder(default=gateway_error(500))

    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, rec,
                             retries=0, max_consecutive_failures=3, sleep=lambda _: None)
    assert result.failed == 3
    assert "3 consecutive failures" in result.aborted
    assert len(rec.calls) == 3


def test_scattered_bad_pages_do_not_end_a_working_model(tmp_path):
    """The counter resets on success: a corpus with a few unreadable images must
    run to the end, while a gateway that has gone away must not."""
    names = tuple(f"p{i:02d}.jpg" for i in range(9))
    src, out = make_corpus(tmp_path / "src", names), tmp_path / "out"
    script = {("m", f"p{i:02d}"): gateway_error(422) for i in (1, 3, 5, 7)}

    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, Recorder(script=script),
                             retries=0, max_consecutive_failures=3)
    assert (result.done, result.failed) == (5, 4)
    assert not result.aborted


def test_a_timeout_is_retried_and_can_succeed(tmp_path):
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise KrakenClientError("read timeout")          # no status = never answered
        return reading(text="recovered")

    rec = Recorder(default=flaky)
    waits: list[float] = []
    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, rec,
                             retries=2, sleep=waits.append)

    assert result.done == 1
    assert (out / "m" / "a.txt").read_text() == "recovered"
    assert waits == [2.0, 4.0], "backoff must grow, or a restarting gateway is hammered"


def test_a_4xx_is_not_retried(tmp_path):
    """Repeating a request the gateway already rejected spends the retry budget
    before the page can be reported as lost."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    rec = Recorder(default=gateway_error(422))
    batch.run_model(batch.discover_pages(src), "m", "run1", out, rec, retries=5,
                    sleep=lambda _: None)
    assert len(rec.calls) == 1


def test_the_manifest_records_every_page(tmp_path):
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    manifest = out / "manifest.jsonl"
    rec = Recorder(script={("m", "b"): gateway_error(422)}, root=src)
    batch.run_model(batch.discover_pages(src), "m", "run1", out, rec, retries=0,
                    manifest=manifest)

    records = [json.loads(ln) for ln in manifest.read_text().splitlines()]
    assert [r["key"] for r in records] == ["b", "letter-01__001", "letter-01__002"]
    assert [r["status"] for r in records] == ["failed", "done", "done"]


# ── the batch ────────────────────────────────────────────────────────────────

def test_the_batch_is_model_major(tmp_path):
    """The reason this module exists. The gateway's VLMs are lazy on one GPU that
    holds one at a time, so page-major pays an evict-and-reload cycle per page."""
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    rec = Recorder()
    batch.run_batch(batch.discover_pages(src), ["m1", "m2", "m3"], "run1", out, rec)

    assert rec.models_in_call_order == ["m1", "m2", "m3"], "model switched mid-corpus"
    assert len(rec.calls) == 9


def test_one_abandoned_model_does_not_stop_the_others(tmp_path):
    src, out = make_corpus(tmp_path / "src"), tmp_path / "out"
    rec = Recorder(script={("typo", k): gateway_error(404)
                           for k in ("b", "letter-01__001", "letter-01__002")}, root=src)
    report = batch.run_batch(batch.discover_pages(src), ["typo", "good"], "run1", out, rec)

    by_id = {m.model: m for m in report.models}
    assert by_id["typo"].aborted
    assert by_id["good"].done == 3, "a mistyped model id threw away a working one"
    assert report.any_aborted is True


def test_each_model_gets_its_own_directory(tmp_path):
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    batch.run_batch(batch.discover_pages(src), ["m1", "m2"], "run1", out, Recorder())
    assert (out / "m1" / "a.txt").exists() and (out / "m2" / "a.txt").exists()


def test_concurrency_stops_early_and_keeps_corpus_order(tmp_path):
    """Submitting the whole corpus up front would make the circuit breaker
    decorative: the pages it 'stopped' would already be queued."""
    names = tuple(f"p{i:02d}.jpg" for i in range(40))
    src, out = make_corpus(tmp_path / "src", names), tmp_path / "out"
    rec = Recorder(default=gateway_error(500))

    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, rec,
                             retries=0, concurrency=4, max_consecutive_failures=3,
                             sleep=lambda _: None)
    assert result.aborted
    assert len(rec.calls) <= 8, f"kept working after the breaker tripped ({len(rec.calls)} calls)"


def test_concurrency_reads_every_page_exactly_once(tmp_path):
    names = tuple(f"p{i:02d}.jpg" for i in range(10))
    src, out = make_corpus(tmp_path / "src", names), tmp_path / "out"
    rec = Recorder()
    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, rec, concurrency=4)
    assert result.done == 10
    assert sorted(k for _, k in rec.calls) == sorted(Path(n).stem for n in names)


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_refuses_to_read_as_a_ranking(tmp_path):
    """With no ground truth, characters per page says how much a model wrote. A
    model that hallucinates fluently leads that column, so the file has to say so
    where the table is — not in a doc nobody opens next to it."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    report = batch.run_batch(batch.discover_pages(src), ["m1", "m2"], "run1", out, Recorder())
    text = batch.format_report(report)

    assert "not quality" in text.lower()
    assert "linebench" in text
    assert "`m1`" in text and "`m2`" in text


def test_the_report_names_what_was_abandoned_and_why(tmp_path):
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    rec = Recorder(script={("typo", "a"): gateway_error(404)})
    report = batch.run_batch(batch.discover_pages(src), ["typo", "good"], "run1", out, rec)
    text = batch.format_report(report)

    assert "## Abandoned" in text
    assert "typo" in text and "404" in text


def test_write_report_leaves_both_shapes_in_the_run_directory(tmp_path):
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    report = batch.run_batch(batch.discover_pages(src), ["m1"], "run1", out, Recorder())
    md = batch.write_report(report)

    assert md == out / "report.md" and md.exists()
    data = json.loads((out / "report.json").read_text())
    assert data["run"] == "run1" and data["models"][0]["model"] == "m1"


# ── truncation ───────────────────────────────────────────────────────────────

def test_a_cut_off_reading_is_kept_counted_and_named(tmp_path):
    """The one number in the report that means the corpus is wrong rather than
    merely expensive. A truncated reading is not a failure — the request
    succeeded and the text is real — so it is kept, and counted apart from both
    the successes and the failures."""
    names = ("a.jpg", "b.jpg", "c.jpg")
    src, out = make_corpus(tmp_path / "src", names), tmp_path / "out"
    cut = reading(text="Lieber Freund, ich ha")
    cut.truncated = True
    rec = Recorder(script={("m", "a"): cut, ("m", "c"): cut}, root=src)

    result = batch.run_model(batch.discover_pages(src), "m", "run1", out, rec)

    assert (result.done, result.failed, result.truncated) == (3, 0, 2)
    assert (out / "m" / "a.txt").read_text() == "Lieber Freund, ich ha", "text was dropped"
    assert json.loads((out / "m" / "a.json").read_text())["truncated"] is True
    assert json.loads((out / "m" / "b.json").read_text())["truncated"] is False


def test_the_report_explains_a_cut_off_reading_rather_than_just_counting_it(tmp_path):
    """Truncated text ends mid-sentence and reads exactly like a model that gave
    up, so the natural response is to blame the model. The report has to point at
    the ceiling instead."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    cut = reading(text="ich ha")
    cut.truncated = True
    report = batch.run_batch(batch.discover_pages(src), ["m"], "run1", out,
                             Recorder(default=cut, root=src))
    text = batch.format_report(report)

    assert "## Readings that were cut off" in text
    assert "ATR_VLLM_MAX_NEW_TOKENS" in text
    assert "`m`: 1 of 1 page(s)" in text


def test_a_clean_run_says_nothing_about_truncation(tmp_path):
    """A section that is always there is a section nobody reads."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    report = batch.run_batch(batch.discover_pages(src), ["m"], "run1", out, Recorder())
    assert "cut off" not in batch.format_report(report).replace("| cut off |", "")


def test_a_gateway_that_cannot_report_truncation_never_flags_it(tmp_path):
    """Older gateways have no such field. Absence of a signal is not evidence of
    one, and a flag that fires on a missing key is a flag people learn to ignore."""
    src, out = make_corpus(tmp_path / "src", ("a.jpg",)), tmp_path / "out"
    old = SimpleNamespace(text="hi", confidence=0.5, model_used="m", service_version="0.1",
                          lines=[], engine="vllm", segmented_by=None, timing_ms=5)
    result = batch.run_model(batch.discover_pages(src), "m", "run1", out,
                             Recorder(default=old, root=src))
    assert result.truncated == 0
    assert json.loads((out / "m" / "a.json").read_text())["truncated"] is False


# ── sampling ─────────────────────────────────────────────────────────────────
#
# `--limit` takes the first N, which on a share that is one folder per document
# means N consecutive pages of a single letter: the same hand, the same ink,
# often the same scanner setting. Enough to prove the path works; misleading as
# an impression of how a model reads the collection. `--sample` crosses
# documents — deterministically, because the runner treats what is on disk as its
# state and a sample that moved between runs would break resuming.

def _corpus(tmp_path, documents=5, pages=20):
    """A share shaped like the real one: one folder per document."""
    for d in range(documents):
        folder = tmp_path / f"letter-{d:02d}"
        folder.mkdir()
        for p in range(pages):
            (folder / f"{p:03d}.jpg").write_bytes(b"x")
    return tmp_path


def test_sampling_crosses_documents_where_limit_does_not(tmp_path):
    root = _corpus(tmp_path)
    first_ten = batch.discover_pages(root, limit=10)
    sampled = batch.discover_pages(root, sample=10)

    assert len({p.doc_id for p in first_ten}) == 1, "the very problem being fixed"
    assert len({p.doc_id for p in sampled}) > 1
    assert len(sampled) == 10


def test_the_same_sample_twice(tmp_path):
    """The property the runner depends on: resuming must re-read the same pages,
    and a second model must see the same corpus as the first."""
    root = _corpus(tmp_path)
    assert [p.key for p in batch.discover_pages(root, sample=10)] == \
           [p.key for p in batch.discover_pages(root, sample=10)]


def test_a_different_seed_gives_a_different_sample(tmp_path):
    root = _corpus(tmp_path)
    a = [p.key for p in batch.discover_pages(root, sample=10, seed=1)]
    b = [p.key for p in batch.discover_pages(root, sample=10, seed=2)]
    assert a != b


def test_a_sample_is_still_processed_in_corpus_order(tmp_path):
    """Sorted back after drawing, so "it failed on page 40" still means something
    and a resumed run walks the sequence it walked before."""
    keys = [p.key for p in batch.discover_pages(_corpus(tmp_path), sample=10)]
    assert keys == sorted(keys)


def test_sampling_more_than_there_is_takes_everything(tmp_path):
    root = _corpus(tmp_path, documents=2, pages=3)
    assert len(batch.discover_pages(root, sample=999)) == 6


def test_limit_and_sample_are_alternatives(tmp_path):
    """A request for both has no obvious reading, and guessing one would be worse
    than refusing."""
    with pytest.raises(ValueError):
        batch.discover_pages(_corpus(tmp_path), limit=5, sample=5)


def test_a_negative_sample_is_refused(tmp_path):
    with pytest.raises(ValueError):
        batch.discover_pages(_corpus(tmp_path), sample=-1)


def test_neither_flag_still_means_the_whole_corpus(tmp_path):
    assert len(batch.discover_pages(_corpus(tmp_path, documents=3, pages=4))) == 12
