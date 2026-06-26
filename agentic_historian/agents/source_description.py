"""
agents/source_description.py — Agent B: Wissenschaftliche Quellenerschliessung
Refactored to use source_heuristic.py (Ad Fontes UZH codicological framework).
Provides full 16-element codicological description + Care-Flag.
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs
from agents.source_heuristic import (
    MANUSCRIPT_SYSTEM,
    full_codicological,
    for_transcription,
)


def describe(doc_id: str, transcription: str, image_path: Optional[str] = None) -> dict:
    """
    Erstellt eine vollstaendige Quellenbeschreibung nach dem Ad Fontes 16-Element-Schema.
    Nutzt Agent A's Transkription + optional das Originalbild.
    """
    logger.info(f"[Agent B] Verarbeite: {doc_id}")

    # Build prompt using the heuristic framework
    if image_path:
        prompt_obj = full_codicological()
    else:
        prompt_obj = for_transcription()

    user_prompt = prompt_obj.build_user_prompt(image_available=(image_path is not None))

    # Combine with transcription for context
    full_prompt = (
        f"{MANUSCRIPT_SYSTEM}\n\n"
        f"Transkription des Dokuments (von Agent A):\n\n"
        f"{transcription[:4000]}\n\n"
        f"---\n\n"
        f"Anweisungen fuer die Beschreibung:\n{user_prompt}"
    )

    try:
        description_md = gs.chat_text(full_prompt, system=None, max_tokens=2000)
    except Exception as e:
        logger.warning(f"[Agent B] VLM-Beschreibung fehlgeschlagen: {e}")
        description_md = "Fehler bei der Beschreibung."

    # Care-Flag analysis
    care = _care_flag(transcription)

    result = {
        "doc_id": doc_id,
        "source_description": description_md,
        "care_flag": care,
        "image_path": image_path or "none",
    }

    _save(doc_id, result)
    logger.info(f"[Agent B] Fertig: {doc_id}")
    return result


def _care_flag(transcription: str) -> dict:
    prompt = (
        MANUSCRIPT_SYSTEM + "\n\n" +
        "Pruefe ob dieses Dokument Care-relevante Inhalte hat.\n\n" +
        transcription[:3000] + "\n\n" +
        "Antworte als JSON: {is_care_related: bool, care_context: str, "
        "care_types: [string], beteiligte: [string]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=600)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Care-Flag fehlgeschlagen: {e}")
        return {"is_care_related": False, "care_context": "", "care_types": [], "beteiligte": []}


def _save(doc_id: str, result: dict):
    """Speichert Quellenbeschreibung als Markdown (Ad Fontes 16-element format)."""
    out_path = config.DESCRIPTIONS_DIR / f"{doc_id}.md"
    r = result

    care = r.get("care_flag", {})
    md = (
        f"# Quellenbeschreibung: {doc_id}\n\n"
        f"_Erstellt mit Agentic Historian — Ad Fontes codicological framework (UZH)_\n\n"
        f"---\n\n"
        f"{r.get('source_description', '—')}\n\n"
        f"---\n\n"
        f"## Care-Flag\n\n"
        f"- **Care-relevant:** {'Ja' if care.get('is_care_related') else 'Nein'}\n"
        f"- **Kontext:** {care.get('care_context', '—')}\n"
        f"- **Care-Typen:** {', '.join(care.get('care_types', []) or ['—'])}\n"
        f"- **Beteiligte:** {', '.join(care.get('beteiligte', []) or ['—'])}\n\n"
        f"_Bildgrundlage: {r.get('image_path', 'none')}_\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)