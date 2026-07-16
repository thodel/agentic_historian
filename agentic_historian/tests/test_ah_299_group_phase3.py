"""#299: grouped orders re-run recognition with Agent B's criteria.

The grouped path — the one that actually processes orders, and produced the
u-17__/bat garbage — had no Phase 3. It planned with EMPTY criteria (no
description exists yet), picked blind, and then threw away what Agent B learned.
Measured on tei 2026-07-16:

    [model_selector]       Best match: CatMuS Medieval (score=0.05, no match)
    [model_selector/trocr] Best match: TrOCR Medieval EscriptMask (score=0.20)
    [ensemble] loop 2: added trocr/dh-unibe/trocr-kurrent-XVI-XVII   ← right model, by accident

Offline — the ensemble and every agent are stubbed; no GPUStack/ATR. Run from the
repo root:
    pytest agentic_historian/tests/test_ah_299_group_phase3.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402
from agent_a.model_selector import RecognitionResult  # noqa: E402

BLIND_TEXT = "duser feunilite grus vor liebe gerrmreuon de scosse"
GOOD_TEXT = "Vnser fründlich grus vor liebe getrune von der stösse wyse"

# Agent B correctly identifies the source — this is the knowledge Phase 1 lacked.
DESCRIPTION = {
    "source_description": "Urkunde, Kurrent, 16. Jh., Papier",
    "source_json": {"schrift": {"wert": "Kurrent"}, "jahrhundert": {"wert": "16"}},
    "low_confidence": False,
}
DEGENERATE_DESCRIPTION = {
    "source_description": "Transkription unlesbar oder degeneriert",
    "source_json": {},
    "low_confidence": True,          # the #276 guard fired
}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A grouped run with the ensemble on; records the criteria of each pass."""
    passes = []

    def fake_ensemble(img, criteria):
        passes.append(criteria)
        first = len(passes) == 1
        return SimpleNamespace(
            recognitions=[
                RecognitionResult(engine="kraken", model_id="catmus_medieval",
                                  text=BLIND_TEXT, confidence=0.3)
                if first else
                RecognitionResult(engine="trocr", model_id="trocr-kurrent-xvi-xvii",
                                  text=GOOD_TEXT, confidence=0.9),
            ],
            text=BLIND_TEXT if first else GOOD_TEXT,
            loops=1, max_pairwise_cer=0.7 if first else 0.1,
        )

    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator.config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", fake_ensemble)
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.agent_b, "describe", lambda **k: DESCRIPTION)
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities",
                        lambda *a, **k: {"entities": []})
    monkeypatch.setattr(orchestrator, "_save_pipeline_result", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_publish_outputs", lambda *a, **k: (False, "off"))
    monkeypatch.setattr(orchestrator.config, "DATA_DIR", tmp_path)

    img = tmp_path / "p1.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return SimpleNamespace(img=img, passes=passes, tmp=tmp_path)


# ── the loop closes ──────────────────────────────────────────────────────────

def test_agent_b_criteria_drive_a_second_pass(rig):
    orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    assert len(rig.passes) == 2, "Phase 3 never re-ran with Agent B's criteria"
    blind, from_b = rig.passes
    assert not blind.script and not blind.century        # Phase 1 knows nothing
    assert from_b.script == "kurrent"                    # Phase 3 knows the source
    assert from_b.century == 16


def test_the_criteria_pass_supplies_the_final_transcription(rig):
    result = orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    assert GOOD_TEXT in result["transcription"]
    assert BLIND_TEXT not in result["transcription"]
    assert result["a_meta"]["criteria_rerun"] is True
    assert result["a_meta"]["source"] == "grouped-ensemble-criteria"


def test_first_pass_candidates_are_kept_as_evidence(rig):
    """The blind pass is not discarded — #284 exports every candidate so the
    historian can compare what each engine actually read."""
    result = orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    texts = [r["text"] for r in result["recognitions"]]
    assert BLIND_TEXT in texts and GOOD_TEXT in texts
    assert len(result["recognitions"]) == 2


# ── the acceptance criterion, on the real selector ───────────────────────────

def test_agent_b_criteria_pick_the_right_models_not_blind_fallbacks(rig):
    """#299's acceptance: the criteria-driven pass picks the models the source
    actually calls for, instead of the blind fallbacks.

    Uses the real plan_models/selector (pure, no I/O) rather than a stub: the claim
    is about model selection, so stubbing the selector would prove nothing.

    Measured, blind → from Agent B:
        kraken  0.05 (zenodo.7516057)   → 0.80 (Early Modern German 16.-17. Jh.)
        trocr   0.20 (escriptmask)      → 0.40 (trocr-kurrent-XVI-XVII)

    The issue text claimed "score >= 0.8" for the TrOCR pick; that was a guess when
    it was written and it is wrong — the scorer gives the correct Kurrent model
    0.40. What matters is the identity of the pick and that criteria beat blindness,
    so that is what this asserts.
    """
    from agent_a import ensemble
    from agent_a.model_selector import SourceCriteria

    blind = SourceCriteria()
    from_b = SourceCriteria.from_agent_b_and_json(
        DESCRIPTION["source_description"], DESCRIPTION["source_json"])

    def top(picks, engine):
        return next((p for p in picks if p.engine == engine), None)

    blind_picks, b_picks = ensemble.plan_models(blind), ensemble.plan_models(from_b)

    # TrOCR: the model that produced the only good reading on BAT_664 (#298)
    b_trocr, blind_trocr = top(b_picks, "trocr"), top(blind_picks, "trocr")
    assert b_trocr.model_id == "dh-unibe/trocr-kurrent-XVI-XVII", b_trocr.model_id
    assert b_trocr.score > blind_trocr.score          # 0.40 > 0.20

    # kraken: a Kurrent/16th-c. model instead of a medieval Latin fallback
    b_kraken, blind_kraken = top(b_picks, "kraken"), top(blind_picks, "kraken")
    assert b_kraken.score >= 0.8                      # 0.80 — a real match
    assert b_kraken.score > blind_kraken.score        # 0.80 > 0.05
    assert b_kraken.model_id != blind_kraken.model_id


# ── guards ───────────────────────────────────────────────────────────────────

def test_no_rerun_when_agent_b_has_no_usable_description(rig, monkeypatch):
    """A low_confidence description yields criteria as empty as Phase 1's — a
    re-run would burn GPU to reach the same blind picks. See #301."""
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: DEGENERATE_DESCRIPTION)

    orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    assert len(rig.passes) == 1, "re-ran on a description Agent B refused to make"


def test_ensemble_off_is_unchanged(rig, monkeypatch):
    monkeypatch.setattr(orchestrator.config, "ENABLE_ENSEMBLE_HTR", False)
    monkeypatch.setattr(orchestrator.agent_a, "transcribe_image",
                        lambda img, **k: {"transcription": "vlm only", "qa_score": 0.5})

    result = orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    assert rig.passes == []                       # ensemble never ran at all
    assert "vlm only" in result["transcription"]


def test_a_failing_rerun_does_not_lose_the_first_pass(rig, monkeypatch):
    """If Phase 3 blows up, the run keeps Phase 1's transcription rather than
    returning nothing — the re-run is an improvement, not a dependency."""
    calls = {"n": 0}

    def flaky(img, criteria):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("ATR gateway down")
        return SimpleNamespace(
            recognitions=[RecognitionResult(engine="kraken", model_id="catmus_medieval",
                                            text=BLIND_TEXT, confidence=0.3)],
            text=BLIND_TEXT, loops=1, max_pairwise_cer=0.7)

    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", flaky)
    result = orchestrator.run_full_pipeline_group("order-299", [str(rig.img)])

    assert BLIND_TEXT in result["transcription"]
    assert result["entities"] is not None          # the run still completed


def test_the_rerun_is_visible_in_the_phase_events(rig):
    events = []
    orchestrator.run_full_pipeline_group("order-299", [str(rig.img)],
                                         on_phase=events.append)

    sel = [e for e in events if e.phase == "model_select"]
    assert len(sel) == 2                           # one plan per pass
    assert "from Agent B" in sel[1].decision
    assert any("pass 2 (criteria)" in e.decision for e in events if e.phase == "vlm")
