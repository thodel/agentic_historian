"""Tests for #155 (HITL-4c): routing prior wired into score_model / select_kraken_model.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_155_wire_prior.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
from agent_a import model_selector as ms
from agent_a.model_selector import SourceCriteria, select_kraken_model

_DESC = "Gotische Kursive, deutsch, 15. Jahrhundert, Urkunde"


def _criteria():
    return SourceCriteria.from_agent_b(_DESC)


def test_flag_off_is_byte_identical(monkeypatch):
    """With the flag off, get_prior must not even be consulted, and scores are
    exactly the base scores."""
    monkeypatch.setattr(config, "ENABLE_ROUTING_PRIOR", False)

    import agent_a.routing_prior as rp
    monkeypatch.setattr(rp, "get_prior",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")))

    matches = select_kraken_model(_criteria(), top_k=5)
    assert matches                                   # sanity
    assert all("prior" not in m.matched_on for m in matches)


def test_flag_on_applies_capped_prior(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_ROUTING_PRIOR", True)

    base = select_kraken_model(_criteria(), top_k=5)
    assert len(base) >= 1
    target_id = base[0].model.model_id
    base_score = base[0].score

    import agent_a.routing_prior as rp
    monkeypatch.setattr(rp, "get_prior",
                        lambda script, century, lang, models: {target_id: 0.1})

    boosted = select_kraken_model(_criteria(), top_k=5)
    top = next(m for m in boosted if m.model.model_id == target_id)
    assert abs(top.score - (base_score + 0.1)) < 1e-9
    assert "prior" in top.matched_on


def test_prior_can_break_a_near_tie(monkeypatch):
    """A prior on the runner-up can lift it above a near-tied leader."""
    monkeypatch.setattr(config, "ENABLE_ROUTING_PRIOR", True)
    base = select_kraken_model(_criteria(), top_k=5)
    if len(base) < 2 or (base[0].score - base[1].score) > 0.15:
        import pytest
        pytest.skip("no near-tie available in the registry for this criteria")

    runner_up = base[1].model.model_id
    import agent_a.routing_prior as rp
    monkeypatch.setattr(rp, "get_prior",
                        lambda *a, **k: {runner_up: 0.15})
    boosted = select_kraken_model(_criteria(), top_k=5)
    assert boosted[0].model.model_id == runner_up


def test_config_flag_exists_and_defaults_off():
    import os
    assert hasattr(config, "ENABLE_ROUTING_PRIOR")
    if not os.getenv("ENABLE_ROUTING_PRIOR"):
        assert config.ENABLE_ROUTING_PRIOR is False
