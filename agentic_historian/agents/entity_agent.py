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
    "Du bist ein Experte für historische Named Entity Recognition (NER) in "
    "spätmittelalterlichen Verwaltungsdokumenten (14.–16. Jh., Schweiz). "
    "Extrahiere ALLE Entitäten der folgenden acht Typen:\n"
    "- PERSON: benannte Einzelpersonen (z.B. «Hans von Wiler»)\n"
    "- PLACE: Orte, Gewässer, Gebäude (z.B. «Thun», «uff Burgdorf», «Aare»)\n"
    "- ORG: Institutionen, Ämter, Zünfte (z.B. «Rat zu Bern», «Spital»)\n"
    "- SOCIAL_GROUP: soziale Kategorien/Gruppen (z.B. «arme lüt», «erbar lüt», "
    "«Vaganten», «Juden», «gesellen», «Knecht», «Magd»)\n"
    "- CARE_ACTOR: Personen in Fürsorge-/Dienstverhältnissen (z.B. Pflegende, Dienstbot:in)\n"
    "- CARE_ACTION: Fürsorge-/Dienst-Handlungen (z.B. «versorgung», «pflege», "
    "«dienst», «erziehung», «almosen»)\n"
    "- ROLE: Ämter/Rollen/Berufe (z.B. «Vogt», «Schultheiss», «Ritter»)\n"
    "- DATE: explizite Datums- und Zeitangaben\n"
    "SOCIAL_GROUP und CARE_ACTION sind für die Forschung zentral — übersieh sie nicht. "
    "Antworte ALS REINES JSON (kein Markdown, kein umschliessender Text)."
)

# Types linked to the hub's controlled vocabulary (Taxonomien) rather than to a
# person/place register. These carry the research payload of TP1 (social
# taxonomies) and TP2 (care).
VOCAB_TYPES = {"SOCIAL_GROUP", "CARE_ACTION", "CARE_ACTOR", "ROLE"}


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
    """Reichert Entitäten gegen den lokalen Hub an:

    - PERSON / PLACE         → Personen-/Orte-Register (id, wikidata, gnd, hls)
    - SOCIAL_GROUP / CARE_*  → kontrolliertes Vokabular / Taxonomien (controlled_vocab)
    - ROLE                   → kontrolliertes Vokabular
    """
    for ent in extracted.get("entities", []):
        ent_type = ent.get("type", "")
        text = ent.get("normalised") or ent.get("text", "")

        if ent_type == "PERSON":
            _link_register(ent, hub.find_person(text))
        elif ent_type == "PLACE":
            _link_register(ent, hub.find_place(text))
        elif ent_type in VOCAB_TYPES:
            term = hub.match_vocabulary(text)
            if term:
                ent["controlled_vocab"] = term
                ent["hub_confidence"] = 0.8

    return extracted


def _link_register(ent: dict, match: dict | None) -> None:
    """Attach hub register IDs (person/place) to an entity, if matched."""
    if not match:
        return
    ent["hub_id"] = match.get("id")
    ent["wikidata"] = match.get("wikidata", "")
    ent["gnd"] = match.get("gnd", "")
    ent["hls"] = match.get("hls", "")
    ent["hub_confidence"] = 0.9


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

    # Stable type order so the social/care payload types are always visible.
    type_order = ["PERSON", "PLACE", "ORG", "SOCIAL_GROUP", "CARE_ACTOR",
                  "CARE_ACTION", "ROLE", "DATE"]
    ordered = [t for t in type_order if t in by_type] + \
              [t for t in by_type if t not in type_order]
    for etype in ordered:
        lines.append(f"## {etype}\n")
        for e in by_type[etype]:
            refs = []
            if e.get("wikidata"):
                refs.append(f"Wikidata: {e['wikidata']}")
            if e.get("gnd"):
                refs.append(f"GND: {e['gnd']}")
            if e.get("hls"):
                refs.append(f"HLS: {e['hls']}")
            if e.get("controlled_vocab"):
                refs.append(f"Vokabular: {e['controlled_vocab']}")
            tag = f" [{' | '.join(refs)}]" if refs else ""
            lines.append(f"- **{e.get('text','')}**: {e.get('context','')}{tag}")
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))