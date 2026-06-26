"""
agents/entity_agent.py — Agent C: Mentioned Entity Agent

Extracts entities (PERSON, PLACE, ORG, SOCIAL_GROUP, CARE_ACTOR,
CARE_ACTION, ROLE, DATE) and links them with Wikidata, GND, HLS, and
the local Knowledge Hub.

Entity linking priority:
  1. Local hub exact match (confidence 0.9)
  2. Embedding + Reranker semantic search (confidence 0.7) — AH-43
  3. HLS-DHS fallback (confidence 0.8)
  4. Wikidata name lookup (confidence 0.6)

Supports all 8 entity types including SOCIAL_GROUP and CARE_ACTION (AH-37).
"""

import json
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
    # Chunk if very large (Agent D-style for performance)
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
        _validate_types(result)
        return result
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[Agent C] Extraktion fehlgeschlagen: {e}")
        return {"entities": []}


def _validate_types(result: dict):
    valid = {"PERSON", "PLACE", "ORG", "SOCIAL_GROUP", "CARE_ACTOR", "CARE_ACTION", "ROLE", "DATE"}
    for ent in result.get("entities", []):
        if ent.get("type") not in valid:
            ent["type"] = "PERSON"


# ── Enrichment (hub → embedding → HLS → wikidata) ───────────────────────────

def _enrich(extracted: dict) -> dict:
    khub = hub_module.get_hub()
    for ent in extracted.get("entities", []):
        ent_type = ent.get("type", "")
        text = ent.get("text", "")

        if not text:
            continue

        # 1. Exact hub match (highest priority)
        hub_match = _hub_lookup(khub, text, ent_type)
        if hub_match:
            ent.update(hub_match)
            ent["hub_confidence"] = 0.9
            ent["link_method"] = "hub_exact"
            continue

        # 2. Semantic link via embedding + reranker (AH-43)
        if ent_type in ("PERSON", "PLACE"):
            semantic = _semantic_link(khub, text, ent_type)
            if semantic:
                ent.update(semantic)
                ent["link_method"] = "embedding_rerank"
                continue

        # 3. HLS-DHS fallback
        if ent_type in ("PERSON", "PLACE"):
            hls = _hls_lookup(text, ent_type)
            if hls:
                ent["hls"] = hls
                ent["hls_url"] = f"https://www.hls-dhs-dss.ch/de/{hls}"
                ent["hub_confidence"] = 0.8
                ent["link_method"] = "hls_dhs"
                continue

        ent["hub_confidence"] = 0.0
        ent["link_method"] = "none"

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


def _semantic_link(khub, text: str, ent_type: str) -> dict | None:
    """
    Use embedding + reranker to find best hub match for a given entity name.
    Only for PERSON + PLACE. Returns None if no confident match.
    """
    # Collect hub candidates
    if ent_type == "PERSON":
        candidates = [p.name for p in khub.persons]
    elif ent_type == "PLACE":
        candidates = [p.name for p in khub.places]
    else:
        return None

    if not candidates:
        return None

    # Embed query and candidates, then rerank
    try:
        top = gs.rerank(query=text, documents=candidates, top_n=3)
        if not top:
            return None

        best = top[0]
        # Threshold: reranker score must be > 0.3 for semantic link
        if best.get("score", 0) < 0.3:
            return None

        # Resolve back to Person/Place object
        matched_name = best["document"]
        if ent_type == "PERSON":
            obj = khub.find_person(matched_name)
        else:
            obj = khub.find_place(matched_name)

        if obj:
            return {
                "hub_id": getattr(obj, "name", matched_name),
                "wikidata": getattr(obj, "wikidata_id", "") or "",
                "gnd": getattr(obj, "gnd_id", "") or "",
                "hls": getattr(obj, "hls_id", "") or "",
                "hub_confidence": round(best.get("score", 0.5), 3),
            }
    except Exception as e:
        logger.warning(f"[Agent C] Semantic link failed for '{text}': {e}")
    return None


def _hls_lookup(text: str, ent_type: str) -> str:
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
            wiki = (f" [Wikidata](https://www.wikidata.org/entity/{e.get('wikidata','')})"
                    if e.get("wikidata") else "")
            gnd = (f" [GND](https://d-nb.info/gnd/{e.get('gnd','')})"
                   if e.get("gnd") else "")
            hls = f" [HLS]({e.get('hls_url','')})" if e.get("hls_url") else ""
            conf_note = f" (conf={e.get('hub_confidence', 0):.2f}, {e.get('link_method','?')})"
            lines.append(
                f"- **{e.get('text','')}** "
                f"({e.get('normalised','')}) — {e.get('context','')}"
                f"{wiki}{gnd}{hls}{conf_note}"
            )
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[Agent C] Gespeichert: {json_path}, {md_path}")