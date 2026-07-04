"""Tests for #144 (ATR-1): gateway client + X-API-Key auth + endpoint config.

The client must authenticate to the serving-atr-inference gateway with
X-API-Key, take its base URL from ATR_GATEWAY_URL, and parse the gateway's
HealthResponse / ModelsResponse shapes.

Offline: httpx is mocked via a transport. Run from the repo root:
    pytest agentic_historian/tests/test_ah_144_gateway_auth.py
"""

import sys
from pathlib import Path

import httpx

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


def _mock_client(monkeypatch, handler):
    """Patch httpx.Client so KrakenHTTPClient uses a MockTransport handler."""
    import agent_a.kraken_client as kc

    real_client = httpx.Client

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(kc.httpx, "Client", _factory)
    return kc


# ── Config wiring ────────────────────────────────────────────────────────────

def test_config_exposes_gateway_url_and_key():
    import config
    assert hasattr(config, "ATR_GATEWAY_URL")
    assert hasattr(config, "ATR_API_KEY")


def test_gateway_url_falls_back_to_legacy():
    """When ATR_GATEWAY_URL is unset, it falls back to the legacy
    KRAKEN_SERVICE_URL alias (side-effect-free: no module reload)."""
    import os
    import config
    if not os.getenv("ATR_GATEWAY_URL"):
        assert config.ATR_GATEWAY_URL == config.KRAKEN_SERVICE_URL


# ── Auth header on every request ─────────────────────────────────────────────

def test_x_api_key_sent_on_all_requests(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("x-api-key")))
        if request.url.path == "/models":
            return httpx.Response(200, json={"models": [{"id": "m1", "engine": "kraken"}]})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "1", "model_count": 1})
        return httpx.Response(200, json={"text": "hi", "confidence": 0.9, "model": "m1"})

    kc = _mock_client(monkeypatch, handler)
    with kc.KrakenHTTPClient(base_url="http://gw:8200", api_key="secret-key") as client:
        client.health_check()
        client.list_models()
        client.transcribe(b"\xff\xd8\xff", model="m1")

    assert {p for p, _ in seen} == {"/health", "/models", "/ocr"}
    assert all(key == "secret-key" for _, key in seen), f"missing X-API-Key: {seen}"


def test_no_header_when_key_unset(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.headers.get("x-api-key"))
        return httpx.Response(200, json={"status": "ok", "version": "1", "model_count": 0})

    kc = _mock_client(monkeypatch, handler)
    with kc.KrakenHTTPClient(base_url="http://gw:8200", api_key="") as client:
        client.health_check()
    assert seen == [None], "no X-API-Key header should be sent when the key is empty"


# ── Gateway schema parsing ───────────────────────────────────────────────────

def test_list_models_parses_modelsresponse(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"models": [
            {"id": "gothic-15c", "engine": "kraken", "scripts": ["kursive"], "centuries": [15]},
            {"id": "trocr-kurrent", "engine": "trocr", "level": "line"},
        ]})

    kc = _mock_client(monkeypatch, handler)
    with kc.KrakenHTTPClient(base_url="http://gw:8200", api_key="k") as client:
        models = client.list_models()
        ids = client.list_model_ids()

    assert isinstance(models, list) and models[0]["id"] == "gothic-15c"
    assert ids == ["gothic-15c", "trocr-kurrent"]


def test_health_check_returns_healthresponse(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={
            "status": "ok", "version": "0.1.0", "model_count": 3,
            "resident_models": ["gothic-15c"], "engines": [],
        })

    kc = _mock_client(monkeypatch, handler)
    with kc.KrakenHTTPClient(base_url="http://gw:8200", api_key="k") as client:
        h = client.health_check()
    assert h["status"] == "ok" and h["model_count"] == 3
