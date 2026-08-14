"""#367: a page with nothing to compare must not report perfect agreement.

Live on tei 2026-08-13 the ATR engines timed out mid model-load (#81). Two of three
candidates failed, one survived, and the run reported:

    qa=1.0    ·    pass 1 ensemble: 3 engine(s), 0 loop(s), agreement CER 0.0%

`_max_pairwise_cer` returns 0.0 below two usable candidates, and the orchestrator
turns that into `1.0 - 0.0 = 1.0`. So "no disagreement measurable" was encoded
identically to "perfect agreement" — certainty manufactured from absence, at
exactly the moment the system was broken.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_367_no_confidence_from_absence.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import orchestrator    # noqa: E402
from agent_a.model_selector import RecognitionResult  # noqa: E402


def _rec(engine, text, error=""):
    """A real RecognitionResult — the pipeline reads fields a SimpleNamespace lacks."""
    return RecognitionResult(engine=engine, model_id=f"{engine}-m", text=text,
                             error=error, confidence=0.5)


# ── the ensemble reports how many candidates were comparable ─────────────────

def test_usable_counts_only_candidates_that_produced_text():
    recs = [_rec("vlm", "eine lesart"),
            _rec("kraken", "", error="502 unreachable"),
            _rec("trocr", "", error="502 unreachable")]
    usable = len([r for r in recs if (r.text or "").strip() and not r.error])
    assert usable == 1                      # the live shape


# ── the QA score ─────────────────────────────────────────────────────────────

@pytest.fixture
def rig(tmp_path, monkeypatch):
    """run_full_pipeline_group with a stubbed ensemble of N usable candidates."""
    def make(usable, cer=0.0):
        def fake(img, criteria):
            recs = [_rec("vlm", "eine lesart")]
            if usable >= 2:
                recs.append(_rec("trocr", "eine andere lesart"))
            recs += [_rec("kraken", "", error="502 unreachable")
                     for _ in range(3 - len(recs))]
            return SimpleNamespace(recognitions=recs, text="eine lesart", loops=0,
                                   max_pairwise_cer=cer, usable=usable)
        monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", fake)

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: {"source_description": "", "source_json": {},
                                     "low_confidence": True})
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities", lambda *a, **k: {})
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    return SimpleNamespace(make=make, img=img)


def test_a_single_usable_candidate_yields_no_quality_score(rig):
    """The live case: two engines down, one reading, reported qa=1.0."""
    rig.make(usable=1)
    result = orchestrator.run_full_pipeline_group("order-367", [str(rig.img)])
    assert result["a_meta"]["qa_score"] != 1.0


def test_two_agreeing_candidates_still_score_one(rig):
    """The guard must not suppress a score that was genuinely earned."""
    rig.make(usable=2, cer=0.0)
    result = orchestrator.run_full_pipeline_group("order-367-ok", [str(rig.img)])
    assert result["a_meta"]["qa_score"] == 1.0


def test_two_disagreeing_candidates_score_normally(rig):
    rig.make(usable=2, cer=0.30)
    result = orchestrator.run_full_pipeline_group("order-367-dis", [str(rig.img)])
    assert result["a_meta"]["qa_score"] == 0.7


# ── the report must say what actually happened ───────────────────────────────

# The equivalent log assertion is deliberately absent: the project logs through
# loguru, which pytest's caplog does not capture, and the board event below carries
# the same claim in the place the historian actually reads it.


def test_the_board_marks_an_unmeasurable_page(rig):
    """A page where the engines mostly failed must be visible as such, not as a
    green tick — this is coverage information (#333) in another guise."""
    rig.make(usable=1)
    seen = []
    orchestrator.run_full_pipeline_group("order-367-board", [str(rig.img)],
                                         on_phase=seen.append)
    ensembles = [e for e in seen if "ensemble:" in (e.decision or "")]
    assert ensembles and ensembles[-1].status == "error"
    assert "1 usable of 3" in ensembles[-1].decision
