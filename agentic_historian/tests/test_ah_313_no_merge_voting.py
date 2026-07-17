"""#313: a no-merge decision is recorded as a voteable Gate-2 card.

#300 selects the best single candidate at high disagreement rather than blend, but
"best" there is the model-MATCH score — a prior on fit-to-source, not a measure of
output quality. On BAT_664 the 0.20-scored escriptmask read better than the
0.80-scored kurrent model (#298). At that disagreement level the right reading is a
human call, so the decision must be VOTEABLE without blocking.

This is the recording half: the auto-pick stays the working transcription and the
candidates are written as Gate-2 `paths`, so the existing voting card (#290/#293)
renders and a vote overrides via `voting.apply_winner` → `apply_path_choice`.
Posting the card to Discord is the #286/#289 half and is not covered here.

Offline — the ensemble and agents are stubbed. Run from the repo root:
    pytest agentic_historian/tests/test_ah_313_no_merge_voting.py
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
import voting          # noqa: E402
from runstate import RunState  # noqa: E402
from agent_a.ensemble import EnsembleResult  # noqa: E402
from agent_a.model_selector import RecognitionResult  # noqa: E402

# escriptmask read better despite the LOWER match score — the #298 evidence.
ESCRIPT = "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint"
KURRENT = "Vnser fründlich grus vor liebe getrune von der stösse wyse so daß nit"


def _no_merge_result():
    escript = RecognitionResult(engine="trocr", model_id="trocr-medieval-escriptmask",
                                text=ESCRIPT, confidence=0.5)
    kurrent = RecognitionResult(engine="trocr", model_id="trocr-kurrent-xvi-xvii",
                                text=KURRENT, confidence=0.5)
    # selected = the score-ranked winner (kurrent, 0.80) — which read WORSE
    return EnsembleResult(
        recognitions=[escript, kurrent], text=KURRENT,
        provenance=["no-merge: CER 105.9% > 35.0% — selected trocr/trocr-kurrent-xvi-xvii"],
        loops=0, max_pairwise_cer=1.059, no_merge=True, selected=kurrent)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator.config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble",
                        lambda img, criteria: _no_merge_result())
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)
    # Agent B has no criteria → no Phase-3 second pass; keeps the test to one pass.
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: {"source_description": "", "source_json": {},
                                     "low_confidence": True})
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities", lambda *a, **k: {})
    monkeypatch.setattr(orchestrator, "_save_pipeline_result", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_publish_outputs", lambda *a, **k: (False, "off"))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "VOTES_LOG_PATH", tmp_path / "votes.jsonl")
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)

    img = tmp_path / "p1.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return SimpleNamespace(img=img, tmp=tmp_path)


# ── recording ────────────────────────────────────────────────────────────────

def test_no_merge_records_candidates_as_gate2_paths(rig):
    orchestrator.run_full_pipeline_group("order-313", [str(rig.img)])

    state = RunState.load_or_new("order-313")
    paths = state.artifacts.get("paths") or {}
    texts = set(paths.values())
    assert ESCRIPT in texts and KURRENT in texts          # both readings voteable
    assert state.gate_decisions.get("gate2_vote_warranted") is True


def test_the_auto_pick_is_recorded_as_the_default(rig):
    orchestrator.run_full_pipeline_group("order-313", [str(rig.img)])

    state = RunState.load_or_new("order-313")
    auto = state.gate_decisions.get("gate2_auto", {})
    # the score-ranked winner (kurrent) is the default the vote can override
    assert any("trocr-kurrent-xvi-xvii" in v for v in auto.values())


def test_the_run_does_not_block(rig):
    """Recording must not stall an unattended run — it completes with the auto-pick
    as the working transcription."""
    result = orchestrator.run_full_pipeline_group("order-313", [str(rig.img)])
    assert KURRENT in result["transcription"]
    assert result["entities"] is not None                  # C still ran


# ── the vote overrides, via the existing path (#293) ─────────────────────────

def test_a_vote_overrides_the_auto_pick(rig, monkeypatch):
    orchestrator.run_full_pipeline_group("order-313", [str(rig.img)])
    state = RunState.load_or_new("order-313")
    paths = state.artifacts["paths"]

    # the historian votes for the escriptmask reading (the better one)
    winner_label = next(k for k, v in paths.items() if v == ESCRIPT)

    applied = {}
    import path_compare

    def fake_apply(s, c, p, **k):
        applied["choice"] = c
        return p[c]
    monkeypatch.setattr(path_compare, "apply_path_choice", fake_apply)

    voting.record_vote("order-313", winner_label, voter="hist1")
    text = voting.apply_winner(state, paths)

    assert text == ESCRIPT
    assert applied["choice"] == winner_label               # went through Gate 2's apply


# ── guard: a normal merge records nothing ────────────────────────────────────

def test_a_merged_page_records_no_vote(rig, monkeypatch):
    merged = EnsembleResult(
        recognitions=[RecognitionResult(engine="trocr", model_id="t0", text="agreed"),
                      RecognitionResult(engine="kraken", model_id="k0", text="agreed")],
        text="agreed", max_pairwise_cer=0.1, no_merge=False, selected=None)
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble",
                        lambda img, criteria: merged)

    orchestrator.run_full_pipeline_group("order-313b", [str(rig.img)])

    state = RunState.load_or_new("order-313b")
    assert not state.artifacts.get("paths")
    assert not state.gate_decisions.get("gate2_vote_warranted")


def test_a_single_candidate_no_merge_records_nothing(rig, monkeypatch):
    """Below 2 voteable candidates there is no choice to offer."""
    one = EnsembleResult(
        recognitions=[RecognitionResult(engine="trocr", model_id="t0", text="only one")],
        text="only one", max_pairwise_cer=0.9, no_merge=True,
        selected=RecognitionResult(engine="trocr", model_id="t0", text="only one"))
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble",
                        lambda img, criteria: one)

    orchestrator.run_full_pipeline_group("order-313c", [str(rig.img)])

    state = RunState.load_or_new("order-313c")
    assert not state.gate_decisions.get("gate2_vote_warranted")
