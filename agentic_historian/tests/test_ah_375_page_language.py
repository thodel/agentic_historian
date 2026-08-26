"""#375 (second half): the language is decided per page, not per order.

An order is not uniform. saa-0428 is a Königsfelden cartulary with German front
matter and Latin charters, and one language for the whole order read the Latin with
a German Kurrent model while `trocr-essoins-middle-latin` sat unused.

**A page is not a language either.** The register can switch between sentences — a
German entry quoting a Latin formula, a Latin charter naming German persons — which
is exactly what Agent B reported for this manuscript ("überwiegend mittelhochdeutsche
Formen, aber mit lateinischen Floskeln"). The page is the unit the pipeline already
has, so it is a better approximation than the order, not a correct one. That limit
is asserted below rather than left implicit.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_375_page_language.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402
from agent_a.model_selector import SourceCriteria, detect_language  # noqa: E402

# from the live run
LATIN = ("modios trititi cum prato sito iuxta Ottiuissingen de quibus anno "
         "domini item est atque conventum nostri")
GERMAN = ("Hut genennt die brief Daz Closter het zien gelich die daz Closter "
          "ze haben sol und die ab schrift der brief nach ordnung")


# ── the detector answers a narrow question ───────────────────────────────────

def test_a_latin_page_is_recognised_among_the_declared_languages():
    assert detect_language(LATIN, ["de", "la"]) == "la"


def test_a_german_page_is_recognised():
    assert detect_language(GERMAN, ["de", "la"]) == "de"


def test_a_genuinely_mixed_page_says_nothing():
    """The honest answer when the register switches inside the page. A wrong page
    language is worse than the order default — it removes the right models."""
    assert detect_language("und der brief cum prato sito ze haben et anno",
                           ["de", "la"]) is None


def test_thin_evidence_says_nothing():
    assert detect_language("modios cum", ["de", "la"]) is None
    assert detect_language("", ["de", "la"]) is None


def test_it_never_proposes_a_language_the_source_did_not_declare():
    """Guessing freely from 500-year-old HTR output would invent evidence; choosing
    between two stated options is a much smaller claim."""
    assert detect_language(LATIN, ["de"]) is None
    assert detect_language(LATIN, []) is None
    assert detect_language(LATIN, ["de", "fr"]) != "la"


# ── the per-page criteria ────────────────────────────────────────────────────

def _ctx_with_pages():
    ctx = SimpleNamespace(recognitions=[
        {"page": "001r.jpg", "text": GERMAN, "error": ""},
        {"page": "015v.jpg", "text": LATIN, "error": ""},
        {"page": "016r.jpg", "text": "cum cum", "error": ""},      # thin
    ])
    return ctx


BILINGUAL = SourceCriteria(script="kursive", century=16, lang="de",
                           langs=["de", "la"])


def test_a_latin_page_gets_latin_criteria():
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    out = fn(Path("015v.jpg"))
    assert out is not None and out.lang == "la"


def test_a_page_matching_the_order_language_is_left_alone():
    """No change is the cheapest correct answer — the order criteria already fit."""
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    assert fn(Path("001r.jpg")) is None


def test_an_undecidable_page_falls_back_to_the_order():
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    assert fn(Path("016r.jpg")) is None


def test_the_page_keeps_the_order_script_and_century():
    """Only the language varies within a codex; the hand and the dating do not."""
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    out = fn(Path("015v.jpg"))
    assert out.script == "kursive" and out.century == 16


def test_the_other_language_stays_eligible_on_a_specialised_page():
    """Even a Latin charter names German persons and places (#375). Demoting German
    entirely would trade one over-commitment for another."""
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    out = fn(Path("015v.jpg"))
    assert out.langs[0] == "la" and "de" in out.langs


def test_a_monolingual_order_gets_no_page_specialisation():
    """Nothing to disambiguate — and running the detector would only add risk."""
    mono = SourceCriteria(script="kursive", century=16, lang="de", langs=["de"])
    assert orchestrator._page_criteria_fn(mono, _ctx_with_pages(), "d", None) is None


def test_the_decision_is_announced():
    """A silent change of model selection is not auditable."""
    seen = []
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", seen.append)
    fn(Path("015v.jpg"))
    assert seen and "015v.jpg" in seen[-1].decision and "la" in seen[-1].decision


# ── it must never break a page ───────────────────────────────────────────────

def test_a_broken_detector_leaves_the_order_criteria_in_place(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr("agent_a.model_selector.detect_language", boom)
    fn = orchestrator._page_criteria_fn(BILINGUAL, _ctx_with_pages(), "d", None)
    with pytest.raises(RuntimeError):
        fn(Path("015v.jpg"))          # the callable itself may raise …


def test_the_ensemble_pass_swallows_a_failing_criteria_fn(tmp_path, monkeypatch):
    """… and _ensemble_pass must absorb it, because a criteria refinement failing
    is not a reason to lose the page."""
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble",
                        lambda img, crit: calls.append(crit) or SimpleNamespace(
                            recognitions=[], text="t", loops=0,
                            max_pairwise_cer=0.0, usable=0, no_merge=False))
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    ctx = SimpleNamespace(recognitions=[], errors=[])

    def boom(_img):
        raise RuntimeError("nope")

    orchestrator._ensemble_pass([img], BILINGUAL, ctx, "d", None,
                                label="pass 2", criteria_for=boom)
    assert calls and calls[0] is BILINGUAL       # fell back to the order criteria


# ── the wiring itself, not just the helper ───────────────────────────────────

def test_pass_two_actually_applies_the_page_language(tmp_path, monkeypatch):
    """Written after a revert probe showed every test above still passing with the
    `criteria_for=` argument deleted: they exercised the helper, never its use."""
    import config
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)

    seen = []

    def fake_ensemble(img, criteria):
        seen.append((Path(img).name, getattr(criteria, "lang", None)))
        return SimpleNamespace(recognitions=[], text="t", loops=0,
                               max_pairwise_cer=0.0, usable=2, no_merge=False)

    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", fake_ensemble)

    latin_page = tmp_path / "015v.jpg"
    german_page = tmp_path / "001r.jpg"
    for f in (latin_page, german_page):
        f.write_bytes(b"\x00")

    ctx = SimpleNamespace(
        transcription="x", a_meta={"qa_score": 0.5}, errors=[],
        description={"source_description": "Deutsch und Latein, gemischt",
                     "source_json": {"Schrift": {"wert": "Kursive"},
                                     "Sprache": {"wert": "Deutsch und Latein, gemischt"},
                                     "Datierung": {"wert": "16. Jahrhundert"}}},
        recognitions=[{"page": "015v.jpg", "text": LATIN, "error": ""},
                      {"page": "001r.jpg", "text": GERMAN, "error": ""}])

    orchestrator._criteria_rerun([german_page, latin_page], ctx, "d-wire", None,
                                 avg_qa=0.5, source_tag="t")

    by_page = dict(seen)
    assert by_page.get("015v.jpg") == "la", f"Latin page did not get Latin criteria: {seen}"
    assert by_page.get("001r.jpg") == "de"
