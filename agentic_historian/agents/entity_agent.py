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
from typing import NamedTuple, Optional

from loguru import logger

import config
import passage_index
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


def bestand_from_doc_id(doc_id: str, sep: str = "__") -> Optional[str]:
    """The holding a `doc_id` names, by the batch runner's convention.

    `PageRef.key` folds a page's path relative to the corpus root on ``__``, so
    ``Marbach__letter-0001__001`` begins with its holding. A caller that knows
    its ids follow that shape can use this; the store applies **no** such rule
    of its own, because `ingest.py` and the batch path mint ids differently and
    a convention the store enforced would turn the first exception into wrong
    data rather than a missing value (#396).

    None when there is no leading component to take — one segment is a document
    with no holding above it, not a holding.
    """
    parts = [p for p in (doc_id or "").split(sep) if p]
    return parts[0] if len(parts) > 1 else None


def passage_records(doc_id: str, entities: list[dict],
                    *, page: int = 1,
                    bestand: Optional[str] = None) -> list[dict]:
    """Index rows for the vocabulary-typed occurrences of one document (#467).

    Two filters, and both are the point.

    **Only `VOCAB_TYPES`.** `PERSON`, `PLACE`, `ORG` and `DATE` do not belong in
    the passage index: it exists to find care and taxonomy passages (#384), and
    those four types link to a person or place register instead. A first attempt
    wrote every type and then pinned that in a test.

    **Only located occurrences.** `char_start` is `NOT NULL` in the store, so an
    entity whose text does not stand verbatim in the transcription (#466) has no
    row to write. It stays in `{doc_id}_entities.json`; it simply is not a
    passage, because nobody can point at where it is.

    Builds **new** dicts and never touches the entities it reads. The same dicts
    are serialised to `{doc_id}_entities.json`, so an internal key set on them
    here — a first attempt used `_doc_id` and `_page` — ends up in the published
    file format.

    `hub_id` carries the controlled-vocabulary term `hub.match_vocabulary`
    resolved, which within that vocabulary is what identifies the entry; it is
    what makes `ix_passages_hub_id` mean anything. None where the mention
    matched no vocabulary entry, which is the ordinary case for a term nobody
    has added yet and not a defect.
    """
    rows: list[dict] = []
    for ent in entities:
        if ent.get("type") not in VOCAB_TYPES:
            continue
        if ent.get("char_start") is None or ent.get("char_end") is None:
            continue
        text = ent.get("text") or ""
        rows.append({
            "doc_id": doc_id,
            "page": page,
            # None, not a guess: a holding nobody named is absent (#396). See
            # `bestand_from_doc_id` for the convention a caller may opt into.
            "bestand": bestand,
            "entity_type": ent["type"],
            "text": text,
            # The store requires one; the model does not always give one.
            "normalised": ent.get("normalised") or text,
            "char_start": ent["char_start"],
            "char_end": ent["char_end"],
            "context": ent.get("context", ""),
            "hub_id": ent.get("controlled_vocab"),
            "gnd_id": ent.get("gnd"),
            "hls_id": ent.get("hls_id"),
        })
    return rows


def _index_passages(doc_id: str, enriched: dict, *, page: int = 1,
                    bestand: Optional[str] = None) -> int:
    """Write this document's passages. Returns the row count.

    Its own step rather than a line inside `_save`, which writes two files and
    is named for it. `upsert_passages` **deletes** the document's existing rows
    before inserting, and a deletion hidden in a function nobody reads for
    deletions is how a re-run quietly loses passages (#467).

    Never fatal, on the same terms as the run manifest: an index that cannot be
    written must not cost a document whose entities were extracted. It is logged
    at warning, because a silent failure here would leave the corpus searchable
    and incomplete with nothing to show for it.
    """
    rows = passage_records(doc_id, enriched.get("entities", []), page=page,
                           bestand=bestand)
    try:
        written = passage_index.upsert_passages(doc_id, rows)
    except Exception as e:                      # noqa: BLE001 — see docstring
        logger.warning(f"[Agent C] Passagen-Index für {doc_id} nicht "
                       f"geschrieben: {e}")
        return 0
    if rows:
        logger.info(f"[Agent C] {written} Passage(n) indiziert: {doc_id}")
    return written


def extract_entities(doc_id: str, transcription: str, page: int = 1,
                     bestand: Optional[str] = None) -> dict:
    """Führt Entity Extraction für ein Dokument durch.

    ``page`` is the page these offsets are into. It defaults to 1 because a
    `doc_id` carries exactly one transcription today, and the store's column is
    `NOT NULL`; the parameter exists so a caller that splits a document into
    pages can say which one rather than having every row claim page 1.

    ``bestand`` is the holding, and it is a parameter rather than something
    derived here: the caller knows, and a rule applied in the store would turn
    the first doc_id that does not follow the convention into wrong data
    instead of a missing value. `bestand_from_doc_id` is that convention, for a
    caller that wants to opt into it.
    """
    logger.info(f"[Agent C] Extrahiere Entitäten: {doc_id}")
    raw_entities = _extract_llm(transcription)
    enriched = _enrich(raw_entities)
    _save(doc_id, enriched, transcription)
    _index_passages(doc_id, enriched, page=page, bestand=bestand)
    count = len(enriched.get("entities", []))
    logger.info(f"[Agent C] Fertig: {doc_id} ({count} Entitäten)")
    return enriched


#: The chunking rule, in one place (#466). It used to be a default argument
#: here and a literal `len(chunk) - 2000` at the call site that needed an
#: offset — the coupling where changing the chunker makes every offset in the
#: database silently wrong. Nothing recomputes a chunk's position any more:
#: `_chunk_text` reports it.
CHUNK_SIZE = 25_000
CHUNK_OVERLAP = 2_000


class Chunk(NamedTuple):
    """One piece of a transcription, and where it begins in that transcription.

    `start` is what makes an offset expressible at all. A plain list of strings
    cannot say where a hit was found, which is why #395 could not be built on
    top of it three times running.
    """

    start: int
    text: str

    @property
    def end(self) -> int:
        return self.start + len(self.text)


def _chunk_text(text: str, chunk_size: Optional[int] = None,
                overlap: Optional[int] = None) -> list[Chunk]:
    """Split text into overlapping chunks, each carrying its start offset.

    The overlap exists so an entity sitting on a boundary is not lost. It has a
    cost that only shows up once offsets are kept: a hit inside the overlap is
    seen by two chunks and must still come out **once**. That is resolved by
    identity, not by arithmetic — two sightings of the same characters map to
    the same absolute span, so they collapse on their own (see `_locate`).
    """
    chunk_size = CHUNK_SIZE if chunk_size is None else chunk_size
    overlap = CHUNK_OVERLAP if overlap is None else overlap
    if len(text) <= chunk_size:
        return [Chunk(0, text)]
    chunks: list[Chunk] = []
    start = 0
    while start < len(text):
        chunks.append(Chunk(start, text[start:start + chunk_size]))
        start += chunk_size - overlap
    return chunks


def occurrences_in(haystack: str, needle: str) -> list[int]:
    """Every start position at which `needle` occurs verbatim, non-overlapping.

    Exact, not case-folded and not normalised. The acceptance for #466 is
    ``transcription[char_start:char_end] == ent["text"]``, and only an exact
    match can satisfy it.

    The first attempt at #395 used ``chunk.lower().find(text.lower())``, which
    fails twice over: a normalised form usually does not appear verbatim in a
    15th-century transcription, so ``find`` returns -1 and the record vanished
    without a word; and where it does appear, ``find`` returns the *first*
    occurrence rather than the one the model was looking at. Returning every
    position replaces a guess with the list of candidates, and a term that
    occurs three times becomes three records instead of one.
    """
    if not needle:
        return []
    found: list[int] = []
    pos = haystack.find(needle)
    while pos != -1:
        found.append(pos)
        pos = haystack.find(needle, pos + len(needle))
    return found


def _locate(ents: list[dict], chunk: Chunk) -> tuple[list[dict], list[dict]]:
    """Expand each entity into one record per occurrence. ``(located, unlocated)``.

    An occurrence record is **two claims of different kinds**, and the split is
    worth knowing when reading one. The *type* is the model's judgement about a
    term it read in this chunk. The *positions* are found mechanically, by
    matching that term's characters. So a record says "this term, typed thus,
    stands here" — not "the model classified this position".

    The search is confined to the chunk the entity came from, deliberately. A
    term the model named while reading chunk 1 says nothing about the same
    characters in chunk 7, which it never saw, and claiming a position there
    would be asserting a judgement nobody made.

    `unlocated` is the entity whose text does not appear verbatim — a normalised
    or silently corrected form. It keeps no offsets, because inventing one is
    how #395's first attempt put wrong spans in a database; and it is returned
    rather than dropped, because it is an entity the extraction found and
    #466's third acceptance point is that today's output survives.
    """
    located: list[dict] = []
    unlocated: list[dict] = []
    for ent in ents:
        text = ent.get("text") or ""
        hits = occurrences_in(chunk.text, text)
        if not hits:
            unlocated.append({**ent, "char_start": None, "char_end": None,
                              "located": False})
            continue
        for local in hits:
            start = chunk.start + local
            located.append({**ent, "char_start": start,
                            "char_end": start + len(text), "located": True})
    return located, unlocated


def _strip_code_fences(raw: str) -> str:
    """Remove leading/trailing ``` / ```json fences without eating payload.

    str.strip("```json") treats its argument as a CHARACTER SET, so it also
    swallows legitimate leading/trailing j/s/o/n characters of the payload.
    """
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


_ENTITY_OBJ_RE = re.compile(r"\{[^{}]*\}")


def _loads_entities(raw: str) -> list[dict]:
    """Best-effort parse of the NER LLM's reply into a list of entity dicts.

    LLMs occasionally emit slightly malformed JSON (a missing/trailing comma,
    prose around the object, or a truncated tail). Rather than drop the whole
    chunk on a single ``json.loads`` failure, try progressively looser
    strategies and finally salvage whatever individual entity objects parse.
    """
    text = _strip_code_fences(raw)
    if not text:
        return []

    candidates = [text]
    # slice to the outermost JSON object (drops any prose around it)
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        candidates.append(text[a:b + 1])
    # drop trailing commas before a closing ] or }
    candidates += [re.sub(r",(\s*[}\]])", r"\1", c) for c in list(candidates)]

    for cand in candidates:
        try:
            data = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("entities"), list):
            return [e for e in data["entities"] if isinstance(e, dict)]
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict)]

    # last resort: collect individual {...} objects that parse and look like
    # entities — salvages a truncated/partly-malformed list.
    salvaged = []
    for m in _ENTITY_OBJ_RE.finditer(text):
        try:
            obj = json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and obj.get("text") and obj.get("type"):
            salvaged.append(obj)
    return salvaged


def _extract_llm(transcription: str) -> dict:
    """
    Extract entities from the full transcription (all chunks), not a truncated
    prefix.  Each chunk is processed separately and entities are merged.
    """
    located_all: list[dict] = []
    unlocated_all: list[dict] = []
    chunks = _chunk_text(transcription)
    total = len(chunks)
    for i, piece in enumerate(chunks):
        chunk = piece.text
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
        except Exception as e:
            logger.warning(f"[Agent C] LLM-Aufruf fehlgeschlagen (chunk {i+1}): {e}")
            continue

        ents = _loads_entities(raw)
        # If nothing parsed from a non-empty reply, retry ONCE — sampling variance
        # alone usually yields parseable JSON, reinforced by an explicit-format nudge.
        if not ents and raw and raw.strip():
            logger.warning(
                f"[Agent C] Chunk {i+1}: JSON nicht parsebar, Retry …")
            try:
                raw = gs.chat_text(
                    prompt + "\n\nWICHTIG: Antworte AUSSCHLIESSLICH mit gültigem "
                    "JSON, kein Text davor oder danach.",
                    system=None, max_tokens=8000)
                ents = _loads_entities(raw)
            except Exception as e:
                logger.warning(f"[Agent C] Retry fehlgeschlagen (chunk {i+1}): {e}")
            if not ents:
                logger.warning(
                    f"[Agent C] Chunk {i+1}: auch nach Retry kein JSON — übersprungen")
        chunk_located, chunk_unlocated = _locate(ents, piece)
        located_all.extend(chunk_located)
        unlocated_all.extend(chunk_unlocated)

    return {"entities": _merge(located_all, unlocated_all)}


def _merge(located: list[dict], unlocated: list[dict]) -> list[dict]:
    """One record per occurrence, and one per entity that has none.

    The old rule deduplicated by ``(text, type)``, which turned a term
    occurring twelve times into a single entry. An index over such entries is
    not a passage index (#466), so the key for a located record is **where it
    stands**: ``(char_start, char_end, type)``.

    That also settles the overlap by identity rather than by arithmetic. A hit
    inside the 2000 characters two chunks share is reported twice, once by each
    chunk, at the same absolute span — so the two collapse to one without
    anybody computing which chunk "owns" the boundary. A rule that worked out
    ownership would be a third copy of the chunking constants.

    Unlocated entities keep the old key, because without a position there is
    nothing else to distinguish them by.

    Within a key, a record carrying `context` beats one without, which is the
    preference the previous rule had and the reason it was not simply
    "last wins".
    """
    seen: dict[tuple, dict] = {}
    for ent in located:
        key = (ent["char_start"], ent["char_end"], ent.get("type", ""))
        if key not in seen or (ent.get("context") and not seen[key].get("context")):
            seen[key] = ent
    for ent in unlocated:
        key = (ent.get("text", ""), ent.get("type", ""))
        if key not in seen or (ent.get("context") and not seen[key].get("context")):
            seen[key] = ent

    if unlocated:
        # Said out loud, because a record without a position cannot enter the
        # passage store (`char_start INTEGER NOT NULL`) and would otherwise
        # leave the corpus one entity short with nothing to show for it.
        kinds = sorted({e.get("type", "?") for e in unlocated})
        logger.info(
            f"[Agent C] {len(unlocated)} Entität(en) ohne wörtliche Fundstelle "
            f"im Text ({', '.join(kinds)}) — behalten, aber ohne Offset")
    return list(seen.values())



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