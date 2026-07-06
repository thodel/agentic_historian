"""Regression for #191: when the gateway (live) registry is populated, kraken
model selection must choose ONLY gateway-served models — never a static-only
entry whose model_id the gateway can't run (which made /ocr return 0 chars).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_191_live_registry_authoritative.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import model_selector as ms
from agent_a.models import KRAKEN_MODELS, KrakenModel

_CRIT = ms.SourceCriteria(script="Textura", lang="de", century=15, document_type="charter")


def _live() -> dict[str, KrakenModel]:
    """Two gateway-style live models (slug ids, not Zenodo ids)."""
    return {
        "kraken-late_medieval_german": KrakenModel(
            model_id="kraken-late_medieval_german", name="Late Medieval German",
            lang="de", script="Textura", centuries=[14, 15, 16],
            scripts=["Textura"], languages=["de"]),
        "kraken-medieval_charters": KrakenModel(
            model_id="kraken-medieval_charters", name="Medieval Charters",
            lang="la", script="Textura", centuries=[13, 14, 15],
            scripts=["Textura"], languages=["la"]),
    }


def test_live_populated_selects_only_gateway_models(monkeypatch):
    monkeypatch.setattr(ms, "KRAKEN_MODELS_LIVE", _live())
    matches = ms.select_kraken_model(_CRIT, top_k=10)
    assert matches
    live_ids = set(_live())
    # every chosen model is one the gateway actually serves …
    assert all(m.model.model_id in live_ids for m in matches)
    # … and NONE is a static-only phantom (the #191 bug picked a Zenodo id).
    static_ids = {mo.model_id for mo in KRAKEN_MODELS.values()}
    assert not ({m.model.model_id for m in matches} & static_ids)


def test_live_empty_falls_back_to_static(monkeypatch):
    """Gateway unreachable → the hand-maintained static table is the fallback."""
    monkeypatch.setattr(ms, "KRAKEN_MODELS_LIVE", {})
    matches = ms.select_kraken_model(_CRIT, top_k=5)
    assert matches
    static_ids = {mo.model_id for mo in KRAKEN_MODELS.values()}
    assert all(m.model.model_id in static_ids for m in matches)
