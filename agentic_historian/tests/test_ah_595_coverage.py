"""A page says how many of its planned readings it was made from — #595.

`missiven`, published 2026-10-09. From its `pipeline.json`:

    20 recognitions: 4 with text, 16 with an error
      vlm 4/4 · kraken 0/10 · trocr 0/6
    "errors": []
    "a_meta": {"pages": 2, "qa_score": 0.0, "source": "grouped-ensemble-criteria"}

Sixteen readings failed against a kraken service that was not running, the
ensemble ran VLM-only, and the publication went through looking like any other
result. A page made from 4 of 20 readings was indistinguishable from one made
from 20 of 20 — the same shape as #482, #483, #406 and #546, by silence rather
than by a wrong number.

Offline throughout. The decision these tests encode (2026-10-10): **publish, but
marked**; refuse only where nothing came back at all.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import ensemble_coverage as ec             # noqa: E402
from utils import publish_github as pg     # noqa: E402

#: The run this is about, in miniature: one page read by five engines, one of
#: which answered. The real one was two pages and ten engines each.
MISSIVEN = [
    {"engine": "vlm", "model_id": "qwen3.8-27b", "page": "m_49_97.JPG",
     "text": "Dem Edlen und Vesten ...", "timing_ms": 8410},
    {"engine": "kraken", "model_id": "kraken-de-1", "page": "m_49_97.JPG",
     "text": "", "error": "Kraken service 502 at http://gw:8200/ocr: "
                          "{\"detail\":\"kraken engine unreachable\"}"},
    {"engine": "kraken", "model_id": "kraken-de-2", "page": "m_49_97.JPG",
     "text": "", "error": "Kraken service 502 at http://gw:8200/ocr: ..."},
    {"engine": "trocr", "model_id": "trocr-1", "page": "m_49_97.JPG",
     "text": "", "error": "Kraken service 502 at http://gw:8200/ocr: ..."},
    {"engine": "trocr", "model_id": "trocr-2", "page": "m_49_97.JPG",
     "text": "", "error": "Kraken service 502 at http://gw:8200/ocr: ..."},
]


# ── counting ────────────────────────────────────────────────────────────────

def test_the_missiven_run_is_counted_as_one_of_five():
    cov = ec.measure(MISSIVEN)
    assert (cov.planned, cov.answered, cov.failed, cov.with_text) == (5, 1, 4, 1)
    assert cov.ratio == 0.2
    assert cov.marked and cov.usable


def test_a_complete_run_is_not_marked():
    recs = [{"engine": e, "text": "etwas"} for e in ("vlm", "kraken", "trocr")]
    cov = ec.measure(recs)
    assert cov.ratio == 1.0
    assert not cov.marked
    assert ec.notice(cov) == ""


def test_an_empty_reading_is_not_a_failure():
    """#483: an engine that read the page and found nothing did its job.

    The address side of StadtASG_Missive_49_98 is sparse by nature. Counting
    that as a malfunction teaches the reader to ignore the notice.
    """
    recs = [{"engine": "vlm", "text": "x"}, {"engine": "kraken", "text": "  "}]
    cov = ec.measure(recs)
    assert (cov.failed, cov.answered, cov.with_text) == (0, 2, 1)
    text = ec.notice(cov)
    assert "schlugen fehl" not in text
    assert "nichts gefunden" in text and "#483" in text


def test_nothing_attempted_is_unmeasured_not_zero():
    """The VLM-only path, and every document from before the ensemble existed.

    ``0.0`` would condemn them retroactively; ``None`` says what is true.
    """
    cov = ec.measure([])
    assert cov.ratio is None
    assert not cov.measured
    assert not cov.marked
    assert ec.notice(cov) == ""


def test_coverage_never_rounds_unmeasured_into_a_number():
    assert ec.Coverage().to_meta()["coverage"] is None
    assert ec.Coverage(planned=4, answered=0, failed=4).to_meta()["coverage"] == 0.0


# ── the document's own errors ───────────────────────────────────────────────

def test_every_failed_reading_becomes_a_document_error():
    """`errors: []` while sixteen readings were failing is what #595 is for."""
    errs = ec.failures(MISSIVEN)
    assert len(errs) == 4
    first = errs[0]
    assert first["engine"] == "kraken" and first["page"] == "m_49_97.JPG"
    assert first["model_id"] == "kraken-de-1"
    assert "502" in first["error"]
    assert first["phase"] == "recognition"


def test_a_successful_reading_contributes_no_error():
    assert ec.failures([{"engine": "vlm", "text": "x"}]) == []


def test_the_error_text_is_truncated():
    """Sixteen copies of the same gateway body make a file nobody reads — which
    is how the information was lost in the first place."""
    [err] = ec.failures([{"engine": "kraken", "error": "x" * 5000}])
    assert len(err["error"]) <= 300


# ── annotating the record ───────────────────────────────────────────────────

def _record(recs=None, **kw):
    out = {"doc_id": "missiven", "transcription": "Dem Edlen ...",
           "errors": [], "a_meta": {"pages": 2, "qa_score": 0.0,
                                    "source": "grouped-ensemble-criteria"},
           "recognitions": MISSIVEN if recs is None else recs}
    out.update(kw)
    return out


def test_annotate_fills_both_the_meta_and_the_errors():
    out = ec.annotate(_record())
    assert out["a_meta"]["engines_planned"] == 5
    assert out["a_meta"]["engines_with_text"] == 1
    assert out["a_meta"]["coverage"] == 0.2
    assert len(out["errors"]) == 4


def test_annotate_keeps_what_was_already_in_the_meta():
    out = ec.annotate(_record())
    assert out["a_meta"]["source"] == "grouped-ensemble-criteria"
    assert out["a_meta"]["pages"] == 2


def test_annotate_keeps_errors_that_were_already_there():
    rec = _record(errors=[{"agent": "B", "error": "describe failed"}])
    out = ec.annotate(rec)
    assert out["errors"][0] == {"agent": "B", "error": "describe failed"}
    assert len(out["errors"]) == 5


def test_annotate_is_idempotent():
    """ingest re-exports pipeline.json (#225). Twice must not mean eight errors."""
    once = ec.annotate(_record())
    twice = ec.annotate(dict(once))
    assert twice["errors"] == once["errors"]
    assert twice["a_meta"] == once["a_meta"]


def test_annotate_writes_the_documented_key_set():
    out = ec.annotate(_record())
    assert set(ec.META_KEYS) <= set(out["a_meta"])


def test_annotate_survives_a_malformed_record():
    assert ec.annotate({"recognitions": "not a list"})["a_meta"]["engines_planned"] == 0
    assert ec.annotate({"errors": "not a list"})["errors"] == []
    assert ec.annotate(None) is None


def test_coverage_reads_back_out_of_a_meta():
    out = ec.annotate(_record())
    cov = ec.from_meta(out["a_meta"])
    assert (cov.planned, cov.with_text, cov.failed) == (5, 1, 4)
    assert cov.marked


def test_a_record_from_before_595_reads_as_unmeasured():
    """Not as a total failure. Every page in the catalogue predates this."""
    cov = ec.from_meta({"pages": 2, "qa_score": 0.0})
    assert not cov.measured and cov.ratio is None and not cov.marked


# ── both write paths go through the annotation ─────────────────────────────

def test_the_orchestrator_annotates_before_it_writes():
    """A guard, and blunt like one.

    The normal path builds the record from the **RunState** and only the
    fallback from ``ctx.to_json()``, so a derivation inside either one would be
    missing from the other — and the missiven run went through the first. There
    must be exactly one write, after the annotation.
    """
    src = (PKG / "orchestrator.py").read_text(encoding="utf-8")
    body = src[src.index("def _save_pipeline_result"):]
    # It is currently the last function in the file; slice to the next one when
    # it stops being, so this guard does not quietly widen to the whole module.
    _next = body.find("\ndef ", 1)
    body = body if _next < 0 else body[:_next]
    assert body.count("json.dump(") == 1, \
        "one write, or a path can dump an un-annotated record"
    assert body.index("ensemble_coverage.annotate(") < body.index("json.dump("), \
        "annotate() must run before the record is written"


def test_the_gateway_timing_is_carried_into_the_record():
    """`timing_ms: 0` on every recognition had two causes, and this is the one
    nobody would guess: the gateway measured it, it landed in KrakenResult, and
    the RecognitionResult was built without it."""
    src = (PKG / "orchestrator.py").read_text(encoding="utf-8")
    fn = src[src.index("def _recognize_fn"):]
    fn = fn[:fn.index("result = ensemble.recognize_ensemble")]
    assert "timing_ms=res.timing_ms" in fn
    assert "segmented_by=res.segmented_by" in fn


def test_an_unreported_duration_is_none_not_zero():
    from agent_a.kraken_client import _timing_of
    assert _timing_of({}) is None
    assert _timing_of({"timing_ms": None}) is None
    assert _timing_of({"timing_ms": "nonsense"}) is None
    # A reported zero is a measurement and stays one, distinct from silence.
    assert _timing_of({"timing_ms": 0}) == 0
    assert _timing_of({"timing_ms": 8410}) == 8410


# ── the published page says it ──────────────────────────────────────────────

def _page(pipe: dict) -> str:
    artifacts = {"pipeline.json": json.dumps(pipe).encode("utf-8")}
    return pg._index_md("missiven", artifacts, None)


def test_the_catalogue_page_carries_the_notice():
    page = _page(ec.annotate(_record()))
    assert "⚠️" in page
    assert "1 von 5 geplanten Lesungen" in page
    # Near the top, not buried under the transcription: a reader who stops after
    # the title must still have seen it.
    assert page.index("1 von 5 geplanten") < page.index("## Metadaten")


def test_the_catalogue_page_carries_the_numbers_too():
    page = _page(ec.annotate(_record()))
    assert "| Lesungen | 1 von 5 mit Text, 4 fehlgeschlagen (Abdeckung 20%) |" in page


def test_a_complete_document_carries_no_notice():
    recs = [{"engine": e, "text": "etwas", "page": "p1"}
            for e in ("vlm", "kraken", "trocr")]
    page = _page(ec.annotate(_record(recs=recs)))
    assert "⚠️" not in page
    assert "geplanten Lesungen" not in page


def test_a_document_from_before_595_says_not_measured_rather_than_zero():
    page = _page({"transcription": "x", "a_meta": {"qa_score": 0.5, "coverage": None}})
    assert "nicht gemessen" in page
    assert "Abdeckung 0%" not in page


# ── publishing: marked, not withheld ───────────────────────────────────────

def test_a_partial_document_is_published():
    """The decision of 2026-10-10. A VLM reading is better than nothing, and the
    run cost real time — the marking is what makes keeping it honest."""
    ok, why = ec.may_publish(ec.annotate(_record()))
    assert ok and why == ""


def test_a_partial_document_publishes_on_what_came_back_not_on_having_text():
    """The decision is about the readings, not about whether a fused
    transcription happens to be present.

    Found by a mutation: replacing ``cov.usable`` with "every reading produced
    text" still passed, because the record under test also had a transcription
    and the next guard let it through. One usable reading is the reason to
    publish; the transcription guard is a separate safety net for work that came
    from somewhere else entirely.
    """
    ok, why = ec.may_publish(_record(transcription=""))
    assert ok and why == ""


def test_a_document_where_nothing_came_back_is_refused():
    recs = [{"engine": "kraken", "error": "502"} for _ in range(10)]
    ok, why = ec.may_publish(_record(recs=recs, transcription=""))
    assert not ok
    assert "keine der 10 geplanten Lesungen" in why
    assert "#595" in why


def test_a_document_with_nothing_measured_is_never_refused():
    """The VLM-only path has no recognitions at all. Refusing it would be
    refusing every document produced before the ensemble existed."""
    ok, _ = ec.may_publish({"recognitions": [], "transcription": ""})
    assert ok


def test_a_transcription_from_elsewhere_is_not_thrown_away():
    """All readings failed, but something readable exists — an earlier run, a
    hand correction. Refusing here would discard work to make a point."""
    recs = [{"engine": "kraken", "error": "502"}]
    ok, _ = ec.may_publish(_record(recs=recs, transcription="Hans von Bern"))
    assert ok


# ── the refusal reaches the publisher, and is its own kind ─────────────────

@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "ENABLE_GITHUB_PUBLISH", True)
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t0ken")
    monkeypatch.setattr(config, "GITHUB_OUTPUT_REPO", "owner/outputs")
    monkeypatch.setattr(config, "GITHUB_OUTPUT_BRANCH", "main")
    return tmp_path


def _on_disk(monkeypatch, pipe: dict, doc_id="missiven"):
    folder = config.DATA_DIR / "docs" / doc_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pipeline.json").write_text(json.dumps(pipe), encoding="utf-8")
    monkeypatch.setattr(pg, "collect_artifacts",
                        lambda d: {"pipeline.json": folder / "pipeline.json"}
                        if d == doc_id else {})


def test_doc_files_refuses_a_document_nothing_came_back_for(monkeypatch):
    recs = [{"engine": "kraken", "error": "502"} for _ in range(10)]
    _on_disk(monkeypatch, ec.annotate(_record(recs=recs, transcription="")))
    with pytest.raises(pg.DocumentUnreadable) as caught:
        pg.doc_files("missiven")
    assert "10 geplanten Lesungen" in str(caught.value)


def test_doc_files_publishes_a_partial_document(monkeypatch):
    _on_disk(monkeypatch, ec.annotate(_record()))
    files = pg.doc_files("missiven")
    assert "docs/missiven/index.md" in files
    assert "geplanten Lesungen" in files["docs/missiven/index.md"].decode()


def test_the_batch_separates_unreadable_from_a_refused_id(monkeypatch):
    """Two refusals with two remedies: rename the material, or fix the engines
    and run it again. A summary that cannot tell them apart sends the reader to
    rename a folder that is named perfectly well."""
    recs = [{"engine": "kraken", "error": "502"}]
    _on_disk(monkeypatch, ec.annotate(_record(recs=recs, transcription="")))
    result = pg.publish_docs(["missiven"])
    assert "missiven" in result.unreadable
    assert result.refused == {}
    assert result.outcome == "nothing"


def test_one_unreadable_document_does_not_cost_the_batch_its_other_work(monkeypatch):
    good = config.DATA_DIR / "docs" / "good"
    good.mkdir(parents=True, exist_ok=True)
    good_pipe = ec.annotate(_record(recs=[{"engine": "vlm", "text": "x"}]))
    (good / "pipeline.json").write_text(json.dumps(good_pipe), encoding="utf-8")
    bad = config.DATA_DIR / "docs" / "bad"
    bad.mkdir(parents=True, exist_ok=True)
    bad_pipe = ec.annotate(_record(recs=[{"engine": "kraken", "error": "502"}],
                                   transcription=""))
    (bad / "pipeline.json").write_text(json.dumps(bad_pipe), encoding="utf-8")
    monkeypatch.setattr(pg, "collect_artifacts", lambda d: {
        "pipeline.json": (config.DATA_DIR / "docs" / d / "pipeline.json")
    } if d in ("good", "bad") else {})
    monkeypatch.setattr(pg, "_commit_files", lambda *a, **kw: "https://commit")

    result = pg.publish_docs(["good", "bad"])
    assert result.published == ["good"]
    assert list(result.unreadable) == ["bad"]
    assert result.outcome == "published"


def test_publish_doc_raises_the_unreadable_refusal_for_one_document(monkeypatch):
    recs = [{"engine": "kraken", "error": "502"}]
    _on_disk(monkeypatch, ec.annotate(_record(recs=recs, transcription="")))
    with pytest.raises(pg.DocumentUnreadable):
        pg.publish_doc("missiven")


def test_the_unreadable_refusal_is_not_an_id_refusal():
    """Separate types, because the remedies are separate."""
    assert not issubclass(pg.DocumentUnreadable, pg.DocumentIdRefused)
    assert not issubclass(pg.DocumentIdRefused, pg.DocumentUnreadable)


# ── the listing, in the output repo's own script ───────────────────────────

def test_the_index_listing_marks_an_incomplete_document(tmp_path):
    sys.path.insert(0, str(PKG / "output_site" / "scripts"))
    import build_index

    docs = tmp_path / "docs"
    (docs / "missiven").mkdir(parents=True)
    (docs / "missiven" / "pipeline.json").write_text(
        json.dumps(ec.annotate(_record())), encoding="utf-8")
    (docs / "whole").mkdir(parents=True)
    (docs / "whole" / "pipeline.json").write_text(json.dumps(ec.annotate(_record(
        recs=[{"engine": e, "text": "x"} for e in ("vlm", "kraken", "trocr")]))),
        encoding="utf-8")
    (docs / "old").mkdir(parents=True)
    (docs / "old" / "pipeline.json").write_text(
        json.dumps({"transcription": "x"}), encoding="utf-8")
    # The VLM-only path: annotated, so the keys ARE there, and every one of them
    # is zero. This is the case a bare `isinstance(planned, int)` check lets
    # through as "0/0" — found by a mutation, and it is the commonest record in
    # the catalogue, not an edge case.
    (docs / "vlmonly").mkdir(parents=True)
    (docs / "vlmonly" / "pipeline.json").write_text(
        json.dumps(ec.annotate({"transcription": "x", "recognitions": [],
                                "a_meta": {"source": "mock-vlm"}})),
        encoding="utf-8")

    build_index.build(docs)
    listing = (docs / "index.md").read_text(encoding="utf-8")

    assert "| 1/5 ⚠️ |" in listing
    assert "| 3/3 |" in listing
    # Not measured prints as a dash — for a record from before #595 AND for an
    # annotated VLM-only one whose counts are all zero. "0/0" would be the very
    # claim #595 removes: a number where nothing was measured.
    assert listing.count("| — |") == 2
    assert "0/0" not in listing

    records = json.loads((docs / "search-index.json").read_text(encoding="utf-8"))
    assert {r["doc_id"]: r["readings"] for r in records} == {
        "missiven": "1/5 ⚠️", "whole": "3/3", "old": "", "vlmonly": ""}
