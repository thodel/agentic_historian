"""Tests for #153 (HITL-4a): uncertainty gating rules + gate timeouts.

Offline: model selection is real; time is controlled. Run from the repo root:
    pytest agentic_historian/tests/test_ah_153_uncertainty_gating.py
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import uncertainty as u
from runstate import RunState
from utils.entity_resolver import ResolvedEntity  # noqa: F401 (import parity)


def _state(**crit):
    rs = RunState(doc_id="saa-0428")
    rs.criteria.update(crit)
    return rs


class _M:
    def __init__(self, score):
        self.score = score
        self.model = type("m", (), {"model_id": "x", "name": "M"})()
        self.matched_on = []


# ── Gate 1 rules ─────────────────────────────────────────────────────────────

def test_gate1_auto_when_confident(monkeypatch):
    monkeypatch.setattr(u, "select_kraken_model", lambda c, top_k=2: [_M(0.8), _M(0.3)])
    r = u.assess_gate1(_state(script="Kurrent", lang="de", century=16))
    assert r.auto and r.decided_by == "model"


def test_gate1_blocks_on_low_score(monkeypatch):
    monkeypatch.setattr(u, "select_kraken_model", lambda c, top_k=2: [_M(0.35), _M(0.1)])
    r = u.assess_gate1(_state(lang="de"))
    assert r.block and "0.35" in r.reason


def test_gate1_blocks_on_close_top2(monkeypatch):
    monkeypatch.setattr(u, "select_kraken_model", lambda c, top_k=2: [_M(0.7), _M(0.6)])
    r = u.assess_gate1(_state(lang="de"))
    assert r.block and "Top-2" in r.reason


def test_gate1_blocks_on_agent_b_unsicher(monkeypatch):
    monkeypatch.setattr(u, "select_kraken_model", lambda c, top_k=2: [_M(0.9), _M(0.1)])
    sj = {"Datierung": {"wert": "15. Jahrhundert (unsicher)"}}
    r = u.assess_gate1(_state(lang="de"), source_json=sj)
    assert r.block and "unsicher" in r.reason.lower()


def test_gate1_blocks_when_no_model(monkeypatch):
    monkeypatch.setattr(u, "select_kraken_model", lambda c, top_k=2: [])
    assert u.assess_gate1(_state()).block


def test_agent_b_unsicher_detection():
    assert u.agent_b_unsicher({"Sprache": {"wert": "de (unsicher)"}}) is True
    assert u.agent_b_unsicher({"Datierung": {"wert": "15. Jh."}}) is False
    assert u.agent_b_unsicher(None) is False


# ── Gate 2 rules ─────────────────────────────────────────────────────────────

def test_gate2_auto_when_paths_agree():
    same = "Wir Hans von Wiler tuend kund allen den die disen brief"
    r = u.assess_gate2({"vlm": same, "kraken": same, "reconciled": same})
    assert r.auto


def test_gate2_blocks_when_paths_disagree():
    r = u.assess_gate2({
        "vlm": "Wir Hans von Wiler tuend kund allen den die disen brief",
        "kraken": "XYZ voellig andere zeichen 12345 ohne uebereinstimmung",
    })
    assert r.block and "CER" in r.reason


def test_gate2_auto_single_path():
    assert u.assess_gate2({"vlm": "nur ein pfad"}).auto


# ── timeouts ─────────────────────────────────────────────────────────────────

def test_is_expired():
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    fresh = datetime.now(timezone.utc).isoformat()
    assert u.is_expired(old) is True
    assert u.is_expired(fresh) is False
    assert u.is_expired("not-a-date") is False


def test_resolve_on_timeout_records_auto():
    st = _state()
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    assert u.resolve_on_timeout(st, "gate1", old) == "auto"
    assert st.gate_decisions["timeouts"][0]["decided_by"] == "auto"
    # not expired → no-op
    fresh = datetime.now(timezone.utc).isoformat()
    assert u.resolve_on_timeout(_state(), "gate1", fresh) is None
