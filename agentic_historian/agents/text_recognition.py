"""
agents/text_recognition.py — Agent A: Handschriftenerkennung (HTR)
Nutzt InternVL3-8B-Instruct via GPUStack.
"""

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs

# ── System Prompt ────────────────────────────────────────────────────────────

SYSTEM = (
    "Du bist ein Experte für historische Handschriftenerkennung (HTR). "
    "Transkribiere den Text EXAKT wie er erscheint — achte auf Gotische/Kursive "
    "Schriften des 14.–16. Jahrhunderts. Beibehalten: Abkürzungen, Nasalstriche, "
    "Kürzel, Zeilenumbruch-Marker. Gib nur die Transkription aus, keine Kommentare."
)

QA_SYSTEM = (
    "Du bist ein QC-System. Begutachte die folgende Transkription eines "
    "historischen Dokuments (14.–16. Jh.) und vergib einen Score von 0.0–1.0. "
    "0.0 = unlesbar/nicht transkribiert, 1.0 = perfekt. "
    "Antworte NUR mit einer Zahl zwischen 0.0 und 1.0."
)

# ── Agent A ──────────────────────────────────────────────────────────────────

def process_image(image_path: str | Path) -> tuple[str, float]:
    """
    Führt HTR auf einem Bild aus und gibt (transcription, qa_score) zurück.
    Bei QA < Schwellenwert: Retry bis MAX_RETRIES.
    """
    image_path = Path(image_path)
    doc_id = image_path.stem
    logger.info(f"[Agent A] Verarbeite: {doc_id}")

    transcription = _htr(image_path)
    qa_score = _qa(transcription, image_path)
    attempts = 1

    while qa_score < config.HTR_QUALITY_THRESHOLD and attempts < config.MAX_RETRIES:
        logger.warning(
            f"[Agent A] QA-Score {qa_score:.2f} < {config.HTR_QUALITY_THRESHOLD}, "
            f"Retry {attempts+1}/{config.MAX_RETRIES}"
        )
        transcription = _htr(image_path, retry=attempts)
        qa_score = _qa(transcription, image_path)
        attempts += 1

    logger.info(f"[Agent A] Fertig: {doc_id} (QA: {qa_score:.2f}, Versuche: {attempts})")
    return transcription, qa_score


def _htr(image_path: Path, retry: int = 0) -> str:
    """InternVL3-Call für Handschriftenerkennung."""
    retry_hint = (
        f"\n[Retry {retry}] Dies ist ein Wiederholungsversuch — "
        "sei besonders sorgfältig bei Ligaturen und Abkürzungen."
        if retry > 0
        else ""
    )
    prompt = (
        "Transkribiere den Handschrifttext EXAKT. "
        "Erhalte Abkürzungen, Nasalstriche, Kürzel. "
        "Trenne Seiten mit '--- SEITE N ---'."
        f"{retry_hint}"
    )

    # Hinweis: System-Messages verursachen 400 bei Vision-Listen in diesem Setup
    # → Instructions inline als Text im Content-Block
    try:
        result = gs.chat_vision(
            prompt=prompt,
            image_source=str(image_path),
            temperature=1.0,
            max_tokens=32768,
        )
        return result.strip()
    except Exception as e:
        logger.error(f"[Agent A] HTR fehlgeschlagen: {e}")
        return ""


def _qa(transcription: str, image_path: Path) -> float:
    """QA-Check: gibt Score 0.0–1.0 zurück."""
    if not transcription.strip():
        return 0.0

    try:
        # QA call: model bewertet eigene Transcription
        # (Wir zeigen dem Modell das Bild nochmal + Transkription)
        score_text = gs.chat_vision(
            prompt=(
                f"Begutachte diese Transkription:\n\n{transcription}\n\n"
                "Vergib einen QC-Score von 0.0 bis 1.0. "
                "0.0 = grundsätzlich falsch/unlesbar, 1.0 = korrekte Transkription. "
                "Antworte NUR mit einer Zahl."
            ),
            image_source=str(image_path),
            temperature=0.3,
            max_tokens=50,
        )
        # Extrahiere Zahl aus Response
        match = re.search(r"0\.\d+", score_text)
        if match:
            return float(match.group())
    except Exception as e:
        logger.warning(f"[Agent A] QA-Call fehlgeschlagen: {e}")

    return 0.5  # Fallback


def save_transcription(doc_id: str, transcription: str, qa_score: float):
    """Speichert Transkription als .txt."""
    out_path = config.TRANSCRIPTIONS_DIR / f"{doc_id}.txt"
    header = (
        f"# Transkription: {doc_id}\n"
        f"# QA-Score: {qa_score:.2f}\n"
        f"# Modell: {config.GPUSTACK_MODEL_VISION}\n\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + transcription)
    logger.info(f"[Agent A] Gespeichert: {out_path}")
    return out_path


def process_file(image_path: str | Path) -> dict:
    """Full run: HTR + QA + Speichern. Gibt Dict mit Resultaten."""
    image_path = Path(image_path)
    doc_id = image_path.stem

    transcription, qa_score = process_image(image_path)
    path = save_transcription(doc_id, transcription, qa_score)

    return {
        "doc_id": doc_id,
        "transcription": transcription,
        "qa_score": qa_score,
        "path": str(path),
        "success": bool(transcription),
    }