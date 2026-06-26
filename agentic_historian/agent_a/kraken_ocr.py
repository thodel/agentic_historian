"""
agent_a/kraken_ocr.py — Kraken OCR pipeline for Agent A (Path 2).

Workflow:
  1. Binarize image (optional, improves segmentation)
  2. Segment into lines via `kraken segment -bl`
  3. OCR each line with a kraken model (or pass lines to HF model)
  4. Assemble page transcription

Requires: `pip install kraken` and a downloaded model.
  kraken get <model-id>   # download from Zenodo
  kraken list             # show available models
"""

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Helpers ──────────────────────────────────────────────────────────────────

def _kraken_available() -> bool:
    return shutil.which("kraken") is not None


def _run_kraken(args: list[str], *, cwd: Optional[str] = None) -> str:
    """Run kraken CLI and return stdout."""
    cmd = ["kraken"] + args
    logger.debug(f"[kraken] Running: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"kraken failed: {result.stderr.strip()}")
    return result.stdout


# ── Segmentation ─────────────────────────────────────────────────────────────

def segment_lines(image_path: str | Path) -> tuple[str, list[dict]]:
    """
    Runs `kraken segment -bl` on the image to detect baselines.

    Returns:
        (baseline_image_path, lines_list)
        lines_list: list of dicts with keys:
          - baseline: [x0, y0, x1, y1] (baseline coordinates)
          - mask: path to line mask image (temp)
          - order: reading order index
    """
    if not _kraken_available():
        raise RuntimeError("kraken CLI not installed. Run: pip install kraken")

    image_path = Path(image_path)
    with tempfile.TemporaryDirectory() as tmpdir:
        lines_json = Path(tmpdir) / "lines.json"
        mask_dir   = Path(tmpdir) / "masks"

        # Produce mask images + JSON ordering file
        _run_kraken([
            "-i", str(image_path),
            str(lines_json),
            "segment",
            "-bl",                       # baseline segmenter
            "-o", str(lines_json),       # output JSON
            "--pad", "0",                # no padding around lines
            "-m", str(mask_dir),         # write mask images
        ])

        with open(lines_json) as f:
            data = json.load(f)

    # Parse lines from the JSON structure
    # kraken outputs: { "lines": [ { "baseline": [...], "mask": "...", "order": N }, ... ] }
    raw_lines = data.get("lines", [])
    parsed = []
    for ln in raw_lines:
        parsed.append({
            "baseline": ln.get("baseline", []),
            "mask":     ln.get("mask", ""),
            "order":    ln.get("order", 0),
        })

    # The "bw" image from binarization is also produced; return the lines JSON path
    logger.info(f"[kraken] Segmented {len(parsed)} lines from {image_path.name}")
    return str(lines_json), parsed


# ── OCR ──────────────────────────────────────────────────────────────────────

def ocr_with_kraken(
    image_path: str | Path,
    model_id: str,
    *,
    lines_json: Optional[str] = None,
) -> str:
    """
    Runs kraken OCR on a full page image (auto-seg + rec).

    Args:
        image_path: Path to the input image
        model_id:   Kraken model ID (e.g. "10.5281/zenodo.10592716")
        lines_json: Optional pre-computed lines JSON from segment_lines().
                    If omitted, kraken will re-segment each call.
    """
    if not _kraken_available():
        raise RuntimeError("kraken CLI not installed. Run: pip install kraken")

    image_path = Path(image_path)
    args = [
        "-i", str(image_path),
        "-",                       # write to stdout
        "-m", model_id,
        "ocr",
    ]
    if lines_json:
        args.extend(["--lines", lines_json])

    result = _run_kraken(args)
    return result.strip()


def ocr_lines_with_kraken(
    image_path: str | Path,
    lines_json: str,
    model_id: str,
) -> list[tuple[int, str]]:
    """
    OCR individual lines using pre-detected baselines.

    Args:
        image_path: Full page image
        lines_json: Path to lines JSON from segment_lines()
        model_id:   Kraken model ID

    Returns:
        List of (order_index, transcription) tuples, sorted by reading order.
    """
    if not _kraken_available():
        raise RuntimeError("kraken CLI not installed. Run: pip install kraken")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        output_txt = tmpdir / "lines_output.txt"

        _run_kraken([
            "-i", str(image_path),
            str(output_txt),
            "-m", model_id,
            "ocr",
            "--lines", lines_json,
        ])

        text = output_txt.read_text()

    # kraken writes one line per input line, blank line separates pages
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return [(i, ln) for i, ln in enumerate(lines)]


# ── Full pipeline ─────────────────────────────────────────────────────────────

def kraken_transcribe(
    image_path: str | Path,
    model_id: str,
    *,
    segment: bool = True,
) -> tuple[str, list[dict]]:
    """
    Full kraken pipeline: segment → OCR → assembled transcription.

    Returns:
        (transcription_text, lines_metadata)

    Raises:
        RuntimeError if kraken is not installed.
    """
    image_path = Path(image_path)
    logger.info(f"[kraken] Transcribing: {image_path.name} with model {model_id}")

    lines_json = None
    if segment:
        lines_json, lines_meta = segment_lines(image_path)
        transcription = ocr_lines_with_kraken(image_path, lines_json, model_id)
    else:
        lines_meta = []
        transcription = ocr_with_kraken(image_path, model_id)

    # Sort by reading order and join
    sorted_lines = sorted(transcription, key=lambda x: x[0])
    assembled = "\n".join(ln for _, ln in sorted_lines)

    logger.info(f"[kraken] Done: {image_path.name} ({len(sorted_lines)} lines)")
    return assembled, lines_meta