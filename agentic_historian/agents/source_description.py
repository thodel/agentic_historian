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


# Human-pinned SourceCriteria field → Ad-Fontes element (+ value formatter).
# When a historian pins a field (HITL Gate 1), Agent B must ADOPT it, not
# re-infer, and the element is stamped quelle="historiker" (#147).
_PIN_TO_ELEMENT = {
    "century": ("Datierung", lambda v: f"{v}. Jahrhundert"),
    "lang":    ("Sprache",   lambda v: str(v)),
    "script":  ("Schrift",   lambda v: str(v)),
}


def pins_from_runstate(state) -> dict:
    """Extract the historian-pinned criteria (last-wins) from a RunState."""
    pins: dict = {}
    for o in state.human_overrides:
        if o.get("field") in _PIN_TO_ELEMENT and o.get("value") is not None:
            pins[o["field"]] = o["value"]
    return pins


def _pin_constraint(pins: dict) -> str:
    lines = [f"- {elem} = {fmt(pins[f])}"
             for f, (elem, fmt) in _PIN_TO_ELEMENT.items() if pins.get(f) is not None]
    if not lines:
        return ""
    return ("GESICHERT durch Historiker:in — ÜBERNIMM diese Werte wörtlich und "
            "leite sie NICHT neu ab:\n" + "\n".join(lines) + "\n\n")


def _apply_pins(source_json: dict, pins: dict) -> dict:
    """Overwrite the mapped Ad-Fontes elements with the pinned values."""
    if not isinstance(source_json, dict) or not pins:
        return source_json
    for field, (element, fmt) in _PIN_TO_ELEMENT.items():
        if pins.get(field) is None:
            continue
        el = source_json.get(element)
        el = el if isinstance(el, dict) else {}
        el["wert"] = fmt(pins[field])
        el["quelle"] = "historiker"
        source_json[element] = el
    return source_json


def describe(doc_id: str, transcription: str, image_path: Optional[str] = None,
             pins: Optional[dict] = None) -> dict:
    """
    Erstellt eine vollstaendige Quellenbeschreibung nach dem Ad Fontes 16-Element-Schema.
    Nutzt Agent A's Transkription + optional das Originalbild.

    ``pins`` are historian-confirmed criteria (century/lang/script) that Agent B
    must adopt: they are injected as constraints and their Ad-Fontes elements are
    overwritten with quelle="historiker" (#147).

    Returns a dict with both 'source_description' (markdown str) AND 'source_json'
    (dict keyed by the 16 Ad Fontes element names).
    """
    logger.info(f"[Agent B] Verarbeite: {doc_id}")
    pins = pins or {}

    # #276: never fabricate a description for a degenerate/illegible transcription.
    # u-17__ was a VLM repetition-collapse ("uuuu") that Agent B rationalised into a
    # fake "Zählsystem" interpretation. Detect the collapse (reusing Fix B's
    # _is_degenerate) and return an honest low-confidence result WITHOUT spending any
    # LLM call (neither the description nor the care-flag). Human pins still apply.
    from agents.text_recognition import _is_degenerate
    if _is_degenerate(transcription):
        logger.warning(f"[Agent B] Transkription degeneriert/unlesbar — keine LLM-Beschreibung: {doc_id}")
        source_json = _apply_pins({elem: None for elem in HANDSCHRIFTEN_ELEMENTS}, pins)
        result = {
            "doc_id": doc_id,
            "source_description": ("Transkription unlesbar oder degeneriert "
                                   "(Wiederholungskollaps) — keine belastbare "
                                   "Quellenbeschreibung möglich."),
            "source_json": source_json,
            "care_flag": {"is_care_related": False, "care_context": "",
                          "care_types": [], "beteiligte": []},
            "image_path": image_path or "none",
            "low_confidence": True,
        }
        _save(doc_id, result)
        return result

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
        f"{_pin_constraint(pins)}"
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

    # Human pins are authoritative — overwrite the mapped elements (#147).
    source_json = _apply_pins(source_json, pins)

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
    default = {"is_care_related": False, "care_context": "", "care_types": [], "beteiligte": []}
    try:
        # gpt-oss is a reasoning model — it spends tokens on reasoning_content
        # before the JSON, so 800 truncated the answer to null. Give it room.
        raw = gs.chat_text(prompt, system=None, max_tokens=2500)
        # Parse defensively: the JSON may be wrapped in reasoning text / fences.
        json_text = _extract_balanced_json(raw or "")
        if not json_text:
            raise ValueError("no JSON object found in care-flag response")
        parsed = json.loads(json_text)
    except Exception as e:
        logger.warning(f"[Agent B] Care-Flag fehlgeschlagen: {e}")
        return dict(default)
    # Normalise: always return the expected keys with the expected types.
    return {
        "is_care_related": bool(parsed.get("is_care_related", False)),
        "care_context": parsed.get("care_context") or "",
        "care_types": parsed.get("care_types") or [],
        "beteiligte": parsed.get("beteiligte") or [],
    }


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