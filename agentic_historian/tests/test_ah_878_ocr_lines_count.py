"""#878: ``/ocr`` reports ``lines`` as a count; the client must not die on it.

The gateway's ``/ocr`` answers ``lines=len(result.lines)`` and ``/recognize``
answers the lines themselves. The pinned ``atr-client`` builds every result
with ``list(data["lines"])``, so a kraken or TrOCR page with one line or more
failed with ``'int' object is not iterable`` — every one of them, since the shim
over the shared client (#664). Observed on tei in the ``lassberg-proof`` batch
of 2026-11-16.

#879 worked around it in the shim; the client itself accepts the count since
serving-atr-inference#272, and the shim no longer overrides ``_result``. These
tests now check the pinned client through the shim.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_878_ocr_lines_count.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.kraken_client import KrakenHTTPClient


class _Response:
    """An ``httpx.Response`` as far as ``_result`` reads it."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


OCR_ANSWER = {"text": "Euer fründlich willig dienst", "confidence": 0.83,
              "model": "kraken-inzigkofen", "version": "1.4", "engine": "kraken",
              "lines": 12, "segmented_by": "blla", "timing_ms": 2140}


def test_a_line_count_from_ocr_is_a_result_not_a_crash():
    got = KrakenHTTPClient._result(_Response(dict(OCR_ANSWER)),
                                   model="kraken-inzigkofen", engine="kraken")

    assert got.text == "Euer fründlich willig dienst"
    assert got.confidence == 0.83
    assert got.model == "kraken-inzigkofen" and got.engine == "kraken"
    assert got.lines == [], "a count is not lines; the field is the lines"
    assert got.segmented_by == "blla" and got.timing_ms == 2140


def test_the_lines_from_recognize_are_kept():
    lines = [{"order": 0, "text": "Euer", "confidence": 0.9},
             {"order": 1, "text": "fründlich", "confidence": 0.8}]
    got = KrakenHTTPClient._result(
        _Response({**OCR_ANSWER, "lines": lines}), model="m", engine="kraken")

    assert got.lines == lines


def test_zero_lines_and_no_lines_stay_empty():
    for payload in ({**OCR_ANSWER, "lines": 0}, {k: v for k, v in OCR_ANSWER.items()
                                                   if k != "lines"}):
        got = KrakenHTTPClient._result(_Response(payload), model="m", engine="trocr")
        assert got.lines == [] and got.text == OCR_ANSWER["text"]


def test_read_reaches_the_tolerant_result(monkeypatch):
    """`read()` → `ocr()` → `self._result`: the override must be on the path the
    pipeline takes, not only callable by name."""
    client = KrakenHTTPClient(base_url="http://gateway.test", api_key="k")
    monkeypatch.setattr(client, "_post",
                        lambda path, files, data=None: _Response(dict(OCR_ANSWER)))

    got = client.read(b"\xff\xd8\xff", model="kraken-inzigkofen", engine="kraken")

    assert got.text == OCR_ANSWER["text"] and got.lines == []


def test_the_shim_no_longer_overrides_the_result():
    """The workaround from #879 is gone; the pinned client does the work."""
    from atr_client import AtrClient

    assert "_result" not in KrakenHTTPClient.__dict__
    assert KrakenHTTPClient._result is AtrClient._result
