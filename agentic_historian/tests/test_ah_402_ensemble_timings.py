"""#402: where the ensemble's wall time actually goes, measured in the code.

Three separate diagnoses in this project were wrong because a gap between two log
lines was attributed to the event the first one named:

  - "the VLM dominates at 141s"      → the VLM is the FASTEST engine (5.9s)
  - "a cold model load takes 90-130s" → measured at 1.5s; models are 16-42 MB
  - and the E2 argument built on the first of those

A page took 152s while its three engine calls summed to 62s. The 90s difference
was the interesting number every time, and it was never measured. `timings` makes
the residual explicit rather than inferable.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_402_ensemble_timings.py
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import ensemble  # noqa: E402
from agent_a.model_selector import RecognitionResult, SourceCriteria  # noqa: E402


def _pick(engine, model_id="m", score=0.5):
    return ensemble.ModelPick(engine=engine, model_id=model_id, score=score)


def _fn(delays):
    """recognize_fn that sleeps a per-engine amount, so timings are checkable."""
    def run(pick, image):
        time.sleep(delays.get(pick.engine, 0.0))
        return RecognitionResult(engine=pick.engine, model_id=pick.model_id,
                                 text=f"lesart von {pick.engine}", confidence=0.5)
    return run


PICKS = [_pick("vlm"), _pick("kraken"), _pick("trocr")]


# ── the phases add up ────────────────────────────────────────────────────────

def test_every_phase_is_recorded():
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({}), picks=list(PICKS), concurrency=1)
    for key in ("plan", "initial", "escalation", "fuse", "total", "other"):
        assert key in er.timings, f"{key} fehlt: {er.timings}"


def test_the_named_phases_plus_the_residual_are_the_total():
    """`other` exists so the unexplained part is computed, not inferred."""
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({}), picks=list(PICKS), concurrency=1)
    t = er.timings
    named = t["plan"] + t["initial"] + t["escalation"] + t["fuse"]
    assert abs(named + t["other"] - t["total"]) < 0.05


# ── per call, not just per phase ─────────────────────────────────────────────

def test_each_engine_call_is_timed_separately():
    """A phase total cannot say whether the cost is one slow engine or many — the
    distinction that decides #390 (drop an engine) and #402 (parallelise more)."""
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({"trocr": 0.3}), picks=list(PICKS),
        concurrency=1)
    by_engine = {c["engine"]: c["s"] for c in er.timings["calls"]}
    assert set(by_engine) == {"vlm", "kraken", "trocr"}
    assert by_engine["trocr"] >= 0.3
    assert by_engine["vlm"] < 0.3


def test_a_failing_call_is_timed_and_marked():
    """A backend that blows up after 40s is a cost; recording only successes would
    hide it — and an engine outage is exactly when timings get read (#367)."""
    def boom(pick, image):
        time.sleep(0.1)
        if pick.engine == "kraken":
            raise RuntimeError("502")
        return RecognitionResult(engine=pick.engine, model_id=pick.model_id,
                                 text="t", confidence=0.5)

    er = ensemble.recognize_ensemble("img.jpg", SourceCriteria(), boom,
                                     picks=list(PICKS), concurrency=1)
    failed = [c for c in er.timings["calls"] if not c["ok"]]
    assert failed and failed[0]["engine"] == "kraken"
    assert failed[0]["s"] >= 0.1


def test_calls_sum_is_the_sequential_cost():
    """Against `total` this shows what concurrency actually bought: 62s of calls in
    a 152s page means the engines are not the problem."""
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({"vlm": 0.1, "kraken": 0.1, "trocr": 0.1}),
        picks=list(PICKS), concurrency=1)
    assert er.timings["calls_sum"] >= 0.3


# ── concurrency must show up as a difference between sum and wall ────────────

def test_concurrency_makes_the_wall_time_shorter_than_the_call_sum():
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({"vlm": 0.3, "kraken": 0.3, "trocr": 0.3}),
        picks=list(PICKS), concurrency=3)
    assert er.timings["calls_sum"] >= 0.9
    assert er.timings["initial"] < 0.7, er.timings      # ~max, not ~sum


def test_sequential_wall_time_matches_the_call_sum():
    er = ensemble.recognize_ensemble(
        "img.jpg", SourceCriteria(), _fn({"vlm": 0.2, "kraken": 0.2, "trocr": 0.2}),
        picks=list(PICKS), concurrency=1)
    assert er.timings["initial"] >= 0.55


# ── it must not change what the ensemble decides ─────────────────────────────

def test_timing_does_not_alter_the_result():
    """Instrumentation that changes the outcome measures itself."""
    kw = dict(picks=list(PICKS), concurrency=1)
    a = ensemble.recognize_ensemble("img.jpg", SourceCriteria(), _fn({}), **kw)
    assert a.text and len(a.recognitions) == 3
    assert a.usable == 3 and a.loops == 0


def test_timings_survive_the_no_merge_path():
    """Two return paths; both must carry the measurement or half the runs go dark."""
    def disagree(pick, image):
        time.sleep(0.05)          # so the rounded seconds are non-zero at all
        texts = {"vlm": "völlig anderer text hier",
                 "kraken": "ganz etwas anderes zzz",
                 "trocr": "drittens noch etwas xyz"}
        return RecognitionResult(engine=pick.engine, model_id=pick.model_id,
                                 text=texts[pick.engine], confidence=0.5)

    er = ensemble.recognize_ensemble("img.jpg", SourceCriteria(), disagree,
                                     picks=list(PICKS), concurrency=1,
                                     no_merge_cer=0.01, max_loops=0)
    assert er.no_merge is True
    assert er.timings.get("total", 0) > 0 and "calls" in er.timings
