"""Tests for the ATR gateway client: X-API-Key auth + gateway config.

The recognition stack (kraken/TrOCR/vLLM) moved to the asterAIx gateway
(serving-atr-inference), which requires a static ``X-API-Key`` header on
/models, /ocr and /segment. KrakenHTTPClient must send it.

Run offline (file-level checks, no imports / no VPN).
Run:  python tests/test_ah_atr_gateway_auth.py   (or: pytest)
"""

PKG = "agentic_historian"
CLIENT = f"{PKG}/agent_a/kraken_client.py"
CONFIG = f"{PKG}/config.py"


def read(path):
    with open(path) as f:
        return f.read()


# ── config exposes the shared secret ─────────────────────────────────────────

def test_config_defines_atr_api_key():
    assert "ATR_API_KEY" in read(CONFIG), "config.py must expose ATR_API_KEY"


def test_config_service_url_present():
    assert "KRAKEN_SERVICE_URL" in read(CONFIG)


# ── client attaches the X-API-Key header to every request ────────────────────

def test_client_sends_x_api_key_header():
    src = read(CLIENT)
    assert "X-API-Key" in src, "kraken_client must send the X-API-Key header"
    assert "ATR_API_KEY" in src, "the header value must come from config.ATR_API_KEY"
    # header dict must be passed into the httpx.Client so ALL requests carry it
    assert "headers=headers" in src


if __name__ == "__main__":
    test_config_defines_atr_api_key()
    test_config_service_url_present()
    test_client_sends_x_api_key_header()
    print("ok")
