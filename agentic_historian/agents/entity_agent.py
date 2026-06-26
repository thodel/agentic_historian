"""
agents/entity_agent.py — Agent C: Mentioned Entity Agent
Extrahiert Entitäten (PERSON, PLACE, ORG, SOCIAL_GROUP, CARE_ACTOR,
CARE_ACTION, ROLE, DATE) und verlinkt sie mit Wikidata, GND und dem Hub.
"""

import json
from pathlib import Path

from loguru import logger

import config
from knowledge_hub import hub
from utils import gpustack_client as gs

SYSTEM = (
    "Du bist ein Experte für historische Named Entity Recognition (NER). "
    "Extrahiere Entitäten aus spätmittelalterlichen Verwaltungsdokumenten (14.–16. Jh., Schweiz). "
    "Verwende folgende Typen: PERSON, PLACE, ORG, SOCIAL_GROUP, "
    "CARE_ACTOR, CARE_ACTION, ROLE, DATE. "
    "Antworte ALS REINES JSON (kein Markdown, kein umschliessendes Text)."
)


def extract_entities(doc_id: str, transcription: str) -> dict:
    """Führt Entity Extraction für ein Dokument durch."""
    logger.info(f"[Agent C] Extrahiere Entitäten: {doc_id}")

    # 1) LLM-gestützte Extraktion
    raw_entities = _extract_llm(transcription)

    # 2) Anreicherung mit Hub-Daten (lokaler Knowledge Hub hat Priorität)
    enriched = _enrich(raw_entities)

    # 3) Speichern
    _save(doc_id, enriched, transcription)
    logger.info(f"[Agent C] Fertig: {doc_id} ({len(enriched.get('entities', []))} Entitäten)")
    return enriched


def _extract_llm(transcription: str) -> dict:
    prompt = (
        SYSTEM + "\n\n" +
        "Extrahiere alle Entitäten aus diesem Text:\n\n" + transcription[:4000] + "\n\n" +
        "Antworte als JSON-Objekt mit Schlüssel 'entities' (Array). "
        "Jeder Eintrag: {text, type, normalised, context}. "
        "Bei DATE: zusätzlich {normalized_date}. "
        "Sei grosszügig — extrahiere auch unsichere Kandidaten."
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=2000)
        # Manchmal gibt das Modell ```json ... ``` zurück
        cleaned = raw.strip().strip("```json").strip("```").strip()
        return json.loads(cleaned)
    except Exception as e:
        logger.error(f"[Agent C] Extraktion fehlgeschlagen: {e}")
        return {"entities": []}


def _enrich(extracted: dict) -> dict:
    """Reichert Entitäten mit Hub- und Wikidata-Links an."""
    entities = extracted.get("entities", [])
    for ent in entities:
        ent_type = ent.get("type", "")
        text = ent.get("text", "")
        hub_match = _hub_lookup(text, ent_type)
        if hub_match:
            ent["hub_id"] = hub_match.get("id")
            ent["wikidata"] = hub_match.get("wikidata", "")
            ent["gnd"] = hub_match.get("gnd", "")
            ent["hub_confidence"] = 0.9

    return extracted


def _hub_lookup(text: str, ent_type: str):
    """Schaut zuerst im lokalen Hub nach, dann ggf. Wikidata."""
    if ent_type == "PERSON":
        matches = hub.search_person(text)
        return matches[0] if matches else None
    if ent_type == "PLACE":
        matches = hub.search_place(text)
        return matches[0] if matches else None
    return None


def _save(doc_id: str, result: dict, transcription: str):
    """Speichert JSON + Markdown."""
    # JSON
    json_path = config.OUTPUTS_DIR / f"{doc_id}_entities.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # Markdown
    md_path = config.OUTPUTS_DIR / f"{doc_id}_entities.md"
    lines = [f"# Entitäten: {doc_id}\n"]
    by_type = {}
    for ent in result.get("entities", []):
        by_type.setdefault(ent.get("type", "UNKNOWN"), []).append(ent)

    for etype, ents in by_type.items():
        lines.append(f"## {etype}\n")
        for e in ents:
            wikidata = f" [Wikidata: {e.get('wikidata','')}]" if e.get("wikidata") else ""
            gnd = f" [GND: {e.get('gnd','')}]" if e.get("gnd") else ""
            lines.append(f"- **{e.get('text','')}**: {e.get('context','')}{wikidata}{gnd}")
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))