"""
agents/text_recognition.py — Agent A: Handschriftenerkennung (HTR)

Primary HTR: kraken (best for gothic cursive 14.–16. c.)
Fallback HTR: InternVL3-8B-Instruct via GPUStack (for non-gothic or when kraken unavailable)

No self-QA loop: QA is a separate concern, handled by the orchestrator
after Agent B has contextual knowledge (not the same model rating its own output).
"""

import re
from pathlib import Path

from loguru import logger

import config
from utils import gpustack_client as gs

# Kraken may not be available (DUAL_AVAILABLE check lives in the caller)
try:
    from agent_a.dual_pipeline import _run_kraken
    HAS_KRAKEN = True
except Exception:
    HAS_KRAKEN = False


# ── System Prompts ────────────────────────────────────────────────────────────

SYSTEM_VLM = (
    "Du bist ein Experte für historische Handschriftenerkennung (HTR). "
    "Transkribiere den Text EXAKT wie er erscheint — achte auf Gotische/Kursive "
    "Schriften des 14.–16. Jahrhunderts. Beibehalten: Abkürzungen, Nasalstriche, "
    "Kürzel, Zeilenumbruch-Marker. Gib nur die Transkription aus, keine Kommentare."
)


# ── Public API ───────────────────────────────────────────────────────────────

def process_image(image_path: str | Path) -> dict:
    """
    Agent A full run: HTR + QA + save.

    Priority:
      1. kraken if available (best for gothic cursive)
      2. VLM fallback

    Returns dict with: doc_id, transcription, qa_score, source (kraken|vlm), path
    """
    image_path = Path(image_path)
    doc_id = image_path.stem
    logger.info(f"[Agent A] Verarbeite: {doc_id}")

    r = transcribe_image(image_path)
    path = _save(doc_id, r["transcription"], r["qa_score"], r["source"])
    logger.info(f"[Agent A] Fertig: {doc_id} (source={r['source']}, QA: {r['qa_score']:.2f})")

    return {
        "doc_id": doc_id,
        "transcription": r["transcription"],
        "qa_score": r["qa_score"],
        "source": r["source"],
        "path": str(path),
        "success": bool(r["transcription"]),
    }


def transcribe_image(image_path: str | Path) -> dict:
    """HTR + QA for one image, WITHOUT saving.

    Used both by process_image (which then saves a per-image .txt) and by the
    grouped/order pipeline (which combines pages into one document before saving).
    Returns {transcription, qa_score, source}.
    """
    image_path = Path(image_path)
    doc_id = image_path.stem

    # Try kraken first (the right tool for historical handwriting)
    transcription, source = _try_kraken(image_path)

    # Fallback: VLM if kraken failed or unavailable
    if not transcription:
        transcription = _htr_vlm(image_path)
        source = "vlm"

    # Independent length-based QA (not self-referential)
    qa_score = _quality_score(transcription)
    if qa_score < config.HTR_QUALITY_THRESHOLD:
        logger.warning(
            f"[Agent A] QA-Score {qa_score:.2f} < {config.HTR_QUALITY_THRESHOLD}, "
            f"VLM-Retry für {doc_id}"
        )
        transcription = _htr_vlm(image_path, retry=1)
        source = "vlm_retry"
        qa_score = _quality_score(transcription)

    return {"transcription": transcription, "qa_score": qa_score, "source": source}


def process_file(image_path: str | Path) -> dict:
    """Alias for process_image — kept for orchestrator compatibility."""
    return process_image(image_path)


def save_transcription(doc_id: str, transcription: str, qa_score: float = 0.0,
                       source: str = "grouped") -> Path:
    """Public save helper used by the grouped/order pipeline."""
    return _save(doc_id, transcription, qa_score, source)


# ── Internal HTR methods ──────────────────────────────────────────────────────

def _try_kraken(image_path: Path) -> tuple[str, str]:
    """
    Run kraken OCR if available.
    Returns (transcription, "kraken") or ("", "kraken_unavailable").
    """
    if not HAS_KRAKEN:
        return "", "kraken_unavailable"

    try:
        # _run_kraken returns (text, model_or_error); empty text on failure.
        text, _info = _run_kraken(image_path)
        text = (text or "").strip()
        if text:
            logger.info(f"[Agent A] kraken OK for {image_path.stem} ({len(text)} chars)")
            return text, "kraken"
    except Exception as e:
        logger.warning(f"[Agent A] kraken failed for {image_path.stem}: {e}")

    return "", "kraken_failed"


def _htr_vlm(image_path: Path, retry: int = 0) -> str:
    """
    VLM HTR — fallback only. Best for non-gothic scripts.
    For gothic cursive: prefer kraken (this is the fallback).
    """
    retry_hint = (
        f"\n[Retry {retry}] Sei besonders sorgfältig bei Ligaturen und Abkürzungen."
        if retry > 0
        else ""
    )
    prompt = (
        "Transkribiere den Handschrifttext EXAKT wie er erscheint. "
        "Erhalte Abkürzungen, Nasalstriche, Kürzel. "
        "Trenne Seiten mit '--- SEITE N ---'."
        f"{retry_hint}"
    )

    try:
        result = gs.chat_vision(
            prompt=prompt,
            image_source=str(image_path),
            temperature=1.0,
            max_tokens=32768,
        )
        return result.strip()
    except Exception as e:
        logger.error(f"[Agent A] VLM HTR failed: {e}")
        return ""


def _quality_score(transcription: str) -> float:
    """
    Independent quality check — does NOT use the same VLM that produced the text.
    
    Simple heuristic scoring:
    - Empty → 0.0
    - Too short relative to image (rough) → penalty
    - Contains only whitespace/punctuation → low score
    - Normalised: 0.0–1.0
    """
    if not transcription.strip():
        return 0.0

    text = transcription.strip()

    # Penalise: mostly punctuation or very short
    alpha_ratio = sum(c.isalpha() for c in text) / max(len(text), 1)
    if alpha_ratio < 0.1:
        return 0.2
    if len(text) < 20:
        return 0.3

    # Length-based confidence (rough proxy; no self-rating)
    # Very long transcriptions of a single page are likely over-confident
    score = min(0.9, 0.5 + 0.1 * (len(text) / 500))
    return round(score, 2)


# ── Persistence ───────────────────────────────────────────────────────────────

def _save(doc_id: str, transcription: str, qa_score: float, source: str) -> Path:
    """Speichert Transkription als .txt."""
    out_path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
    header = (
        f"# Transkription: {doc_id}\n"
        f"# QA-Score: {qa_score:.2f}\n"
        f"# HTR-Source: {source}\n"
        f"# Modell: {config.GPUSTACK_MODEL_VISION}\n\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + transcription)
    logger.info(f"[Agent A] Gespeichert: {out_path}")
    return out_path