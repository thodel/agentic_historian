"""
ATR gateway HTTP client — calls the serving-atr-inference gateway (kraken +
TrOCR + party + vllm) instead of running kraken CLI locally.

Endpoint + auth are configured in config.py:
    ATR_GATEWAY_URL   base URL of the gateway (falls back to the legacy
                      KRAKEN_SERVICE_URL for backward compatibility)
    ATR_API_KEY       static secret sent as the `X-API-Key` header on every
                      request (empty = no auth header, dev/local only)

API design
──────────
POST /ocr
    Body (multipart/form-data):
        image   – image file (JPEG, PNG, TIFF, …)
        model   – kraken model identifier, e.g. "10.5281/zenodo.7516057"
        seg_mode – "baseline" (default) or "lines"
    Returns JSON:
        {"text": "<OCR result string>", "confidence": 0.93, "model": "...", ...}

POST /segment
    Body (multipart/form-data):
        image   – image file
        seg_mode – "baseline" (default) or "lines"
    Returns JSON:
        {"lines": [{"baseline": [[x0,y0],…], "mask": …}, …]}

GET /models
    Returns JSON: {"models": ["10.5281/zenodo.7516057", …]}

GET /health
    Returns JSON: {"status": "ok", "models_loaded": ["10.5281/zenodo.xxx", …]}
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Optional

import httpx

# Local imports
import config


class KrakenHTTPClient:
    """Thin HTTP wrapper around the remote kraken OCR service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout: float = 120.0,
        api_key: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or config.ATR_GATEWAY_URL or "").rstrip("/")
        self.timeout = timeout
        # X-API-Key for the gateway. Default from config; "" = no auth header.
        self.api_key = config.ATR_API_KEY if api_key is None else api_key
        self._client: Optional[httpx.Client] = None

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "KrakenHTTPClient":
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        )
        return self

    def __exit__(self, *args) -> None:
        assert self._client is not None
        self._client.close()
        self._client = None

    # ── public API ───────────────────────────────────────────────────────────

    def segment(
        self,
        image: Path | bytes | io.BytesIO,
        seg_mode: str = "baseline",
    ) -> dict:
        """
        Send an image to the kraken service for segmentation only.

        Returns a dict with a ``lines`` key listing polygon baselines
        and text masks.  Raises ``KrakenClientError`` on HTTP errors.
        """
        files = self._prepare_files(image)
        files["seg_mode"] = seg_mode
        resp = self._post("/segment", files=files)
        return resp.json()

    def transcribe(
        self,
        image: Path | bytes | io.BytesIO,
        model: str,
        seg_mode: str = "baseline",
    ) -> KrakenResult:
        """
        Send an image to the kraken service for HTR/OCR.

        Parameters
        ----------
        image : file path, raw bytes, or BytesIO
            The document image to process.
        model : str
            Full kraken model identifier, e.g. "10.5281/zenodo.7516057".
        seg_mode : "baseline" | "lines"

        Returns a ``KrakenResult`` dataclass (text, confidence, model_used).
        Raises ``KrakenClientError`` on HTTP errors.
        """
        files: dict = self._prepare_files(image)
        files["model"] = model
        files["seg_mode"] = seg_mode
        resp = self._post("/ocr", files=files)
        data = resp.json()
        return KrakenResult(
            text=data.get("text", ""),
            confidence=data.get("confidence", 0.0),
            model_used=data.get("model", model),
            service_version=data.get("version", "?"),
        )

    def list_models(self) -> list[dict]:
        """Return the gateway model registry.

        The gateway responds with ModelsResponse ``{"models": [ModelInfo, ...]}``
        where each ModelInfo carries id/engine/scripts/centuries/languages/level
        — the selection metadata #110 consumes. Returns the list of ModelInfo
        dicts (empty list if the payload is malformed).
        """
        resp = self._get("/models")
        models = resp.json().get("models", [])
        return models if isinstance(models, list) else []

    def list_model_ids(self) -> list[str]:
        """Convenience: just the model ids from the gateway registry."""
        return [m["id"] for m in self.list_models() if isinstance(m, dict) and "id" in m]

    def health_check(self) -> dict:
        """Return gateway health (HealthResponse: status, version, model_count,
        resident_models, engines)."""
        resp = self._get("/health")
        return resp.json()

    # ── internals ────────────────────────────────────────────────────────────

    def _prepare_files(
        self, image: Path | bytes | io.BytesIO
    ) -> dict[str | tuple[str, bytes, str]]:
        if isinstance(image, Path):
            with open(image, "rb") as fh:
                data = fh.read()
            return {"image": (image.name, data, "application/octet-stream")}

        if isinstance(image, bytes):
            return {"image": ("image", image, "application/octet-stream")}

        # Already a BytesIO — grab raw bytes
        image.seek(0)
        return {"image": ("image", image.read(), "application/octet-stream")}

    def _get(self, path: str) -> httpx.Response:
        assert self._client is not None
        try:
            return self._client.get(path)
        except httpx.RequestError as exc:
            raise KrakenClientError(
                f"Kraken service unreachable at {self.base_url}{path}: {exc}"
            ) from exc

    def _post(
        self,
        path: str,
        files: dict,
    ) -> httpx.Response:
        assert self._client is not None
        try:
            return self._client.post(path, files=files)
        except httpx.RequestError as exc:
            raise KrakenClientError(
                f"Kraken service error at {self.base_url}{path}: {exc}"
            ) from exc


# ── result dataclass ──────────────────────────────────────────────────────────

from dataclasses import dataclass


@dataclass
class KrakenResult:
    """Result of a remote kraken HTR/OCR call."""

    text: str
    confidence: float
    model_used: str
    service_version: str = "?"


# ── exceptions ───────────────────────────────────────────────────────────────

class KrakenClientError(Exception):
    """Raised when the kraken HTTP service returns an error or is unreachable."""
    pass


# ── convenience helper ───────────────────────────────────────────────────────

def kraken_transcribe(
    image: Path | bytes | io.BytesIO,
    model: str,
    seg_mode: str = "baseline",
    service_url: Optional[str] = None,
) -> KrakenResult:
    """
    One‑shot ``KrakenHTTPClient`` context manager helper.

    Usage::

        result = kraken_transcribe(Path("page.tif"), "10.5281/zenodo.7516057")
        print(result.text, result.confidence)
    """
    url = service_url or config.ATR_GATEWAY_URL
    with KrakenHTTPClient(base_url=url) as client:
        return client.transcribe(image, model=model, seg_mode=seg_mode)