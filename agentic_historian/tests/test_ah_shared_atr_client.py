"""The gateway client is the installed ``atr-client`` package; ``agent_a.kraken_client``
is a shim over it (outremer ADR 0002, #87).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_shared_atr_client.py
"""

import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_client                                   # noqa: E402
import config                                        # noqa: E402
from agent_a import kraken_client as kc              # noqa: E402

REPO = PKG.parent
PIN = re.compile(
    r"^atr-client @ git\+https://github\.com/thodel/serving-atr-inference@[0-9a-f]{40}"
    r"#subdirectory=clients/python$", re.M)


def test_the_client_comes_from_the_installed_package():
    assert "site-packages" in Path(atr_client.__file__).resolve().parts
    assert kc.KrakenClientError is atr_client.AtrClientError
    assert issubclass(kc.KrakenHTTPClient, atr_client.AtrClient)
    assert issubclass(kc.KrakenResult, atr_client.AtrResult)
    assert kc.endpoint_for is atr_client.endpoint_for and kc.OCR_ENGINES is atr_client.OCR_ENGINES


def test_the_shim_keeps_this_repositorys_defaults(monkeypatch):
    monkeypatch.setattr(config, "ATR_GATEWAY_URL", "http://gw:8200/")
    monkeypatch.setattr(config, "ATR_API_KEY", "k")
    monkeypatch.setattr(config, "ATR_HTTP_TIMEOUT", 321.0)
    c = kc.KrakenHTTPClient()
    assert (c.base_url, c.api_key, c.timeout) == ("http://gw:8200", "k", 321.0)
    assert kc.KrakenHTTPClient("http://other", 5.0, "x").api_key == "x", "positional order unchanged"


def test_model_used_is_the_same_value_as_model():
    r = kc.KrakenResult(text="t", confidence=0.5, model_used="m")
    assert r.model == r.model_used == "m"


def test_both_requirement_files_pin_a_commit_sha():
    for name in ("agentic_historian/requirements.txt", "requirements-dev.txt"):
        assert PIN.search((REPO / name).read_text()), f"{name}: no 40-hex SHA pin for atr-client"
