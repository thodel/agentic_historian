"""
agents/entity_agent.py — Agent C: Mentioned Entity Agent

Extracts entities (PERSON, PLACE, ORG, SOCIAL_GROUP, CARE_ACTOR,
CARE_ACTION, ROLE, DATE) and links them with the local Knowledge Hub,
HLS-DHS (Historisches Lexikon der Schweiz), and Wikidata.

Entity linking priority (PERSON / PLACE):
  1. Hub exact match — label="high"
     Person/place name found verbatim in the hub (HBLS person database).
     hub_exact is a bidirectional substring match: the entity name must
     appear in the hub canonical name OR any registered variant, and the
     hub name must appear in the entity's text.  NOT a calibrated score.
  2. Semantic search via embedding + reranker — label="medium" — AH-43
  3. HLS-DHS live lookup — label="low"
     Live API call to hls-dhs-dss.ch; returns an HLS ID if the name
     matches.  Low confidence because HLS contains many similar names
     with no disambiguation.
  4. No link — label="unverified"

SOCIAL_GROUP / CARE_ACTION / CARE_ACTOR / ROLE → controlled vocabulary
(label="medium").

Note: confidence labels (high/medium/low/unverified) are qualitative
heuristics, not calibrated probabilities.  Do NOT treat them as
p < 0.9 or similar numeric thresholds.

Fixes AH-37: all 8 entity types fully supported.
Fixes AH-39: HLS-DHS linking for PERSON + PLACE.
Fixes AH-43: semantic entity linking via embedding + reranker.
"""

import json
import re

from loguru import logger

import config
from knowledge_hub import hub
from utils import gpustack_client as gs


SYSTEM = (
    "Du bist ein Experte für historische Named Entity Recognition (NER) in "
    "spätmittelalterlichen Verwaltungsdokumenten (14.–16. Jh., Schweiz). "
    "Extrahiere ALLE Entitäten der folgenden acht Typen:\n"
    "- PERSON: benannte Einzelpersonen (z.B. «Hans von Wiler»)\n"
    "- PLACE: Orte, Gewässer, Gebäude (z.B. «Thun», «Aare»)\n"
    "- ORG: Institutionen, Ämter, Zünfte (z.B. «Rat zu Bern», «Spital»)\n"
    "- SOCIAL_GROUP: soziale Kategorien (arme lüt, erbar lüt, Juden, Vaganten…)\n"
    "- CARE_ACTOR: Personen in Fürsorge-/Dienstverhältnissen\n"
    "- CARE_ACTION: Fürsorge-/Dienst-Handlungen (almosen, versorgung…)\n"
    "- ROLE: Ämter/Rolle/Berufe (Vogt, Schultheiss, Ritter…)\n"
    "- DATE: explizite Datumsangaben\n"
    "Antworte ALS REINES JSON: {\"entities\": [...]}. Kein Markdown."
)

# These link to the controlled vocabulary rather than a person/place register.
VOCAB_TYPES = {"SOCIAL_GROUP", "CARE_ACTION", "CARE_ACTOR", "ROLE"}


def extract_entities(doc_id: str, transcription: str) -> dict:
    """Führt Entity Extraction für ein Dokument durch."""
    logger.info(f"[Agent C] Extrahiere Entitäten: {doc_id}")
    raw_entities = _extract_llm(transcription)
    enriched = _enrich(raw_entities)
    _save(doc_id, enriched, transcription)
    count = len(enriched.get("entities", []))
    logger.info(f"[Agent C] Fertig: {doc_id} ({count} Entitäten)")
    return enriched


def _chunk_text(text: str, chunk_size: int = 25_000, overlap: int = 2000) -> list[str]:
    """
    Split text into overlapping chunks of chunk_size chars.
    Overlap ensures entities at boundaries are not lost.
    """
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunk = text[start:start + chunk_size]
        chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def _strip_code_fences(raw: str) -> str:
    """Remove leading/trailing ``` / ```json fences without eating payload.

    str.strip("```json") treats its argument as a CHARACTER SET, so it also
    swallows legitimate leading/trailing j/s/o/n characters of the payload.
    """
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_llm(transcription: str) -> dict:
    """
    Extract entities from the full transcription (all chunks), not a truncated
    prefix.  Each chunk is processed separately and entities are merged.
    """
    all_entities = []
    chunks = _chunk_text(transcription)
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        offset_info = (
            f"[Hinweis: Teil {i+1}/{total}]\n\n"
            if total > 1 else ""
        )
        prompt = (
            SYSTEM + "\n\n" +
            offset_info +
            "Extrahiere alle Entitäten aus diesem Text:\n\n" + chunk + "\n\n"
            "Antworte als JSON: {\"entities\": ["
            "{\"text\": str, \"type\": str, \"normalised\": str, \"context\": str}"
            "]}. "
            "Sei grosszügig — extrahiere auch unsichere Kandidaten."
        )
        try:
            raw = gs.chat_text(prompt, system=None, max_tokens=8000)
            cleaned = _strip_code_fences(raw)
            data = json.loads(cleaned)
            all_entities.extend(data.get("entities", []))
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(f"[Agent C] Extraktion fehlgeschlagen (chunk {i+1}): {e}")

    # Deduplicate by (text, type) — last occurrence wins for duplicates
    seen: dict[tuple, dict] = {}
    for ent in all_entities:
        key = (ent.get("text", ""), ent.get("type", ""))
        if key not in seen or ent.get("context"):
            seen[key] = ent

    return {"entities": list(seen.values())}



def _enrich(extracted: dict) -> dict:
    for ent in extracted.get("entities", []):
        ent_type = ent.get("type", "")
        text = (ent.get("normalised") or ent.get("text") or "").strip()
        if not text:
            continue

        # PERSON / PLACE — use hub, then embedding, then HLS-DHS
        if ent_type in ("PERSON", "PLACE"):
            _link_entity(ent, text, ent_type)
        # SOCIAL_GROUP / CARE_* / ROLE — controlled vocabulary
        elif ent_type in VOCAB_TYPES:
            term = hub.match_vocabulary(text)
            if term:
                ent["controlled_vocab"] = term
                ent["hub_confidence"] = "medium"
                ent["link_method"] = "controlled_vocab"

    return extracted


def _conf_rank(conf) -> int:
    """Map confidence label to sort rank (higher = better)."""
    return {"high": 4, "medium": 3, "low": 2, "unverified": 1}.get(conf, 0)


def _mcp_link(text: str) -> dict | None:
    """Link a PERSON via the MCP federation (KH-6, #92) — the primary authority.

    Queries all Knowledge-Hub sources, resolves cross-source matches, and links
    to the top entity when its name genuinely matches (guarding against a
    spurious top hit). Network/federation failures degrade to None so the local
    hub chain still runs. Offline (tests): mock ``search_agent.search_sync``.
    """
    if not getattr(config, "ENABLE_MCP_LINKING", True):
        return None
    try:
        from agents import search_agent
        from utils import entity_resolver
        resp = search_agent.search_sync(text, limit=10)
    except Exception as e:
        logger.warning(f"[Agent C] MCP link failed for '{text}': {e}")
        return None
    if not resp.entities:
        return None
    top = resp.entities[0]
    # Guard: require a real name match (query tokens ⊆ hit tokens or vice versa).
    q = set(entity_resolver._norm_name(text).split())
    n = set(entity_resolver._norm_name(top.name).split())
    if not q or not (q <= n or n <= q):
        return None
    return {
        "hub_id": top.gnd_id or (str(top.hls_id) if top.hls_id else top.name),
        "gnd": top.gnd_id or "",
        "hls": top.hls_id or "",
        "wikidata": top.wikidata_id or "",
        "hub_confidence": top.confidence,      # high | medium | low
        "link_method": "mcp_federation",
        "mcp_sources": top.sources,
    }


def _link_entity(ent: dict, text: str, ent_type: str) -> None:
    """Link PERSON/PLACE: MCP federation → hub exact → embedding+rerank → HLS-DHS."""
    # 0. MCP federation (primary authority for PERSON — KH-6/#92)
    if ent_type == "PERSON":
        fed = _mcp_link(text)
        if fed:
            ent.update(fed)
            return

    # 1. Hub exact match
    if ent_type == "PERSON":
        match = hub.find_person(text)
    elif ent_type == "PLACE":
        match = hub.find_place(text)
    else:
        match = None

    if match:
        ent["hub_id"] = match.get("id")
        ent["wikidata"] = match.get("wikidata", "")
        ent["gnd"] = match.get("gnd", "")
        ent["hls"] = match.get("hls", "")
        ent["hub_confidence"] = "high"
        ent["link_method"] = "hub_exact"
        return

    # 2. Semantic search via embedding + reranker (AH-43)
    semantic = _semantic_link(text, ent_type)
    if semantic:
        ent.update(semantic)
        ent["link_method"] = "embedding_rerank"
        return

    # 3. HLS-DHS live lookup (AH-39)
    hls = _hls_lookup(text, ent_type)
    if hls:
        ent["hls"] = hls["hls_id"]
        ent["hls_name"] = hls.get("name", "")
        ent["hls_url"] = f"https://www.hls-dhs-dss.ch/de/{hls['hls_id']}"
        ent["hub_confidence"] = "low"
        ent["link_method"] = "hls_dhs"
        return

    ent["hub_confidence"] = "unverified"
    ent["link_method"] = "none"


def _semantic_link(text: str, ent_type: str) -> dict | None:
    """
    Use embedding + reranker to find best hub match for entity name.
    Only for PERSON + PLACE. Requires score > 0.3 to be valid.
    """
    # Collect hub candidates
    if ent_type == "PERSON":
        candidates = [p["name"] for p in hub.get_hub().get_persons()]
    elif ent_type == "PLACE":
        candidates = [p["name"] for p in hub.get_hub().get_places()]
    else:
        return None

    if not candidates:
        return None

    try:
        top = gs.rerank(query=text, documents=candidates, top_n=3)
        if not top or top[0].get("score", 0) < 0.3:
            return None

        best = top[0]
        matched_name = best["document"]

        # Resolve to full dict
        if ent_type == "PERSON":
            obj = hub.find_person(matched_name)
        else:
            obj = hub.find_place(matched_name)

        if obj:
            return {
                "hub_id": obj.get("id", matched_name),
                "wikidata": obj.get("wikidata", ""),
                "gnd": obj.get("gnd", ""),
                "hls": obj.get("hls", ""),
                # hub_confidence is a qualitative label everywhere (high/medium/
                # low/unverified); keep the raw reranker score separately.
                "hub_confidence": "medium",
                "semantic_score": round(best["score"], 3),
            }
    except Exception as e:
        logger.warning(f"[Agent C] Semantic link failed for '{text}': {e}")
    return None


def _hls_lookup(text: str, ent_type: str) -> dict | None:
    """HLS-DHS live search — returns {hls_id, name} or None."""
    try:
        if ent_type == "PERSON":
            results = hub.hls_search_person(text)
        elif ent_type == "PLACE":
            results = hub.hls_search_place(text)
        else:
            return None
        return results[0] if results else None
    except Exception as e:
        logger.warning(f"[Agent C] HLS lookup failed for '{text}': {e}")
        return None


# ── Persistence ───────────────────────────────────────────────────────────────

def _save(doc_id: str, result: dict, transcription: str) -> None:
    json_path = config.OUTPUTS_DIR / f"{doc_id}_entities.json"
    md_path = config.OUTPUTS_DIR / f"{doc_id}_entities.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    lines = [f"# Entitäten: {doc_id}\n"]
    by_type: dict = {}
    for ent in result.get("entities", []):
        by_type.setdefault(ent.get("type", "UNKNOWN"), []).append(ent)

    for etype, ents in by_type.items():
        lines.append(f"## {etype}\n")
        # Most-confident links first (high → unverified)
        ents = sorted(ents, key=lambda e: _conf_rank(e.get("hub_confidence")), reverse=True)
        for e in ents:
            wiki = (f" [Wikidata](https://www.wikidata.org/entity/{e.get('wikidata','')})"
                    if e.get("wikidata") else "")
            gnd = (f" [GND](https://d-nb.info/gnd/{e.get('gnd','')})"
                   if e.get("gnd") else "")
            hls = (f" [HLS]({e.get('hls_url','')})"
                   if e.get("hls_url") else "")
            method = e.get("link_method", "?")
            conf = e.get("hub_confidence", "unverified")
            lines.append(
                f"- **{e.get('text','')}** ({e.get('normalised','')}) — "
                f"{e.get('context','')}{wiki}{gnd}{hls}"
                f" _(conf={conf}, {method})_"
            )
        lines.append("")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"[Agent C] Gespeichert: {json_path}, {md_path}")