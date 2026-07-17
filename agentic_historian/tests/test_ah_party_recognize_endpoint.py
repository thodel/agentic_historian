"""Party must use /recognize, not /ocr (#310 follow-up).

#310 routed party through the gateway but via KrakenHTTPClient.transcribe → /ocr.
The gateway's /ocr is kraken+trocr auto-segment only; party is page-level and gets

    400 {"detail":"/ocr supports kraken + trocr (auto-segment); use /recognize for 'party'"}

Seen live on tei 2026-07-17. So party 400s, falls back to local (unavailable), and
never runs. It must go through /recognize.

Offline — the client is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_party_recognize_endpoint.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import dual_pipeline  # noqa: E402


class _Client:
    def __init__(self):
        self.recognize_calls = []
        self.transcribe_calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def recognize(self, image=None, model=None):
        self.recognize_calls.append(model)
        return SimpleNamespace(text="hant geschriben", confidence=0.8)

    def transcribe(self, image=None, model=None, **kw):
        self.transcribe_calls.append(model)
        raise AssertionError("party must not use /ocr (transcribe) — see #310 follow-up")


def test_party_uses_recognize_not_ocr(monkeypatch, tmp_path):
    client = _Client()
    monkeypatch.setattr(dual_pipeline, "KrakenHTTPClient", lambda *a, **k: client)
    monkeypatch.setattr(dual_pipeline, "_party_available",
                        lambda: (_ for _ in ()).throw(AssertionError("gateway path first")))

    text, model = dual_pipeline._run_party(tmp_path / "p.jpg")

    assert text == "hant geschriben"
    assert client.recognize_calls == ["party"]     # went to /recognize with model=party
    assert client.transcribe_calls == []           # never touched /ocr
