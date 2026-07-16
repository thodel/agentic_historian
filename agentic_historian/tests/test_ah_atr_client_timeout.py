"""The ATR client must not give up sooner than the gateway it calls.

`trocr-medieval-escriptmask` was recorded as "timed out" during a live ensemble on
2026-07-16 and written off as a broken engine. It is not broken: it answers a line
in ~7s over HTTP. What happened is that KrakenHTTPClient hardcoded a **120s**
budget while the gateway allows its engines **300s**
(`HTTPEngineClient(timeout=300.0)` in serving-atr-inference). A full page — cold
model load plus ~4s/line across ~20 lines — runs past 120s, so tei hung up on work
the gateway was still doing and blamed the engine.

A client stricter than its server manufactures failures that look like the
server's fault. The budget now comes from config and defaults to the gateway's.

Offline — no HTTP. Run from the repo root:
    pytest agentic_historian/tests/test_ah_atr_client_timeout.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                    # noqa: E402
from agent_a.kraken_client import KrakenHTTPClient  # noqa: E402

# The gateway's own per-engine budget (serving-atr-inference clients.py).
GATEWAY_ENGINE_TIMEOUT_S = 300.0


def test_default_timeout_is_not_tighter_than_the_gateway():
    """The regression, stated directly: 120 < 300 is what caused the phantom."""
    assert KrakenHTTPClient().timeout >= GATEWAY_ENGINE_TIMEOUT_S


def test_default_comes_from_config(monkeypatch):
    monkeypatch.setattr(config, "ATR_HTTP_TIMEOUT", 456.0)
    assert KrakenHTTPClient().timeout == 456.0


def test_an_explicit_timeout_still_wins():
    """Callers that know better (a health probe, a test) keep control."""
    assert KrakenHTTPClient(timeout=5.0).timeout == 5.0


def test_explicit_zero_is_honoured_not_swallowed_by_a_falsy_check():
    """`timeout or default` would turn 0 into the default. Guard against that."""
    assert KrakenHTTPClient(timeout=0.0).timeout == 0.0
