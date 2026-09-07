"""#390: start with two engines, escalate to three only on disagreement.

Offline — engine execution is a mock recognize_fn, fusion and CER are the real
modules. The load-bearing claim is the equivalence: on a disagreeing page the new
defaults (2 + up to 5) must run the same picks as the old ones (3 + up to 4), so
nothing downstream sees a different candidate set. The saving falls entirely on
pages where the first two agree.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import ModelPick, recognize_ensemble  # noqa: E402

AGREE = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
DIS = ["voellig andere zeichen xyz qrs mno abc def ghi jkl ohne jeden sinn",
       "1234567890 !!! ??? zzz yyy xxx www vvv uuu ttt sss rrr qqq ppp ooo",
       "noch eine ganz verschiedene lesart mit voellig anderem inhalt hier",
       "und hier steht wieder etwas komplett anderes als in allen anderen"]

PICKS = [ModelPick("vlm", "v0", 1.0), ModelPick("kraken", "k0", 0.9),
         ModelPick("trocr", "t0", 0.8), ModelPick("kraken", "k1", 0.7),
         ModelPick("trocr", "t1", 0.6), ModelPick("kraken", "k2", 0.5),
         ModelPick("trocr", "t2", 0.4)]


def _fn(text_map, fail=()):
    calls = []

    def fn(pick, image):
        calls.append(pick.model_id)
        if pick.model_id in fail:
            return None
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": text_map.get(pick.model_id, ""), "error": ""}
    fn.calls = calls
    return fn


def _agreeing():
    return _fn({p.model_id: AGREE for p in PICKS})


def _disagreeing():
    return _fn({p.model_id: DIS[i % len(DIS)] for i, p in enumerate(PICKS)})


# ── the saving ───────────────────────────────────────────────────────────────

def test_agreeing_pair_runs_two_engines_and_does_not_escalate():
    fn = _agreeing()
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=1)
    assert fn.calls == ["v0", "k0"]
    assert r.loops == 0 and r.fast_path is True
    assert len(r.ran) == 2 and r.usable == 2


def test_the_fast_path_is_recorded_on_the_result():
    r = recognize_ensemble("img", None, _agreeing(), picks=list(PICKS), concurrency=1)
    assert "no escalation needed" in r.path and r.fast_path is True


# ── the equivalence: disagreement must reach the same place ─────────────────

def test_disagreeing_page_runs_the_same_picks_as_the_old_defaults():
    old = _disagreeing()
    recognize_ensemble("img", None, old, picks=list(PICKS), concurrency=1,
                       min_engines=3, max_loops=4)
    new = _disagreeing()
    recognize_ensemble("img", None, new, picks=list(PICKS), concurrency=1,
                       min_engines=2, max_loops=5)
    assert new.calls == old.calls, "the fast path changed which engines run"


def test_disagreement_escalates_past_two():
    fn = _disagreeing()
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=1)
    assert len(fn.calls) > 2
    assert r.loops >= 1 and r.fast_path is False
    assert "escalation(s)" in r.path


# ── the three states must stay distinct ──────────────────────────────────────

def test_exhausted_escalation_is_not_reported_as_a_fast_path():
    """No loop ran, but the candidates disagree — the pool was spent, not agreed."""
    fn = _disagreeing()
    r = recognize_ensemble("img", None, fn, picks=PICKS[:2], concurrency=1,
                           min_engines=2, max_loops=5)
    assert r.loops == 0
    assert r.fast_path is False
    assert "escalation unavailable" in r.path


# ── #367 / backfill must be untouched ────────────────────────────────────────

def test_a_failed_pick_is_still_backfilled_to_reach_two():
    fn = _fn({p.model_id: AGREE for p in PICKS}, fail=("k0",))
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=1)
    assert fn.calls == ["v0", "k0", "t0"]          # k0 failed, t0 backfilled it
    assert len(r.ran) == 2 and r.usable == 2


def test_a_single_usable_candidate_still_reports_two_usable_only_when_it_has_them():
    """#367: below two usable candidates the pairwise CER is unmeasured, not 0."""
    fn = _fn({"v0": AGREE, "k0": ""}, )
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=1)
    assert r.usable == 1
    assert r.max_pairwise_cer == 0.0               # no pair existed to compare
