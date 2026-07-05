"""
Tests for #155 (HITL-4c): routing prior from human feedback.

Offline. Run from the repo root:
    .venv/bin/python -m pytest agentic_historian/tests/test_ah_155_routing_prior.py -v
"""

import json
import pathlib
import sys
import tempfile
from unittest.mock import patch

PKG = pathlib.Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.routing_prior import (
    PriorEntry,
    clear_cache,
    get_prior,
    get_routing_prior,
    load_routing_prior,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_jsonl(tmp_path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    """Write entries as JSONL, return the path."""
    path = tmp_path / "routing.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return path


def _entry(doc_id="d1", script="Caroline", century=14, lang="de", model_id="m1", chosen="m1"):
    return dict(doc_id=doc_id, script=script, century=century, lang=lang,
                model_id=model_id, chosen_value=chosen)


# ── prior_score calculation ───────────────────────────────────────────────────

def test_prior_score_calculation_60pct_winrate():
    """win_rate=0.6 → prior_score = min(0.6-0.5, 0.15) = 0.10."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1", chosen="m1") for _ in range(6)]
                           + [_entry(model_id="m2", chosen="m2") for _ in range(4)])
        # 6 wins m1 / 10 total = 0.6 → prior = 0.10
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store = load_routing_prior()
        key = ("caroline", 14, "de")
        assert key in store
        m1_entry = next(e for e in store[key] if e.model_id == "m1")
        assert abs(m1_entry.prior_score - 0.10) < 0.001


def test_prior_score_50pct_winrate():
    """win_rate=0.5 → prior_score = 0.0 (floored, not stored)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1") for _ in range(5)]
                           + [_entry(model_id="m2") for _ in range(5)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store = load_routing_prior()
        # At 50/50 split, prior_score = 0.0 for both models → not stored → bucket not created
        assert store == {}


def test_prior_score_70pct_winrate_capped_at_0_15():
    """win_rate=0.7 → prior_score = min(0.7-0.5, 0.15) = 0.15 (capped)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1") for _ in range(7)]
                           + [_entry(model_id="m2") for _ in range(3)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store = load_routing_prior()
        key = ("caroline", 14, "de")
        m1_entry = next(e for e in store[key] if e.model_id == "m1")
        assert abs(m1_entry.prior_score - 0.15) < 0.001


def test_prior_score_90pct_winrate_still_capped():
    """win_rate=0.9 → prior_score = min(0.9-0.5, 0.15) = 0.15 (capped, not 0.40)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1") for _ in range(9)]
                           + [_entry(model_id="m2") for _ in range(1)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store = load_routing_prior()
        key = ("caroline", 14, "de")
        m1_entry = next(e for e in store[key] if e.model_id == "m1")
        assert m1_entry.prior_score == 0.15


# ── minimum 10 entries ────────────────────────────────────────────────────────

def test_prior_activates_only_at_10_entries():
    """Exactly 10 entries → prior activates. 9 → no prior."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)

        # 9 entries → below threshold
        path9 = _make_jsonl(tmp, [_entry(doc_id=f"d{i}", model_id="m1") for i in range(9)])
        with patch("agent_a.routing_prior._routing_path", return_value=path9):
            clear_cache()
            store9 = load_routing_prior()
        assert ("caroline", 14, "de") not in store9

        # 10 entries → activates
        path10 = _make_jsonl(tmp, [_entry(doc_id=f"d{i}", model_id="m1") for i in range(10)])
        with patch("agent_a.routing_prior._routing_path", return_value=path10):
            clear_cache()
            store10 = load_routing_prior()
        assert ("caroline", 14, "de") in store10


def test_prior_empty_when_file_missing():
    """routing.jsonl doesn't exist → empty store."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        missing = tmp / "routing.jsonl"
        assert not missing.exists()
        with patch("agent_a.routing_prior._routing_path", return_value=missing):
            clear_cache()
            store = load_routing_prior()
        assert store == {}


def test_prior_empty_when_no_positive_priors():
    """All models at 50% win rate → no positive prior stored (floored to 0.0)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1") for _ in range(5)]
                           + [_entry(model_id="m2") for _ in range(5)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store = load_routing_prior()
        # No model has a positive prior → bucket never created
        assert store == {}


# ── get_prior() API ───────────────────────────────────────────────────────────

def test_get_prior_returns_empty_dict_when_no_prior():
    """No prior for this bucket → all models get 0.0."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        missing = tmp / "routing.jsonl"
        with patch("agent_a.routing_prior._routing_path", return_value=missing):
            clear_cache()
            result = get_prior("Caroline", 14, "de", ["m1", "m2"])
        # No positive prior 2192 returns {} (empty, no nudge for any model)
        assert result == {}


def test_get_prior_returns_positive_prior():
    """Model with 70% win rate gets 0.15 prior, others get 0.0."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry(model_id="m1") for _ in range(7)]
                           + [_entry(model_id="m2") for _ in range(3)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            result = get_prior("Caroline", 14, "de", ["m1", "m2", "m3"])
        assert result["m1"] == 0.15
        assert result["m2"] == 0.0
        assert result["m3"] == 0.0


def test_get_prior_none_for_missing_fields():
    """script=None or lang=None → empty dict (can't look up bucket)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry() for _ in range(12)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            assert get_prior(None, 14, "de", ["m1"]) == {}
            assert get_prior("Caroline", None, "de", ["m1"]) == {}
            assert get_prior("Caroline", 14, None, ["m1"]) == {}


def test_get_prior_normalises_script_and_lang():
    """'CAROLINE' normalises to 'caroline'; 'DE' normalises to 'de'."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        # Entry has lowercase normalised values in the JSONL
        path = _make_jsonl(tmp, [_entry(doc_id=f"d{i}", script="caroline", lang="de",
                                         model_id="m1") for i in range(12)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            # Pass uppercase — normalisation happens inside get_prior
            result = get_prior("CAROLINE", 14, "DE", ["m1"])
        # If normalisation worked, m1 gets a positive prior
        assert result.get("m1", 0.0) > 0


# ── clear_cache ───────────────────────────────────────────────────────────────

def test_clear_cache_resets_state():
    """clear_cache() resets _ROUTING_PRIOR and _ROUTING_PRIOR_LOADED."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        path = _make_jsonl(tmp, [_entry() for _ in range(12)])
        with patch("agent_a.routing_prior._routing_path", return_value=path):
            clear_cache()
            store1 = get_routing_prior()
            assert ("caroline", 14, "de") in store1

            clear_cache()
            # After clear, re-load returns the same data (cache is per-load)
            store2 = get_routing_prior()
            assert store2 == store1