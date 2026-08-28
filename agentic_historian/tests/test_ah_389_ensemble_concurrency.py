"""#389: the ensemble's initial batch runs its engine calls concurrently.

Offline — engine execution is a mock recognize_fn with per-pick sleeps; no
network, no config env. Run from the repo root:
    pytest agentic_historian/tests/test_ah_389_ensemble_concurrency.py

What must hold (from the issue):
- wall time of the initial batch ≈ the slowest pick, not the sum
- ``recognitions``/``ran`` stay in POOL order, not completion order
- a raising/None pick is backfilled from the pool so min_engines is still reached
- ``concurrency=1`` restores the sequential behaviour (same picks, same order)
- no pick runs speculatively beyond the min_engines budget
"""

import sys
import threading
import time
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.ensemble import ModelPick, recognize_ensemble  # noqa: E402

AGREE = "Wir Hans von Wiler tuend kund allen die disen brief ansehent"


def _picks(*ids):
    def eng(i):
        return "kraken" if i.startswith("k") else "trocr" if i.startswith("t") else "vlm"
    return [ModelPick(eng(i), i, 1.0) for i in ids]


def _fn(sleep_map=None, fail=(), raise_on=(), text_map=None, calls=None):
    """Mock recognize_fn: sleeps per pick, returns agreeing text by default.
    ``calls`` (a list) records model_ids in START order, thread-safely."""
    lock = threading.Lock()

    def fn(pick, image):
        if calls is not None:
            with lock:
                calls.append(pick.model_id)
        time.sleep((sleep_map or {}).get(pick.model_id, 0.0))
        if pick.model_id in raise_on:
            raise RuntimeError("engine down")
        if pick.model_id in fail:
            return None
        return {"engine": pick.engine, "model_id": pick.model_id,
                "text": (text_map or {}).get(pick.model_id, AGREE), "error": ""}
    return fn


# ── wall time ≈ max, not sum ─────────────────────────────────────────────────

def test_initial_batch_overlaps():
    picks = _picks("v0", "k0", "t0")
    fn = _fn(sleep_map={"v0": 0.3, "k0": 0.3, "t0": 0.3})
    t0 = time.monotonic()
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=3)
    elapsed = time.monotonic() - t0
    assert len(res.recognitions) == 3
    # sequential would be ≥ 0.9 s; concurrent must be well under the sum
    assert elapsed < 0.7, f"initial batch did not overlap: {elapsed:.2f}s"


# ── pool order survives out-of-order completion ──────────────────────────────

def test_results_stay_in_pool_order():
    picks = _picks("v0", "k0", "t0")
    # v0 is the SLOWEST — it completes last but must still come first
    fn = _fn(sleep_map={"v0": 0.25, "k0": 0.05, "t0": 0.01})
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=3)
    assert [p.model_id for p in res.ran] == ["v0", "k0", "t0"]
    assert [r["model_id"] for r in res.recognitions] == ["v0", "k0", "t0"]


# ── failures backfill from the pool ──────────────────────────────────────────

def test_raising_pick_is_backfilled():
    picks = _picks("v0", "k0", "t0", "k1")
    fn = _fn(raise_on=("k0",))
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=3)
    assert [p.model_id for p in res.ran] == ["v0", "t0", "k1"]
    assert res.usable == 3


def test_none_pick_is_backfilled():
    picks = _picks("v0", "k0", "t0", "k1")
    fn = _fn(fail=("t0",))
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=3)
    assert [p.model_id for p in res.ran] == ["v0", "k0", "k1"]


def test_exhausted_pool_ends_short():
    picks = _picks("v0", "k0", "t0")
    fn = _fn(raise_on=("k0",))
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=3)
    assert [p.model_id for p in res.ran] == ["v0", "t0"]


# ── no speculative picks beyond the min_engines budget ───────────────────────

def test_no_speculative_runs():
    picks = _picks("v0", "k0", "t0", "k1", "t1")
    calls: list = []
    fn = _fn(calls=calls)
    recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                       picks=picks, concurrency=5)
    assert sorted(calls) == ["k0", "t0", "v0"], f"ran extra picks: {calls}"


# ── concurrency=1 restores the sequential behaviour ──────────────────────────

def test_concurrency_one_is_sequential():
    picks = _picks("v0", "k0", "t0", "k1")
    calls: list = []
    fn = _fn(raise_on=("k0",), calls=calls)
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=0,
                             picks=picks, concurrency=1)
    # start order IS pool order — nothing overlaps
    assert calls == ["v0", "k0", "t0", "k1"]
    assert [p.model_id for p in res.ran] == ["v0", "t0", "k1"]


# ── the feedback loop still works after a concurrent initial batch ───────────

def test_loop_still_expands_on_disagreement():
    d1 = AGREE
    d2 = "voellig andere zeichen xyz qrs mno abc def ghi jkl ohne jeden sinn"
    d3 = "1234567890 !!! ??? zzz yyy xxx www vvv uuu ttt sss rrr qqq ppp"
    picks = _picks("v0", "k0", "t0", "k1")
    fn = _fn(text_map={"v0": d1, "k0": d2, "t0": d3, "k1": d1})
    res = recognize_ensemble("img", None, fn, min_engines=3, max_loops=1,
                             agreement_cer=0.30, picks=picks, concurrency=3)
    assert [p.model_id for p in res.added] == ["k1"]
    assert len(res.recognitions) == 4


# ── default comes from config ────────────────────────────────────────────────

def test_default_concurrency_reads_config(monkeypatch):
    import config
    monkeypatch.setattr(config, "ENSEMBLE_CONCURRENCY", 1, raising=False)
    picks = _picks("v0", "k0", "t0")
    calls: list = []
    fn = _fn(sleep_map={"v0": 0.05}, calls=calls)
    recognize_ensemble("img", None, fn, min_engines=3, max_loops=0, picks=picks)
    assert calls == ["v0", "k0", "t0"]
