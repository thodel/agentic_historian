"""#402: the escalation loop sends its picks in batches.

Escalation was 64 % of a run and strictly serial — each added model waited for
the previous one before the code decided whether to add another. Batching trades
that wait for the chance of overshooting: the first pick alone may settle the
disagreement, and the second then ran for nothing.

What these tests hold is that the trade is bounded and visible — the batch never
exceeds the budget, the overshoot is counted, and batch=1 is exactly the old
behaviour.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import ModelPick, recognize_ensemble

AGREE = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
DIS = ["voellig andere zeichen xyz qrs mno abc def ghi jkl ohne jeden sinn",
       "1234567890 !!! ??? zzz yyy xxx www vvv uuu ttt sss rrr qqq ppp ooo",
       "noch eine ganz verschiedene lesart mit voellig anderem inhalt hier",
       "und hier steht wieder etwas komplett anderes als in allen anderen"]

PICKS = [ModelPick("vlm", "v0", 1.0), ModelPick("kraken", "k0", 0.9),
         ModelPick("trocr", "t0", 0.8), ModelPick("kraken", "k1", 0.7),
         ModelPick("trocr", "t1", 0.6), ModelPick("kraken", "k2", 0.5),
         ModelPick("trocr", "t2", 0.4)]


def _fn(text_map):
    calls = []

    def fn(pick, image):
        calls.append(pick.model_id)
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": text_map.get(pick.model_id, ""), "error": ""}
    fn.calls = calls
    return fn


def _disagreeing():
    return _fn({p.model_id: DIS[i % len(DIS)] for i, p in enumerate(PICKS)})


# ── the saving ───────────────────────────────────────────────────────────────

def test_a_batch_of_two_goes_out_together():
    fn = _disagreeing()
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=3,
                           min_engines=2, max_loops=4, escalation_batch=2)
    # Two initial + two batches of two = six picks, in pool order.
    assert fn.calls == ["v0", "k0", "t0", "k1", "t1", "k2"]
    assert r.loops == 4
    assert [p.model_id for p in r.ran] == fn.calls   # order is pool, not completion


def test_pool_order_survives_out_of_order_completion():
    """Ranking, provenance and the Gate-2 card must not depend on who answered first."""
    import time

    order = {p.model_id: i for i, p in enumerate(PICKS)}

    def fn(pick, image):
        time.sleep(0.05 if pick.model_id == "t0" else 0.0)   # the batch's first is slow
        # Indexed, not hashed: PYTHONHASHSEED differs per process, so hash() here
        # made the candidate texts vary between runs and the test flaky.
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": DIS[order[pick.model_id] % len(DIS)], "error": ""}

    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=3,
                           min_engines=2, max_loops=2, escalation_batch=2)
    # t0 answered last and is still recorded before k1: the batch is added in
    # pool order, so ranking and the Gate-2 card cannot depend on the network.
    assert [p.model_id for p in r.ran] == ["v0", "k0", "t0", "k1"]


# ── the price, counted ───────────────────────────────────────────────────────

def test_overshoot_is_counted_when_the_first_pick_already_settled_it():
    """One usable reading; the batch's first partner is enough, the second is not needed."""
    fn = _fn({"v0": AGREE, "k0": "", "t0": AGREE})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=2,
                           escalation_batch=2)
    assert fn.calls == ["v0", "k0", "t0", "k1"]     # k1 rode along
    assert r.overshoot == 1
    assert r.usable >= 2


def test_the_overshot_candidate_is_kept_not_discarded():
    """It is already computed; throwing a real reading away to tidy a counter is worse."""
    fn = _fn({"v0": AGREE, "k0": "", "t0": AGREE, "k1": AGREE})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=2,
                           escalation_batch=2)
    assert "k1" in [p.model_id for p in r.ran]
    assert r.usable == 3


def test_batch_of_one_is_the_serial_behaviour_and_never_overshoots():
    fn = _fn({"v0": AGREE, "k0": "", "t0": AGREE})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=3,
                           escalation_batch=1)
    assert fn.calls == ["v0", "k0", "t0"]
    assert r.overshoot == 0 and r.loops == 1


# ── the budget still binds ───────────────────────────────────────────────────

def test_a_batch_never_exceeds_the_loop_budget():
    fn = _disagreeing()
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=3,
                           min_engines=2, max_loops=3, escalation_batch=2)
    assert r.loops == 3                    # not 4, even though the batch is 2
    assert len(fn.calls) == 5              # 2 initial + 3


def test_a_batch_never_runs_past_the_end_of_the_pool():
    fn = _disagreeing()
    r = recognize_ensemble("img", None, fn, picks=PICKS[:3], concurrency=3,
                           min_engines=2, max_loops=5, escalation_batch=2)
    assert len(fn.calls) == 3
    assert r.loops == 1


def test_an_agreeing_pair_never_reaches_the_batch_at_all():
    fn = _fn({p.model_id: AGREE for p in PICKS})
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), concurrency=3,
                           escalation_batch=2)
    assert fn.calls == ["v0", "k0"]
    assert r.fast_path is True and r.overshoot == 0
