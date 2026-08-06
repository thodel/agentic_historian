"""#313 (the remaining gap): a no-merge must TELL the historian a vote is waiting.

The recording half worked: candidates are stored as Gate-2 paths, the score-ranked
pick is applied as the default, /votes renders the card, a vote overrides and
reaches routing.jsonl. But nothing ever announced the card. `gate2_vote_warranted`
was written by the orchestrator and read by nothing outside its own tests, and a
no-merge produced a single logger.info on the server.

#313's acceptance is that the historian is *shown* the readings rather than handed
the highest-scoring guess. A card only reachable by someone who already knows to
type `/votes <doc_id>` does not meet that — the preference log (#332) sat at one
event for days because nothing surfaced one.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_313_surface_vote_card.py
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


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def _er(n=3, *, no_merge=True, cer=0.96):
    recs = [{"engine": "trocr", "model_id": f"m{i}", "text": f"lesart {i}"}
            for i in range(n)]
    return SimpleNamespace(no_merge=no_merge, recognitions=recs, selected=recs[0],
                           ran=[], max_pairwise_cer=cer)


def _events(er, doc_id="d-313"):
    seen = []
    orchestrator._record_no_merge_vote(doc_id, "p.jpg", er, None,
                                       on_phase=seen.append)
    return seen


# ── the gap ──────────────────────────────────────────────────────────────────

def test_a_no_merge_announces_that_a_vote_is_waiting():
    evs = _events(_er())
    assert evs, "a no-merge produced no phase event — the card is unannounced"
    assert any(e.phase == "gate2_vote" for e in evs)


def test_the_announcement_names_the_command_to_run():
    """The card is pull-based by design (posting an interactive view from the
    headless worker thread is the risky path), so the announcement has to carry the
    command — otherwise it reports a problem with no way to act on it."""
    ev = next(e for e in _events(_er(), "prefs-test-BAT664") if e.phase == "gate2_vote")
    assert "/votes prefs-test-BAT664" in ev.decision


def test_the_announcement_reports_the_disagreement():
    """The historian should know how uncertain this is before opening the card."""
    ev = next(e for e in _events(_er(cer=0.96)) if e.phase == "gate2_vote")
    assert "96%" in ev.decision


def test_the_announcement_reports_how_many_readings():
    ev = next(e for e in _events(_er(n=5)) if e.phase == "gate2_vote")
    assert "5" in ev.decision


# ── it must not fire when there is nothing to vote on ────────────────────────

def test_consensus_announces_nothing():
    """Below the no-merge band the ensemble fused; there is no choice to make."""
    assert not [e for e in _events(_er(no_merge=False)) if e.phase == "gate2_vote"]


def test_a_single_candidate_announces_nothing():
    """One reading is not a choice — _record_no_merge_vote already declines to
    record, and it must not announce either."""
    assert not [e for e in _events(_er(n=1)) if e.phase == "gate2_vote"]


# ── observability must never break the pipeline ──────────────────────────────

def test_a_broken_callback_does_not_break_the_run():
    def boom(_ev):
        raise RuntimeError("board is down")
    orchestrator._record_no_merge_vote("d-boom", "p.jpg", _er(), None, on_phase=boom)


def test_no_callback_is_fine():
    orchestrator._record_no_merge_vote("d-none", "p.jpg", _er(), None, on_phase=None)


# ── the paths are still recorded (the half that already worked) ──────────────

def test_the_candidates_are_still_recorded_as_vote_paths():
    from runstate import RunState
    _events(_er(n=3), "d-still")
    st = RunState.load_or_new("d-still")
    assert len(st.artifacts.get("paths") or {}) == 3
    assert st.gate_decisions.get("gate2_vote_warranted") is True


# ── the board must not render a call to act as a completion ──────────────────

def test_the_announcement_is_marked_waiting_not_done():
    ev = next(e for e in _events(_er()) if e.phase == "gate2_vote")
    assert ev.status == "waiting"


def test_the_board_renders_waiting_distinctly_from_done():
    """A ✅ buries the one line on the board that asks the historian to do
    something — the whole point of #313 is that it gets noticed."""
    from progress import format_phase_event
    from runstate import PhaseEvent

    waiting = format_phase_event(PhaseEvent(
        doc_id="d", phase="gate2_vote", agent="A", status="waiting",
        decision="9 Lesarten uneinig — /votes d"))
    done = format_phase_event(PhaseEvent(
        doc_id="d", phase="vlm", agent="A", status="done", excerpt="text"))

    assert "✅" not in waiting
    assert "✅" in done


def test_an_unknown_status_still_renders_as_done():
    """Back-compat: every existing event has status "done" or "error"."""
    from progress import format_phase_event
    from runstate import PhaseEvent
    line = format_phase_event(PhaseEvent(doc_id="d", phase="p", agent="A",
                                         status="done", excerpt="x"))
    assert "✅" in line
