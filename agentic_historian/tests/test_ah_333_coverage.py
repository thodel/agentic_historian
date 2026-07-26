"""#333: did the ensemble offer an acceptable reading at all?

The one quality question a selection can answer without a reference text: accepting
something means the pool contained a usable option, rejecting means it did not. It
cannot be gamed by reproducing our own errors and is not capped by the candidate
pool — unlike CER against a chosen reading (#326).

Before this, the card had no way to say "none of these is usable", so an abandoned
page was indistinguishable from an unseen one and the negative signal was lost.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_333_coverage.py
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import path_compare    # noqa: E402
import preferences     # noqa: E402
import routing_report  # noqa: E402
from preferences import PreferenceEvent  # noqa: E402
from runstate import RunState  # noqa: E402

PAGE = "p1.jpg"
GOOD = f"{PAGE}:trocr/trocr-kurrent-xvi-xvii"
BAD = f"{PAGE}:kraken/kraken-catmus_medieval"
PATHS = {GOOD: "Vnser fründlich grus vor liebe", BAD: "duser feunilite grus"}


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "preferences.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "AUTO_RESUME_AFTER_GATE", False)
    return tmp_path


def _ev(*, accepted=True, rejected=False, script="kurrent", century=16,
        ts="2026-07-26T10:00:00+00:00"):
    return PreferenceEvent(
        doc_id="d", page=PAGE,
        offered=[{"engine": "trocr", "model_id": "trocr-kurrent-xvi-xvii"}],
        chosen=["trocr/trocr-kurrent-xvi-xvii"] if accepted else [],
        rejected=rejected, auto_pick="trocr/trocr-kurrent-xvi-xvii",
        criteria={"script": script, "century": century, "lang": "de"}, ts=ts,
    )


def _state(doc_id="d-333"):
    st = RunState(doc_id=doc_id)
    st.artifacts["paths"] = dict(PATHS)
    return st


class _Interaction:
    def __init__(self):
        self.user = SimpleNamespace(id=42, display_name="Anna", name="Anna")
        self.edited, self.views = [], []
        self.response = SimpleNamespace(edit_message=self._edit)

    async def _edit(self, *, content=None, view=None):
        self.edited.append(content)
        self.views.append(view)


def _click(state, field):
    async def go():
        view = path_compare.build_view(state, PATHS)
        btn = next(b for b in view.children if b.custom_id.endswith(":" + field))
        inter = _Interaction()
        await btn.callback(inter)
        return inter
    return asyncio.run(go())


# ── the negative signal the card could not express ───────────────────────────

def test_rejecting_records_a_coverage_failure_and_collapses_the_card():
    st = _state()
    inter = _click(st, "__reject__")

    evs = preferences.load_preferences()
    assert len(evs) == 1
    assert evs[0].rejected is True and evs[0].chosen == []
    assert inter.views[-1] is None                     # collapsed, like a confirm
    assert "keine Lesart brauchbar" in inter.edited[-1]


def test_the_reject_button_exists_on_the_card():
    async def go():
        return path_compare.build_view(_state(), PATHS)
    view = asyncio.run(go())
    assert any(b.custom_id.endswith(":__reject__") for b in view.children)


def test_a_rejection_carries_the_offered_candidates():
    """A coverage failure is only interpretable against what was on offer."""
    st = _state()
    _click(st, "__reject__")
    ev = preferences.load_preferences()[0]
    assert {o["model_id"] for o in ev.offered} == {
        "trocr-kurrent-xvi-xvii", "kraken-catmus_medieval"}


def test_rejecting_does_not_set_a_working_transcription():
    st = _state()
    _click(st, "__reject__")
    assert not st.artifacts.get("reconcile")
    assert st.gate_decisions.get("gate2_rejected") is True


# ── the metric ───────────────────────────────────────────────────────────────

def test_coverage_is_accepted_over_decided():
    out = routing_report.compute_coverage(
        [_ev(), _ev(), _ev(accepted=False, rejected=True)])
    assert out["overall"] == {"decided": 3, "accepted": 2, "rate": pytest.approx(2 / 3)}


def test_undecided_pages_are_excluded_not_counted_as_failures():
    """An event that is neither accepted nor rejected is unknown — counting silence
    as failure would make coverage fall simply because nobody has looked yet."""
    undecided = PreferenceEvent(doc_id="d", page=PAGE, chosen=[], rejected=False)
    out = routing_report.compute_coverage([_ev(), undecided])
    assert out["overall"]["decided"] == 1
    assert out["overall"]["rate"] == 1.0


def test_coverage_slices_by_bucket():
    out = routing_report.compute_coverage([
        _ev(script="kurrent", century=16),
        _ev(script="bastarda", century=15, accepted=False, rejected=True),
    ])
    assert out["by_bucket"][("kurrent", 16, "de")]["rate"] == 1.0
    assert out["by_bucket"][("bastarda", 15, "de")]["rate"] == 0.0


def test_an_empty_bucket_reports_no_data_not_zero_percent():
    """"no data" and "nothing was usable" are different claims and must not look
    the same on a report."""
    out = routing_report.compute_coverage([])
    assert out["overall"]["rate"] is None
    assert out["overall"]["rate"] != 0.0


def test_coverage_trends_over_time():
    out = routing_report.compute_coverage([
        _ev(ts="2026-06-01T10:00:00+00:00", accepted=False, rejected=True),
        _ev(ts="2026-07-01T10:00:00+00:00"),
    ])
    assert out["by_month"]["2026-06"]["rate"] == 0.0
    assert out["by_month"]["2026-07"]["rate"] == 1.0


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_states_coverage_and_the_worst_bucket():
    text = routing_report.format_coverage_stats([
        _ev(script="kurrent", century=16),
        _ev(script="bastarda", century=15, accepted=False, rejected=True),
    ])
    assert "1/2" in text or "50%" in text
    assert "bastarda" in text


def test_the_report_is_honest_when_there_is_no_data():
    assert "noch keine" in routing_report.format_coverage_stats([]).lower()


# ── accept still works (the reject must not have broken the confirm path) ────

def test_confirming_still_records_an_acceptance():
    st = _state()
    st.gate_decisions["gate2_selected"] = [GOOD]
    _click(st, "__confirm__")

    evs = preferences.load_preferences()
    assert len(evs) == 1 and evs[0].rejected is False and evs[0].chosen
    assert routing_report.compute_coverage(evs)["overall"]["rate"] == 1.0
