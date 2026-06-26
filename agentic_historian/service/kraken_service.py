"""
kraken OCR HTTP service
───────────────────────
A lightweight FastAPI wrapper around the kraken library that runs on a
dedicated server (or your GPU workstation) and serves HTR/OCR requests
over HTTP.

Run with::

    pip install kraken[torch] fastapi uvicorn
    uvicorn service.kraken_service:app --host 0.0.0.0 --port 8765

Environment variables
─────────────────────
KRKEN_DEFAULT_MODEL   – model loaded at startup if none specified (optional)
KRAKEN_MODEL_DIR      – local kraken model cache directory (default: ~/.kraken)
"""

from __future__ import annotations

import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import kraken.lib.hocr
import kraken.lib.log
import ujson  # type: ignore[import]

# ujson is much faster than stdlib json — used throughout for perf
import fastapi
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

# ── logging ──────────────────────────────────────────────────────────────────

logger = logging.getLogger("kraken_service")
kraken.lib.log.KRAKEN_LOGGER.setLevel("WARNING")


# ── lifespan / startup ───────────────────────────────────────────────────────

# Global model cache: model_id → loaded kraken model instance
_model_cache: dict[str, object] = {}
_cache_dir = Path(os.environ.get("KRAKEN_MODEL_DIR", Path.home() / ".kraken"))
_cache_dir.mkdir(parents=True, exist_ok=True)


def _load_kraken_model(model_id: str):
    """
    Load (or return cached) kraken model by Zenodo model ID.

    Downloads from Zenodo on first call using ``kraken.get``.
    """
    if model_id in _model_cache:
        return _model_cache[model_id]

    logger.info("Loading kraken model: %s", model_id)
    from kraken.lib import models

    try:
        model_path = _cache_dir / f"{model_id.replace('/', '_')}.mlmodel"
        if not model_path.exists():
            # Let kraken download it to its default location, then move it
            models.download_model(model_id, model_dir=_cache_dir)
        else:
            logger.info("Model file found locally at %s", model_path)

        # kraken expects just the model ID (e.g. "10.5281/zenodo.xxx")
        # and searches KRAKEN_MODEL_DIR
        model = models.load_model(model_id, model_dir=_cache_dir)
        _model_cache[model_id] = model
        logger.info("Model loaded and cached: %s", model_id)
        return model

    except Exception as exc:
        logger.error("Failed to load model %s: %s", model_id, exc)
        raise HTTPException(status_code=502, detail=f"Model load failed: {exc}") from exc


# ── FastAPI app ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-load the default model at startup if KRAKEN_DEFAULT_MODEL is set."""
    default = os.environ.get("KRKEN_DEFAULT_MODEL")
    if default:
        logger.info("Pre-loading default model: %s", default)
        try:
            _load_kraken_model(default)
        except Exception as exc:
            logger.warning("Could not pre-load default model: %s", exc)
    yield
    # Shutdown: clear model cache to free RAM
    _model_cache.clear()
    logger.info("Kraken service shutting down, model cache cleared.")


app = fastapi.FastAPI(
    title="kraken OCR Service",
    description="Remote HTR/OCR endpoint powered by kraken",
    version="1.0.0",
    lifespan=lifespan,
)


# ── routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Return service health and loaded model list."""
    return JSONResponse({
        "status": "ok",
        "version": "1.0.0",
        "models_loaded": list(_model_cache.keys()),
        "cache_dir": str(_cache_dir),
    })


@app.get("/models")
async def list_models():
    """Return all model IDs currently loaded in the cache."""
    return JSONResponse({"models": list(_model_cache.keys())})


@app.post("/segment")
async def segment_image(
    image: UploadFile = File(...),
    seg_mode: Annotated[str, Form] = "baseline",
):
    """
    Segment an image into text lines (no OCR).

    Parameters
    ----------
    image : uploaded file
        Document image (JPEG, PNG, TIFF, …)
    seg_mode : "baseline" | "lines"
        Segmentation mode passed to kraken.

    Returns
    -------
    JSON with ``lines`` list.  Each line has ``baseline`` (list of [x,y]
    points) and ``mask`` (polygon mask points).
    """
    try:
        import PIL.Image
        from kraken import binarization
        from kraken import pageseg

        # Read uploaded image into a PIL Image
        img_bytes = await image.read()
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("L")

        # Binarize
        bin_img = binarization.nlbin(img)

        # Segment
        seg_result = pageseg.segment(
            bin_img,
            model=None,  # pure baseline / mask seg, no model needed
            text_direction="horizontal-lr",
            mode=seg_mode,
        )

        # Return serialisable dict
        lines = []
        for record in seg_result.get("script", [{"lines": []}]):
            for line in record.get("lines", []):
                lines.append({
                    "baseline": line.get("baseline", []),
                    "mask": line.get("mask", []),
                    "bbox": line.get("bbox", []),
                })

        return JSONResponse({"lines": lines, "seg_mode": seg_mode})

    except Exception as exc:
        logger.exception("Segmentation failed")
        raise HTTPException(status_code=500, detail=f"Segmentation error: {exc}") from exc


@app.post("/ocr")
async def run_ocr(
    image: UploadFile = File(...),
    model: Annotated[str, Form] = "10.5281/zenodo.20642057",
    seg_mode: Annotated[str, Form] = "baseline",
):
    """
    Run HTR/OCR on an image with a named kraken model.

    Parameters
    ----------
    image : uploaded file
        Document image
    model : str
        Full kraken model identifier (e.g. "10.5281/zenodo.7516057")
    seg_mode : "baseline" | "lines"
        Segmentation mode passed to kraken.

    Returns
    -------
    JSON with ``text`` (transcribed string), ``confidence`` (mean char accuracy),
    ``model`` (model used), and ``lines`` (line‑by‑line transcription list).
    """
    try:
        import io
        import PIL.Image
        from kraken import pageseg
        from kraken import rpred
        from kraken.lib import models

        # Load image
        img_bytes = await image.read()
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("L")

        # Load model
        logger.info("Loading model %s for OCR", model)
        nn = _load_kraken_model(model)

        # Binarize
        from kraken import binarization
        bin_img = binarization.nlbin(img)

        # Segment
        seg = pageseg.segment(
            bin_img,
            model=nn,
            text_direction="horizontal-lr",
            mode=seg_mode,
        )

        # Run recogniser
        pred_it = rpred.rpred(nn, seg["script"][0]["lines"], img)
        lineTexts: list[dict] = []
        confidences: list[float] = []
        full_text_parts: list[str] = []

        for rec in pred_it:
            lineTexts.append({"text": rec.prediction, "confidence": rec.confidence})
            confidences.append(rec.confidence)
            full_text_parts.append(rec.prediction)

        mean_conf = sum(confidences) / len(confidences) if confidences else 0.0
        return JSONResponse({
            "text": "\n".join(full_text_parts),
            "confidence": round(mean_conf, 4),
            "model": model,
            "lines": lineTexts,
            "version": "1.0.0",
        })

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("OCR failed for model %s", model)
        raise HTTPException(status_code=500, detail=f"OCR error: {exc}") from exc


@app.post("/ocr-json")
async def run_ocr_json(image: UploadFile = File(...)):
    """
    Alternative OCR endpoint that accepts a JSON body with a base64‑encoded image.
    Useful when the image is already in memory and can't be sent as multipart.

    Body JSON:
        image : base64 string
        model : str  (optional, default "10.5281/zenodo.20642057")
        seg_mode : str  (optional, default "baseline")
    """
    try:
        import base64
        import io
        import PIL.Image
        from kraken import binarization, pageseg, rpred

        body = await image.read()  # misused as JSON carrier; kept for compat
        data = ujson.loads(body)

        img_bytes = base64.b64decode(data["image"])
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("L")

        model = data.get("model", "10.5281/zenodo.20642057")
        seg_mode = data.get("seg_mode", "baseline")

        nn = _load_kraken_model(model)
        bin_img = binarization.nlbin(img)
        seg = pageseg.segment(bin_img, model=nn, text_direction="horizontal-lr", mode=seg_mode)

        pred_it = rpred.rpred(nn, seg["script"][0]["lines"], img)
        lines_out, confs, texts = [], [], []
        for rec in pred_it:
            lines_out.append({"text": rec.prediction, "confidence": rec.confidence})
            confs.append(rec.confidence)
            texts.append(rec.prediction)

        return JSONResponse({
            "text": "\n".join(texts),
            "confidence": round(sum(confs) / len(confs), 4) if confs else 0.0,
            "model": model,
            "lines": lines_out,
        })
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── run directly ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "service.kraken_service:app",
        host="0.0.0.0",
        port=8765,
        reload=False,
        log_level="info",
    )