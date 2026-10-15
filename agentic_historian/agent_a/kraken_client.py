"""
Compatibility shim: the gateway client is the ``atr-client`` package.

The client that used to live here now lives in serving-atr-inference under
``clients/python`` — next to the routes it calls and tested there against the
real app — and is installed, pinned to a commit, by this repository and by
outremer (outremer ADR 0002, issue #87). Nothing in this file talks HTTP.

Every name callers and tests import from here still exists and means the
same thing:

* ``KrakenHTTPClient`` — ``atr_client.AtrClient`` with this repository's
  defaults (``config.ATR_GATEWAY_URL``, ``ATR_API_KEY``, ``ATR_HTTP_TIMEOUT``,
  all read at call time) and the historical positional order
  ``(base_url, timeout, api_key)``.
* ``KrakenResult`` — ``atr_client.AtrResult``, accepting the historical
  ``model_used=`` keyword; ``.model_used`` reads the same value as ``.model``.
* ``KrakenClientError`` — ``atr_client.AtrClientError`` itself (``status_code``
  is ``None`` when the gateway never answered).
* ``OCR_ENGINES``, ``endpoint_for``, ``kraken_transcribe``, ``_timing_of``.

What moved with the code, so it is tuned once: the 300 s default budget, the
engine → endpoint routing (#540), the status on the error, ``timing_ms`` as
``None`` rather than ``0`` (#595). What stays here: the
``KRAKEN_SERVICE_URL`` fallback (``config.ATR_GATEWAY_URL`` resolves it).
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import httpx  # noqa: F401  — tests patch ``kraken_client.httpx.Client``; the package reads the same module

# Local imports
import config
from atr_client import OCR_ENGINES, AtrClient, AtrClientError, AtrResult, endpoint_for
from atr_client.client import _timing_of

__all__ = [
    "OCR_ENGINES",
    "KrakenClientError",
    "KrakenHTTPClient",
    "KrakenResult",
    "endpoint_for",
    "kraken_transcribe",
]

KrakenClientError = AtrClientError


class KrakenResult(AtrResult):
    """``AtrResult`` under its historical name, built with ``model_used=``."""

    def __init__(self, text: str, confidence: float, model_used: str = "",
                 service_version: str = "?", *, model: Optional[str] = None, **fields):
        super().__init__(text=text, confidence=confidence,
                         model=model_used if model is None else model,
                         service_version=service_version, **fields)


class KrakenHTTPClient(AtrClient):
    """``AtrClient`` with this repository's configuration as its defaults."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        api_key: Optional[str] = None,
    ) -> None:
        super().__init__(
            base_url or config.ATR_GATEWAY_URL or "",
            api_key=config.ATR_API_KEY if api_key is None else api_key,
            # Default from config (300 s), never a tighter hardcoded budget: a
            # client stricter than its server manufactures phantom timeouts.
            timeout=config.ATR_HTTP_TIMEOUT if timeout is None else timeout,
        )

    # The historical hook name; tests replace it on the instance.
    _prepare_files = staticmethod(AtrClient._image_part)

    def _image_part(self, image: Path | bytes | io.BytesIO):  # type: ignore[override]
        return self._prepare_files(image)

    def transcribe(self, image: Path | bytes | io.BytesIO, model: str,
                   seg_mode: str = "baseline", **_ignored) -> AtrResult:
        """``/ocr`` only, as it always was here; ``read()`` routes by engine."""
        return self.ocr(image, model, seg_mode=seg_mode)


def kraken_transcribe(
    image: Path | bytes | io.BytesIO,
    model: str,
    seg_mode: str = "baseline",
    service_url: Optional[str] = None,
) -> AtrResult:
    """One-shot ``KrakenHTTPClient`` helper (``/ocr``)."""
    with KrakenHTTPClient(base_url=service_url or config.ATR_GATEWAY_URL) as client:
        return client.transcribe(image, model=model, seg_mode=seg_mode)
