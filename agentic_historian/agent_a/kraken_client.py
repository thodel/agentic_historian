"""
kraken HTTP client — calls the remote kraken OCR service instead of
running kraken CLI locally.

The service endpoint is configured via KRAKEN_SERVICE_URL in the
application config (see config.py / .env.example).

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
    ) -> None:
        self.base_url = (base_url or config.KRAKEN_SERVICE_URL or "").rstrip("/")
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None

    # ── context manager ──────────────────────────────────────────────────────

    def __enter__(self) -> "KrakenHTTPClient":
        # The ATR gateway authenticates via a static shared X-API-Key header
        # (config.ATR_API_KEY). /health is public; /models, /ocr, /segment are not.
        headers = {}
        if getattr(config, "ATR_API_KEY", ""):
            headers["X-API-Key"] = config.ATR_API_KEY
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

    def list_models(self) -> list[str]:
        """Return the list of model identifiers loaded in the service."""
        resp = self._get("/models")
        return resp.json().get("models", [])

    def health_check(self) -> dict:
        """Return service health + loaded model list."""
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
    url = service_url or config.KRAKEN_SERVICE_URL
    with KrakenHTTPClient(base_url=url) as client:
        return client.transcribe(image, model=model, seg_mode=seg_mode)