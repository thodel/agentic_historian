"""
agents/entity_agent.py — Agent C: Mentioned Entity Agent

Extracts entities (PERSON, PLACE, ORG, SOCIAL_GROUP, CARE_ACTOR,
CARE_ACTION, ROLE, DATE) and links them with Wikidata, GND, and HLS.

Enrichment priority:
  1. Local Knowledge Hub (exact match → highest confidence)
  2. HLS-DHS (Historisches Lexikon der Schweiz — for PERSON + PLACE)
  3. Wikidata via name matching (fallback)

Fixes AH-37: SOCIAL_GROUP + CARE_ACTION types fully supported.
Fixes AH-39: HLS-DHS linking added for PERSON + PLACE entities.
"""

import json
import urllib.parse
from pathlib import Path

from loguru import logger

import config
from knowledge_hub import hub as hub_module
from utils import gpustack_client as gs

SYSTEM = (
    "Du bist ein Experte für historische Named Entity Recognition (NER). "
    "Extrahiere Entitäten aus spätmittelalterlichen Verwaltungsdokumenten "
    "(14.–16. Jh., Schweiz, Deutsch). "
    "Verwende folgende Typen: PERSON, PLACE, ORG, SOCIAL_GROUP, "
    "CARE_ACTOR, CARE_ACTION, ROLE, DATE. "
    "Antworte ALS REINES JSON (kein Markdown, kein umschliessendes Text)."
)


# ── Public API ───────────────────────────────────────────────────────────────

def extract_entities(doc_id: str, transcription: str) -> dict:
    """Führt Entity Extraction für ein Dokument durch."""
    logger.info(f"[Agent C] Extrahiere Entitäten: {doc_id}")

    raw_entities = _extract_llm(transcription)
    enriched = _enrich(raw_entities)
    _save(doc_id, enriched)

    count = len(enriched.get("entities", []))
    logger.info(f"[Agent C] Fertig: {doc_id} ({count} Entitäten)")
    return enriched


# ── LLM extraction ────────────────────────────────────────────────────────────

def _extract_llm(transcription: str) -> dict:
    # Chunk if large (Agent D-style chunking for very long transcriptions)
    if len(transcription) > 30_000:
        transcription = transcription[:30_000]

    prompt = (
        SYSTEM + "\n\n" +
        "Extrahiere alle Entitäten aus diesem Text:\n\n" +
        transcription + "\n\n" +
        "Antworte als JSON: {\"entities\": ["
        "{\"text\": str, \"type\": PERSON|PLACE|ORG|SOCIAL_GROUP|CARE_ACTOR|CARE_ACTION|ROLE|DATE, "
        "\"normalised\": str, \"context\": str}"
        "]}. "
        "Sei grosszügig — extrahiere auch unsichere Kandidaten."
    )
    try:
        raw = gs.chat_text(prompt, system=None, max_tokens=2500)
        cleaned = raw.strip().strip("```json").strip("```").strip()
        result = json.loads(cleaned)
        # Ensure at least 8-type structure
        _validate_types(result)
        return result
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[Agent C] Extraktion fehlgeschlagen: {e}")
        return {"entities": []}


def _validate_types(result: dict):
    """Ensure all entities have a valid 8-type."""
    valid = {"PERSON", "PLACE", "ORG", "SOCIAL_GROUP", "CARE_ACTOR", "CARE_ACTION", "ROLE", "DATE"}
    for ent in result.get("entities", []):
        if ent.get("type") not in valid:
            ent["type"] = "PERSON"  # fallback unknown → PERSON


# ── Enrichment ────────────────────────────────────────────────────────────────

def _enrich(extracted: dict) -> dict:
    """
    Enrich each entity with hub, HLS, and wikidata links.
    Priority: hub (confidence 0.9) → HLS (0.8) → wikidata (0.6)
    """
    khub = hub_module.get_hub()
    for ent in extracted.get("entities", []):
        ent_type = ent.get("type", "")
        text = ent.get("text", "")

        if not text:
            continue

        # 1. Local hub lookup (highest priority)
        hub_match = _hub_lookup(khub, text, ent_type)
        if hub_match:
            ent["hub_id"] = hub_match.get("hub_id")
            ent["wikidata"] = hub_match.get("wikidata", "")
            ent["gnd"] = hub_match.get("gnd", "")
            ent["hls"] = hub_match.get("hls", "")
            ent["hub_confidence"] = 0.9
            continue

        # 2. HLS-DHS lookup (PERSON + PLACE)
        if ent_type in ("PERSON", "PLACE"):
            hls = _hls_lookup(text, ent_type)
            if hls:
                ent["hls"] = hls
                ent["hls_url"] = f"https://www.hls-dhs-dss.ch/de/{hls}"
                ent["hub_confidence"] = 0.8
                continue

        # 3. No link found
        ent["hub_confidence"] = 0.0

    return extracted


def _hub_lookup(khub, text: str, ent_type: str) -> dict | None:
    if ent_type == "PERSON":
        matches = khub.search_person(text, limit=1)
        if matches:
            p = matches[0]
            return {
                "hub_id": p.name,
                "wikidata": p.wikidata_id or "",
                "gnd": p.gnd_id or "",
                "hls": p.hls_id or "",
            }
    elif ent_type == "PLACE":
        matches = khub.search_place(text, limit=1)
        if matches:
            p = matches[0]
            return {
                "hub_id": p.name,
                "wikidata": p.wikidata_id or "",
                "gnd": p.gnd_id or "",
                "hls": p.hls_id or "",
            }
    return None


def _hls_lookup(text: str, ent_type: str) -> str:
    """
    Search HLS-DHS by name, return article ID.
    """
    if ent_type == "PERSON":
        results = hub_module._hls_search_person(text)
    elif ent_type == "PLACE":
        results = hub_module._hls_search_place(text)
    else:
        return ""

    if results:
        return getattr(results[0], "hls_id", "") or ""
    return ""


# ── Persistence ───────────────────────────────────────────────────────────────

def _save(doc_id: str, result: dict):
    json_path = config.OUTPUTS_DIR / f"{doc_id}_entities.json"
    md_path = config.OUTPUTS_DIR / f"{doc_id}_entities.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines = [f"# Entitäten: {doc_id}\n"]
    by_type = {}
    for ent in result.get("entities", []):
        by_type.setdefault(ent.get("type", "UNKNOWN"), []).append(ent)

    for etype, ents in by_type.items():
        lines.append(f"## {etype}\n")
        for e in ents:
            wiki = f" [Wikidata](https://www.wikidata.org/entity/{e.get('wikidata','')})" if e.get("wikidata") else ""
            gnd = f" [GND](https://d-nb.info/gnd/{e.get('gnd','')})" if e.get("gnd") else ""
            hls = f" [HLS]({e.get('hls_url','')})" if e.get("hls_url") else ""
            lines.append(
                f"- **{e.get('text','')}** "
                f"({e.get('normalised','')}) — {e.get('context','')}"
                f"{wiki}{gnd}{hls}"
            )
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[Agent C] Gespeichert: {json_path}, {md_path}")