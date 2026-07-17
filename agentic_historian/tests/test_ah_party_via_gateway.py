"""Party must run on the ATR gateway, not on this host.

`_run_party` went straight to a LOCAL path: `_party_available()` shells out to
`kraken list` on THIS machine and checks whether the party model is downloaded
here. But recognition does not happen here — that is what the gateway is for. tei
has no local kraken models, so every run reported

    party model not available (run: kraken get 10.5281/zenodo.20642057)

while asterAIx sat there with a healthy atr-party service on :8203 that nobody
called. The error reads like a host instruction, which is how it got mis-filed
against the gateway host (serving-atr-inference#30).

Party is also not kraken-loadable (party/safetensors, needs the standalone `party`
package), so `kraken list` could never have listed it — the check was doomed twice.

Offline — the gateway client is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_party_via_gateway.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import dual_pipeline  # noqa: E402
from agent_a.kraken_client import KrakenClientError  # noqa: E402


class _Client:
    def __init__(self, result=None, raises=None):
        self._result, self._raises = result, raises
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def recognize(self, image=None, model=None, **kw):
        self.calls.append(model)
        if self._raises:
            raise self._raises
        return self._result


def test_party_goes_through_the_gateway(monkeypatch, tmp_path):
    client = _Client(result=SimpleNamespace(text="hant geschriben", confidence=0.8))
    monkeypatch.setattr(dual_pipeline, "KrakenHTTPClient", lambda *a, **k: client)
    monkeypatch.setattr(dual_pipeline, "_party_available",
                        lambda: pytest.fail("must not probe the local host first"))

    text, model = dual_pipeline._run_party(tmp_path / "p.jpg")

    assert text == "hant geschriben"
    assert client.calls == ["party"]          # asked the gateway for party


def test_gateway_down_falls_back_to_local_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(dual_pipeline, "KrakenHTTPClient",
                        lambda *a, **k: _Client(raises=KrakenClientError("503")))
    monkeypatch.setattr(dual_pipeline, "_party_available", lambda: True)
    monkeypatch.setattr(dual_pipeline, "party_transcribe",
                        lambda p: ("lokal gelesen", None))

    text, _ = dual_pipeline._run_party(tmp_path / "p.jpg")
    assert text == "lokal gelesen"


def test_gateway_down_and_no_local_reports_honestly(monkeypatch, tmp_path):
    """The old message told the operator to run `kraken get` on the wrong host."""
    monkeypatch.setattr(dual_pipeline, "KrakenHTTPClient",
                        lambda *a, **k: _Client(raises=KrakenClientError("503")))
    monkeypatch.setattr(dual_pipeline, "_party_available", lambda: False)

    text, err = dual_pipeline._run_party(tmp_path / "p.jpg")

    assert text == ""
    assert "gateway unreachable" in err
    assert "kraken get" not in err            # no misleading host instruction
