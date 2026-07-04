"""
agents/source_description.py — Agent B: Wissenschaftliche Quellenerschliessung
Refactored to use source_heuristic.py (Ad Fontes UZH codicological framework).
Provides full 16-element codicological description + Care-Flag.
Emits BOTH Markdown and structured JSON (per AH-20).
"""

import json
import re
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from utils import gpustack_client as gs
from agents.source_heuristic import (
    MANUSCRIPT_SYSTEM,
    full_codicological,
    for_transcription,
    HANDSCHRIFTEN_ELEMENTS,
)


def describe(doc_id: str, transcription: str, image_path: Optional[str] = None) -> dict:
    """
    Erstellt eine vollstaendige Quellenbeschreibung nach dem Ad Fontes 16-Element-Schema.
    Nutzt Agent A's Transkription + optional das Originalbild.

    Returns a dict with both 'source_description' (markdown str) AND 'source_json'
    (dict keyed by the 16 Ad Fontes element names).
    """
    logger.info(f"[Agent B] Verarbeite: {doc_id}")

    if image_path:
        prompt_obj = full_codicological()
    else:
        prompt_obj = for_transcription()

    user_prompt = prompt_obj.build_user_prompt(image_available=(image_path is not None))

    # Chunking: take first + last part of the transcription so the
    # description is not silently truncated to only page 1 of a multi-page order.
    _chunk_size = 3000
    if len(transcription) > 2 * _chunk_size:
        transcription_snippet = (
            f"[Dokumentanfang — erste {_chunk_size} Zeichen]\n"
            f"{transcription[:_chunk_size]}\n\n"
            f"[... — {len(transcription) - 2*_chunk_size} Zeichen in der Mitte verworfen ...]\n\n"
            f"[Dokumentende — letzte {_chunk_size} Zeichen]\n"
            f"{transcription[-_chunk_size:]}"
        )
    else:
        transcription_snippet = transcription[:2 * _chunk_size]

    full_prompt = (
        f"{MANUSCRIPT_SYSTEM}\n\n"
        f"Transkription des Dokuments (von Agent A):\n\n"
        f"{transcription_snippet}\n\n"
        f"---\n\n"
        f"Anweisungen fuer die Beschreibung:\n{user_prompt}\n\n"
        "Wichtige Anforderung: Antworte ZUERST mit einem JSON-Objekt (siehe Schema unten),\n"
        "DANN im Anschluss mit einem vollstaendigen Fliesstext in Markdown.\n\n"
        "JSON-Schema (16 Elemente - alle Felder ausfuellen):\n"
        + json.dumps(SIXTEEN_ELEMENT_SCHEMA, indent=2, ensure_ascii=False)
        + "\n\n"
        "Regeln:\n"
        "- Verwende KEINE Markdown-Codefences um das JSON.\n"
        "- Gib das JSON als Erstes aus, direkt gefolgt vom Markdown.\n"
        "- Fehlende oder nicht beobachtbare Felder mit null oder leerem String/[] je nach Typ.\n"
        "- JSON erlaubt KEINE Kommentare: kennzeichne unsichere Angaben, indem du dem\n"
        "  Wert die Zeichenkette \" (unsicher)\" anhaengst (z.B. \"Bern (unsicher)\").\n"
    )

    try:
        raw = gs.chat_text(full_prompt, system=None, max_tokens=3500)
    except Exception as e:
        logger.warning(f"[Agent B] VLM-Beschreibung fehlgeschlagen: {e}")
        raw = ""

    # ── Parse Markdown + JSON from response ────────────────────────────────────
    source_json, description_md = _parse_response(raw)

    # Care-Flag analysis
    care = _care_flag(transcription)

    result = {
        "doc_id": doc_id,
        "source_description": description_md,
        "source_json": source_json,
        "care_flag": care,
        "image_path": image_path or "none",
    }

    _save(doc_id, result)
    _validate_and_log(doc_id, source_json)

    logger.info(f"[Agent B] Fertig: {doc_id}")
    return result


# ── JSON Schema (AH-20 acceptance: validates against this schema) ─────────────

SIXTEEN_ELEMENT_SCHEMA = {
    "type": "object",
    "required": [
        "Aufbewahrungsort", "Beschreibstoff", "Blaetter", "Format",
        "Datierung", "Lagen", "Schriftraum_Gliederung", "Schrift",
        "Schreiber", "Ausstattung", "Sprache", "Einband",
        "Provenienz", "Literatur", "Inhalt", "Weitere_Hinweise",
    ],
    "properties": {
        **{elem: {"type": "object", "description": f"Ad Fontes element: {elem}"}
           for elem in HANDSCHRIFTEN_ELEMENTS},
    },
    # Each element object has these optional fields:
    "elementProperties": {
        "wert": {"type": "string"},
        "unsicher": {"type": "boolean"},
        "notiz": {"type": "string"},
    },
}


def _parse_response(raw: str) -> tuple[dict, str]:
    """
    Splits the LLM response into JSON and Markdown.
    JSON is expected FIRST (no code fences), followed by Markdown.
    Falls back gracefully if parsing fails.
    """
    # Try to find JSON object start (first { after any leading whitespace)
    json_start = re.search(r"\{", raw)
    md_start = raw.find("## ")  # Markdown section starts with ## heading

    if json_start and (not md_start or json_start.start() < md_start):
        json_text = raw[json_start.start():]
        json_text = _extract_balanced_json(json_text)
        if json_text:
            try:
                source_json = json.loads(json_text)
                description_md = raw[json_start.start() + len(json_text):].lstrip()
                return source_json, description_md or raw
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"[Agent B] JSON parsing failed: {e}")
    return {}, raw


def _extract_balanced_json(text: str) -> str:
    """Extract the first balanced JSON object from text."""
    depth = 0
    start = None
    for i, c in enumerate(text):
        if c == "{":
            if start is None:
                start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if start is not None and depth == 0:
                return text[start:i + 1]
    return ""


def _care_flag(transcription: str) -> dict:
    # First + last chunk (same strategy as describe())
    _chunk = 2000
    snippet = (
        transcription[:_chunk] + "\n\n[...]\n\n" + transcription[-_chunk:]
        if len(transcription) > 2 * _chunk else transcription
    )
    prompt = (
        MANUSCRIPT_SYSTEM + "\n\n" +
        "Pruefe ob dieses Dokument Care-relevante Inhalte hat.\n\n" +
        snippet + "\n\n" +
        "Antworte als JSON: {is_care_related: bool, care_context: str, "
        "care_types: [string], beteiligte: [string]}"
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=800)
        return json.loads(raw)
    except Exception as e:
        logger.warning(f"[Agent B] Care-Flag fehlgeschlagen: {e}")
        return {"is_care_related": False, "care_context": "", "care_types": [], "beteiligte": []}


def _save(doc_id: str, result: dict):
    """Speichert Quellenbeschreibung als Markdown UND JSON."""
    # Markdown
    out_md = config.DESCRIPTIONS_DIR / f"{doc_id}.md"
    # JSON
    out_json = config.DESCRIPTIONS_DIR / f"{doc_id}.json"

    care = result.get("care_flag", {})
    source_json = result.get("source_json", {})

    # Markdown
    md = (
        f"# Quellenbeschreibung: {doc_id}\n\n"
        f"_Erstellt mit Agentic Historian — Ad Fontes codicological framework (UZH)_\n\n"
        f"---\n\n"
        f"{result.get('source_description', '—')}\n\n"
        f"---\n\n"
        f"## Care-Flag\n\n"
        f"- **Care-relevant:** {'Ja' if care.get('is_care_related') else 'Nein'}\n"
        f"- **Kontext:** {care.get('care_context', '—')}\n"
        f"- **Care-Typen:** {', '.join(care.get('care_types', []) or ['—'])}\n"
        f"- **Beteiligte:** {', '.join(care.get('beteiligte', []) or ['—'])}\n\n"
        f"_Bildgrundlage: {result.get('image_path', 'none')}_\n"
    )

    # JSON (cleaned, schema-aligned)
    json_out = {
        "doc_id": doc_id,
        "ad_fontes_version": "UZH 16-element",
        "elements": source_json,
        "care_flag": care,
        "image_path": result.get("image_path", "none"),
    }

    config.DESCRIPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2, ensure_ascii=False)

    logger.info(f"[Agent B] Gespeichert: {out_md} + {out_json}")


def _validate_and_log(doc_id: str, source_json: dict):
    """Validates the JSON against the 16-element schema and logs warnings."""
    if not source_json:
        logger.warning(f"[Agent B] #{doc_id}: JSON leer/nicht geparst — kein Schema-Check moeglich")
        return

    missing = [e for e in HANDSCHRIFTEN_ELEMENTS if e not in source_json]
    if missing:
        logger.warning(f"[Agent B] #{doc_id}: Fehlende Schema-Elemente: {missing}")
    else:
        logger.info(f"[Agent B] #{doc_id}: JSON Schema-Check bestanden — alle 16 Elemente vorhanden")

    # Warn on null/empty required fields
    for elem in HANDSCHRIFTEN_ELEMENTS:
        val = source_json.get(elem)
        if val is None or val == "" or val == []:
            logger.info(f"[Agent B] #{doc_id}: Element '{elem}' = {val!r} (nicht beobachtbar)")