"""The escalation loop stops on corroboration, not on the absence of an outlier.

`_max_pairwise_cer` is a maximum over a growing set, so it never falls. The old
stopping rule `max_cer > agreement_cer` therefore could not be satisfied BY
ESCALATING: once the initial pair disagreed, every added candidate could only
hold or raise the maximum and the loop ran to `max_loops` by construction. Live,
`loops` was 0 or 5 across 28 recorded pages and never once in between.

The loop now stops when two engines corroborate each other — a condition
escalation can actually reach. `max_pairwise_cer` is unchanged and still
reported: it is the spread, and #300/#313/#332 read it.

Offline — engine execution is a mock recognize_fn, CER and fusion are real.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import (  # noqa: E402
    ModelPick,
    _consensus,
    _max_pairwise_cer,
    recognize_ensemble,
)

A = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"
B = "Wir Hans von Wiler tuend kund allen die disen brief ansehend"   # ~A
X = "1234567890 !!! ??? zzz yyy xxx www vvv uuu ttt sss rrr qqq ppp"
Y = "voellig andere zeichen xyz qrs mno abc def ghi jkl ohne jeden s"
Z = "%%% &&& /// ((( ))) === ??? ;;; ::: ___ ### @@@ +++ *** ~~~ <<<"

PICKS = [ModelPick("vlm", "v0", 1.0), ModelPick("kraken", "k0", 0.9),
         ModelPick("trocr", "t0", 0.8), ModelPick("kraken", "k1", 0.7),
         ModelPick("trocr", "t1", 0.6), ModelPick("kraken", "k2", 0.5)]


def _fn(text_map):
    calls = []

    def fn(pick, image):
        calls.append(pick.model_id)
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": text_map.get(pick.model_id, ""), "error": ""}
    fn.calls = calls
    return fn


def _run(text_map, **kw):
    fn = _fn(text_map)
    kw.setdefault("min_engines", 2)
    kw.setdefault("max_loops", 4)
    kw.setdefault("agreement_cer", 0.30)
    kw.setdefault("concurrency", 1)
    r = recognize_ensemble("img", None, fn, picks=list(PICKS), **kw)
    return r, fn.calls


# ── the defect itself ────────────────────────────────────────────────────────

def test_one_outlier_no_longer_forces_the_full_budget():
    """v0 and k0 disagree; k1 corroborates v0. The loop must stop there.

    Under the old rule the outlier t0 kept the maximum above the threshold for
    the rest of the run, so this page spent every loop it had.
    """
    r, calls = _run({"v0": A, "k0": X, "t0": Y, "k1": B, "t1": Z, "k2": Z})
    assert r.loops == 2, "stopped on corroboration, not on the worst pair"
    assert calls == ["v0", "k0", "t0", "k1"]
    assert r.consensus_cer <= 0.30
    assert r.max_pairwise_cer > 0.30, "the spread is still wide, and still reported"


def test_the_measure_that_could_not_fall_still_reports_the_spread():
    """#300/#313/#332 read `max_pairwise_cer`; it must not have changed meaning."""
    recs = [{"engine": "vlm", "model_id": "v0", "text": A, "error": ""},
            {"engine": "kraken", "model_id": "k0", "text": B, "error": ""},
            {"engine": "trocr", "model_id": "t0", "text": X, "error": ""}]
    assert _max_pairwise_cer(recs) > 0.30            # the outlier still dominates
    agreed, best = _consensus(recs, 0.30)
    assert agreed and best <= 0.30                   # and a corroborating pair exists


def test_a_page_without_corroboration_still_spends_the_budget():
    """The loop must not stop early just because it now can. No pair agrees here."""
    r, calls = _run({"v0": A, "k0": X, "t0": Y, "k1": Z,
                     "t1": "sechste voellig andere lesart ohne jede aehnlichkeit",
                     "k2": "siebte lesart die mit keiner der uebrigen etwas teilt"})
    assert r.loops == 4 and len(calls) == 6
    assert r.fast_path is False
    assert "no consensus, budget spent" in r.path


# ── independence: an agreeing pair must span two engines ─────────────────────

def test_two_models_of_one_engine_agreeing_is_not_corroboration():
    """k0 and k1 are both kraken. Family resemblance is not a second opinion."""
    r, calls = _run({"v0": A, "k0": X, "t0": Y, "k1": X, "t1": Z, "k2": X})
    assert r.loops > 1, "two kraken models agreeing must not end the escalation"
    assert "t1" in calls, "the loop kept looking for a second ENGINE"


def test_a_single_engine_pool_falls_back_to_any_agreeing_pair():
    """With only one engine present there is no independence to be had.

    Demanding it would replace one unreachable condition with another — exactly
    the defect this change exists to remove.
    """
    picks = [ModelPick("kraken", "k0", 0.9), ModelPick("kraken", "k1", 0.8),
             ModelPick("kraken", "k2", 0.7)]
    fn = _fn({"k0": A, "k1": B, "k2": X})
    r = recognize_ensemble("img", None, fn, picks=picks, min_engines=2,
                           max_loops=4, agreement_cer=0.30, concurrency=1)
    assert r.loops == 0 and r.fast_path is True
    assert fn.calls == ["k0", "k1"]


# ── the neighbouring contracts must not move ─────────────────────────────────

def test_the_two_engine_fast_path_is_unchanged():
    """#390: an agreeing initial pair runs two engines and stops.

    With exactly two candidates from two engines the closest pair IS the widest
    pair, so the new rule and the old one are the same test — by construction,
    not by luck.
    """
    r, calls = _run({p.model_id: A for p in PICKS})
    assert r.loops == 0 and r.fast_path is True and calls == ["v0", "k0"]


def test_a_single_usable_candidate_still_escalates():
    """#367: below two usable candidates there is nothing to corroborate."""
    r, _ = _run({"v0": A, "k0": "", "t0": "", "k1": "", "t1": "", "k2": ""})
    assert r.usable == 1 and r.loops > 0
    assert r.fast_path is False
    assert "unchecked" in r.path


def test_consensus_is_zero_below_two_candidates():
    """The same trap as #367: no pair means unmeasured, never agreement."""
    agreed, best = _consensus(
        [{"engine": "vlm", "model_id": "v0", "text": A, "error": ""}], 0.30)
    assert agreed is False and best == 0.0
