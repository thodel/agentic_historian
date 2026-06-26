"""
agent_a/pary_ocr.py — Party / PARY HTR model integration (Path 3).

Party is a kraken-format HTR model for medieval/historical documents.
Landing page: https://zenodo.org/records/20642057
DOI: 10.5281/zenodo.20642057

Uses the same kraken CLI infrastructure as kraken_ocr.py but targets
the party model specifically.

Installation:
  kraken get 10.5281/zenodo.20642057

Usage:
  from agent_a.pary_ocr import party_transcribe
  text, lines = party_transcribe("path/to/image.jpg")
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

from agent_a.kraken_ocr import _kraken_available, _run_kraken


PARTY_MODEL_ID = "10.5281/zenodo.20642057"
PARTY_MODEL_NAME = "party"


def _party_available() -> bool:
    """Check if the party model has been downloaded."""
    try:
        result = subprocess.run(
            ["kraken", "list"],
            capture_output=True,
            text=True,
        )
        return PARTY_MODEL_ID in result.stdout or PARTY_MODEL_NAME in result.stdout
    except Exception:
        return False


def ensure_party_model() -> bool:
    """
    Ensure the party model is downloaded.
    Returns True if available after (or already), False on failure.
    """
    if _party_available():
        return True
    try:
        logger.info(f"[party] Downloading model {PARTY_MODEL_ID}...")
        subprocess.run(
            ["kraken", "get", PARTY_MODEL_ID],
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("[party] Model downloaded successfully")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"[party] Failed to download model: {e.stderr}")
        return False


def party_transcribe(
    image_path: str | Path,
    *,
    segment: bool = True,
) -> tuple[str, list[dict]]:
    """
    Run party/PARY HTR on a manuscript image using kraken.

    Args:
        image_path: Path to the input image
        segment:    If True, run baseline segmentation first.
                    If False, kraken auto-segments.

    Returns:
        (transcription_text, lines_metadata)

    Raises:
        RuntimeError if kraken or the party model is not available.
    """
    if not _kraken_available():
        raise RuntimeError("kraken CLI not installed. Run: pip install kraken")

    if not _party_available():
        if not ensure_party_model():
            raise RuntimeError(
                f"Party model ({PARTY_MODEL_ID}) not available. "
                "Run: kraken get 10.5281/zenodo.20642057"
            )

    image_path = Path(image_path)
    logger.info(f"[party] Transcribing: {image_path.name}")

    lines_json: Optional[str] = None
    lines_meta: list[dict] = []

    if segment:
        # Run baseline segmentation (reuse infrastructure from kraken_ocr)
        from agent_a.kraken_ocr import segment_lines
        lines_json, lines_meta = segment_lines(image_path)

    # Run OCR with party model
    try:
        transcription = _run_party_ocr(image_path, lines_json=lines_json)
    except Exception as e:
        logger.warning(f"[party] OCR failed: {e}")
        transcription = ""

    sorted_lines = sorted(
        [(i, ln) for i, ln in enumerate(transcription.splitlines()) if ln.strip()],
        key=lambda x: x[0]
    )
    assembled = "\n".join(ln for _, ln in sorted_lines)

    logger.info(f"[party] Done: {image_path.name} ({len(sorted_lines)} lines)")
    return assembled, lines_meta


def _run_party_ocr(
    image_path: Path,
    *,
    lines_json: Optional[str] = None,
) -> str:
    """Run party HTR via kraken CLI."""
    args = [
        "-i", str(image_path),
        "-",                      # stdout
        "-m", PARTY_MODEL_ID,
        "ocr",
    ]
    if lines_json:
        args.extend(["--lines", lines_json])

    return _run_kraken(args).strip()