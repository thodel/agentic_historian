"""Tests for #145 (HITL-1a): RunState + stage-invalidation state machine.

Offline, pure logic + tmp-file persistence. Run from the repo root:
    pytest agentic_historian/tests/test_ah_145_runstate.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from runstate import RunState, StageResult, PhaseEvent, STAGES, DONE, DIRTY, PENDING


def _all_done(doc="d1") -> RunState:
    rs = RunState(doc_id=doc)
    for s in STAGES:
        rs.stage_status[s] = DONE
        rs.artifacts[s] = f"artifact-{s}"
    return rs


# ── invalidation matrix ──────────────────────────────────────────────────────

def test_invalidate_century_dirties_exact_set():
    rs = _all_done()
    dirty = rs.invalidate("century", value=16, user="tobias")
    assert set(dirty) == {"model_select", "kraken", "reconcile", "agent_b", "agent_c"}
    assert set(rs.dirty_stages()) == {"model_select", "kraken", "reconcile", "agent_b", "agent_c"}
    # VLM is NOT re-run when only the century changes
    assert rs.stage_status["vlm"] == DONE
    # agent_d is stale-flagged, not dirty
    assert rs.stage_status["agent_d"] == DONE and "agent_d" in rs.stale


def test_invalidate_script_is_narrower():
    rs = _all_done()
    rs.invalidate("script", value="Kurrent")
    assert set(rs.dirty_stages()) == {"model_select", "kraken", "reconcile", "agent_b"}
    assert rs.stage_status["agent_c"] == DONE           # script doesn't dirty C
    assert rs.stale == []


def test_invalidate_pins_criteria_and_records_override():
    rs = _all_done()
    rs.invalidate("lang", value="de", user="u")
    assert rs.criteria["lang"] == "de"                  # pinned (authoritative)
    assert rs.human_overrides[-1]["field"] == "lang"
    assert rs.human_overrides[-1]["value"] == "de"
    assert rs.human_overrides[-1]["user"] == "u"


def test_entity_link_writes_hub_no_stage_rerun():
    rs = _all_done()
    dirty = rs.invalidate("entity_link", value="gnd:118")
    assert dirty == [] and rs.dirty_stages() == []


def test_unknown_field_raises():
    with pytest.raises(ValueError):
        RunState(doc_id="d").invalidate("nonsense", value=1)


# ── resume: only pending/dirty stages run ────────────────────────────────────

def test_resume_fresh_runs_all_in_order():
    rs = RunState(doc_id="d")
    called = []

    def make(stage):
        def _r(state):
            called.append(stage)
            return StageResult(artifact=f"out-{stage}", agent=stage,
                               excerpt=f"text from {stage}", decision=f"decided {stage}")
        return _r

    ran = rs.resume({s: make(s) for s in STAGES})
    assert ran == list(STAGES) and called == list(STAGES)
    assert all(rs.stage_status[s] == DONE for s in STAGES)


def test_resume_after_invalidate_runs_only_dirty():
    rs = _all_done()
    rs.invalidate("century", value=16)
    called = []

    def make(stage):
        return lambda state: (called.append(stage) or
                              StageResult(artifact=f"new-{stage}", agent=stage))

    ran = rs.resume({s: make(s) for s in STAGES})
    assert set(ran) == {"model_select", "kraken", "reconcile", "agent_b", "agent_c"}
    assert "vlm" not in called and "agent_d" not in called
    # the untouched VLM artifact is preserved
    assert rs.artifacts["vlm"] == "artifact-vlm"
    assert rs.artifacts["kraken"] == "new-kraken"


def test_resume_emits_phase_events_with_excerpt_and_decision():
    rs = RunState(doc_id="d")
    events: list[PhaseEvent] = []

    def runner(state):
        return StageResult(artifact="x", agent="vlm",
                           excerpt="Wir Hans von Wiler tuend kund…",
                           decision="qa_score=0.8 source=vlm")

    rs.resume({"vlm": runner}, on_phase=events.append)
    assert len(events) == 1
    ev = events[0]
    assert ev.phase == "vlm" and ev.status == DONE
    assert ev.excerpt.startswith("Wir Hans") and "qa_score=0.8" in ev.decision


def test_resume_records_stage_error():
    rs = RunState(doc_id="d")
    events = []
    rs.resume({"vlm": lambda s: StageResult(error="gpustack down")},
              on_phase=events.append)
    assert rs.stage_status["vlm"] == "error"
    assert events[0].status == "error" and events[0].error == "gpustack down"


def test_missing_runner_skips_stage():
    rs = RunState(doc_id="d")
    ran = rs.resume({"vlm": lambda s: StageResult(artifact="x", agent="vlm")})
    assert ran == ["vlm"]
    assert rs.stage_status["agent_d"] == PENDING          # no runner → left pending


# ── persistence ──────────────────────────────────────────────────────────────

def test_roundtrip_json(tmp_path):
    rs = _all_done("saa-0428")
    rs.invalidate("century", value=15, user="t")
    p = rs.save(path=tmp_path / "saa.json")
    loaded = RunState.load("saa-0428", path=p)
    assert loaded.doc_id == "saa-0428"
    assert loaded.criteria["century"] == 15
    assert set(loaded.dirty_stages()) == set(rs.dirty_stages())
    assert loaded.human_overrides == rs.human_overrides
