"""Tests for #149 (HITL-2b): Gate 2 path-comparison card with measured CER.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_149_gate2_compare.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import path_compare as pc
from runstate import RunState, DONE, STAGES


def _state():
    rs = RunState(doc_id="saa-0428")
    for s in STAGES:
        rs.stage_status[s] = DONE
    return rs


_AGREE = {
    "vlm": "Wir Hans von Wiler tuend kund allen den die disen brief ansehent",
    "kraken": "Wir Hans von Wiler tuend kund allen den die disen brief ansehent",
    "reconciled": "Wir Hans von Wiler tuend kund allen den die disen brief ansehent",
}
_DISAGREE = {
    "vlm": "Wir Hans von Wiler tuend kund allen den die disen brief ansehent",
    "kraken": "XYZ voellig andere Zeichen ohne jede Uebereinstimmung 123456",
    "reconciled": "Wir Hans von Wiler tuend kund allen den die disen brief ansehent",
}


# ── comparison + gating ──────────────────────────────────────────────────────

def test_compare_paths_computes_pairwise_cer():
    comp = pc.compare_paths(_DISAGREE)
    assert set(comp["names"]) == {"vlm", "kraken", "reconciled"}
    assert comp["pairs"][("vlm", "kraken")] > 0.3      # big disagreement
    assert comp["pairs"][("vlm", "reconciled")] == 0.0  # identical
    assert comp["max_cer"] > 0.3


def test_compare_ignores_empty_paths():
    comp = pc.compare_paths({"vlm": "abc", "kraken": "", "reconciled": None})
    assert comp["names"] == ["vlm"] and comp["pairs"] == {}


def test_should_gate_true_on_disagreement_false_on_agreement():
    assert pc.should_gate(_DISAGREE) is True
    assert pc.should_gate(_AGREE) is False
    # a single path never gates
    assert pc.should_gate({"vlm": "only one"}) is False


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_shows_snippets_and_cer():
    card = pc.render_compare_card(_state(), _DISAGREE, snippet=40)
    assert "Transkriptionsvergleich" in card
    assert "VLM" in card and "Kraken" in card and "Reconciled" in card
    assert "CER" in card


def test_render_notes_auto_choice_when_agreeing():
    card = pc.render_compare_card(_state(), _AGREE)
    assert "auto-gewählt" in card


# ── choice applies invalidation ──────────────────────────────────────────────

def test_apply_path_choice_dirties_bc_and_sets_working_text():
    st = _state()
    text = pc.apply_path_choice(st, "kraken", _DISAGREE)
    assert text == _DISAGREE["kraken"]
    # per the invalidation matrix, path_preference dirties reconcile/agent_b/agent_c
    assert set(st.dirty_stages()) == {"reconcile", "agent_b", "agent_c"}
    assert st.stage_status["vlm"] == DONE and st.stage_status["model_select"] == DONE
    assert st.artifacts["reconcile"] == _DISAGREE["kraken"]
    assert st.gate_decisions["path"] == "kraken"


def test_apply_path_choice_then_resume_runs_only_bc(monkeypatch):
    st = _state()
    called = []
    pc.apply_path_choice(st, "vlm", _DISAGREE)
    st.resume({s: (lambda state, _s=s: called.append(_s) or __import__("runstate").StageResult(artifact="x"))
               for s in STAGES})
    assert set(called) == {"reconcile", "agent_b", "agent_c"}


def test_unknown_choice_raises():
    import pytest
    with pytest.raises(ValueError):
        pc.apply_path_choice(_state(), "nonsense", _DISAGREE)


# ── Discord View ─────────────────────────────────────────────────────────────

async def _build(st, paths):
    return pc.build_view(st, paths)


def test_build_view_has_button_per_path():
    import discord
    view = asyncio.run(_build(_state(), _DISAGREE))
    buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
    assert {b.custom_id for b in buttons} == {
        f"ah:saa-0428:gate2:{p}" for p in ("vlm", "kraken", "reconciled")}
