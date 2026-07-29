"""
knowledge_hub/rdf_export.py

Exports the knowledge hub to RDF/Turtle using CIDOC-CRM (v7.1) ontology.
This is the first step toward the QLEVER triple-store target (WP4).

CIDOC-CRM classes used:
  - E21 Person
  - E53 Place
  - E40 Legal Body (for organisations)
  - E74 Group (for social groups)
  - E7 Activity (for care actions)
  - E82 Actor Appellation (names)
  - E48 Place Name
  - P1 is identified by (E1 Entity → E41 Appellation)
  - P74 has current or former residence (E53 Place)

Linked data:
  - Wikidata via wdt: predicates (Wikidata entity URIs)
  - GND via GND URI (https://d-nb.info/gnd/)
  - HLS via HLS URI (https://www.hls-dhs-dss.ch/)

Usage:
  graph = hub.to_rdf()          # add hub entities to a new graph
  graph.serialize(format="turtle", destination="hub.ttl")
"""

import json
import re
import unicodedata
from pathlib import Path
from typing import Optional
import rdflib
from rdflib import Namespace, URIRef, Literal, Graph, RDF, RDFS

# ── Namespaces ────────────────────────────────────────────────────────────────
CRM = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
SDHSS = Namespace("https://sdhss.org/ontology/")
WD = Namespace("https://www.wikidata.org/entity/")        # entity (resolve)
WDT = Namespace("https://www.wikidata.org/prop/direct/")  # direct claim
GND = Namespace("https://d-nb.info/gnd/")
HLS = Namespace("https://www.hls-dhs-dss.ch/articles/")

GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
SCHEMA = Namespace("https://schema.org/")
OWL = Namespace("http://www.w3.org/2002/07/owl#")

# Our own namespace. The corpus graph's subject is OUR corpus, so document,
# reading and entity nodes are minted here — never in an authority's namespace
# (KG-2, #328). Authority ids are attached with owl:sameAs instead.
CORPUS_BASE = "https://tei.dh.unibe.ch/id/"
AH = Namespace(CORPUS_BASE)

# ── Prefixes for Turtle output ────────────────────────────────────────────────
PREFIXES = {
    "cidoc-crm": str(CRM),
    "sdhss": str(SDHSS),
    "wd": str(WD),
    "wdt": str(WDT),
    "gnd": str(GND),
    "hls": str(HLS),
    "geo": str(GEO),
    "schema": str(SCHEMA),
    "owl": str(OWL),
    "ah": str(AH),
    "rdf": str(RDF),
    "rdfs": str(RDFS),
}


def _prefix_graph(g: Graph) -> Graph:
    """Register standard prefixes on a graph."""
    for prefix, uri in PREFIXES.items():
        g.bind(prefix, URIRef(uri))
    return g


# ── Entity URI factories ──────────────────────────────────────────────────────

def _person_uri(wikidata_id: Optional[str]) -> Optional[URIRef]:
    if wikidata_id:
        return WD[wikipedia_id_to_qid(wikidata_id)]
    return None


def wikipedia_id_to_qid(wikidata_id: str) -> str:
    """Normalise Wikidata ID: 'Q12345' or '12345' → 'Q12345'."""
    if wikidata_id.startswith("Q"):
        return wikidata_id
    return f"Q{wikidata_id}"


def _safe_literal(value: str) -> Literal:
    return Literal(value, lang="de")


# ── Core export ───────────────────────────────────────────────────────────────

def person_to_rdf(g: Graph, person) -> Graph:
    """
    Add a Person entity as CRM E21 Person.
    Maps: name, wikidata_id, gnd_id, notes.
    """
    if not person.name:
        return g

    # Create or reuse URI
    if person.wikidata_id:
        uri = WD[wikipedia_id_to_qid(person.wikidata_id)]
    else:
        # Blank node for persons without Wikidata ID
        import uuid
        uri = rdflib.BNode(f"person_{uuid.uuid4().hex[:8]}")

    # CRM E21 Person
    g.add((uri, RDF.type, CRM["E21_Person"]))
    g.add((uri, RDFS.label, _safe_literal(person.name)))

    # CRM E82 Actor Appellation — the name
    name_node = rdflib.BNode()
    g.add((uri, CRM["P1_is_identified_by"], name_node))
    g.add((name_node, RDF.type, CRM["E82_Actor_Appellation"]))
    g.add((name_node, RDFS.label, _safe_literal(person.name)))

    # Wikidata
    if person.wikidata_id:
        qid = wikipedia_id_to_qid(person.wikidata_id)
        g.add((uri, RDF.type, WD[qid]))          # typed as Wikidata item
        g.add((uri, SCHEMA["sameAs"], WD[qid]))

    # GND
    if person.gnd_id:
        gnd_id = person.gnd_id.replace("gnd:", "").strip()
        g.add((uri, SCHEMA["sameAs"], GND[gnd_id]))

    # Notes
    if person.notes:
        g.add((uri, RDFS.comment, _safe_literal(person.notes)))

    return g


def place_to_rdf(g: Graph, place) -> Graph:
    """
    Add a Place entity as CRM E53 Place.
    Maps: name, wikidata_id, gnd_id, coordinates, notes.
    """
    if not place.name:
        return g

    if place.wikidata_id:
        uri = WD[wikipedia_id_to_qid(place.wikidata_id)]
    else:
        import uuid
        uri = rdflib.BNode(f"place_{uuid.uuid4().hex[:8]}")

    # CRM E53 Place
    g.add((uri, RDF.type, CRM["E53_Place"]))
    g.add((uri, RDFS.label, _safe_literal(place.name)))

    # Place name (CRM E48)
    name_node = rdflib.BNode()
    g.add((uri, CRM["P1_is_identified_by"], name_node))
    g.add((name_node, RDF.type, CRM["E48_Place_Name"]))
    g.add((name_node, RDFS.label, _safe_literal(place.name)))

    # Wikidata
    if place.wikidata_id:
        qid = wikipedia_id_to_qid(place.wikidata_id)
        g.add((uri, RDF.type, WD[qid]))

    # GND
    if place.gnd_id:
        gnd_id = place.gnd_id.replace("gnd:", "").strip()
        g.add((uri, SCHEMA["sameAs"], GND[gnd_id]))

    # Coordinates (geo:wgs84_pos)
    if place.coordinates:
        lat, lon = place.coordinates
        g.add((uri, GEO["lat"], Literal(lat)))
        g.add((uri, GEO["long"], Literal(lon)))

    # Notes
    if place.notes:
        g.add((uri, RDFS.comment, _safe_literal(place.notes)))

    return g


def organisation_to_rdf(g: Graph, name: str, wikidata_id: Optional[str] = None,
                         gnd_id: Optional[str] = None, notes: str = "") -> Graph:
    """
    Add an Organisation as CRM E40 Legal Body.
    Pass an org dict from the vocabulary list.
    """
    if not name:
        return g

    if wikidata_id:
        uri = WD[wikipedia_id_to_qid(wikidata_id)]
    else:
        import uuid
        uri = rdflib.BNode(f"org_{uuid.uuid4().hex[:8]}")

    g.add((uri, RDF.type, CRM["E40_Legal_Body"]))
    g.add((uri, RDFS.label, _safe_literal(name)))

    if wikidata_id:
        qid = wikipedia_id_to_qid(wikidata_id)
        g.add((uri, RDF.type, WD[qid]))
    if gnd_id:
        gnd_clean = gnd_id.replace("gnd:", "").strip()
        g.add((uri, SCHEMA["sameAs"], GND[gnd_clean]))
    if notes:
        g.add((uri, RDFS.comment, _safe_literal(notes)))

    return g


def social_group_to_rdf(g: Graph, name: str, wikidata_id: Optional[str] = None,
                         notes: str = "") -> Graph:
    """
    Add a Social Group as CRM E74 Group.
    SDHSS defines custom subclasses but CRM E74 Group is the parent.
    """
    if not name:
        return g

    if wikidata_id:
        uri = WD[wikipedia_id_to_qid(wikidata_id)]
    else:
        import uuid
        uri = rdflib.BNode(f"group_{uuid.uuid4().hex[:8]}")

    g.add((uri, RDF.type, CRM["E74_Group"]))
    g.add((uri, RDFS.label, _safe_literal(name)))

    if wikidata_id:
        qid = wikipedia_id_to_qid(wikidata_id)
        g.add((uri, RDF.type, WD[qid]))
    if notes:
        g.add((uri, RDFS.comment, _safe_literal(notes)))

    return g


def care_action_to_rdf(g: Graph, action_name: str, notes: str = "") -> Graph:
    """
    Add a Care Action as CRM E7 Activity.
    This is a first approximation; the SDHSS ontology defines
    specific care action subclasses (TBD in future iteration).
    """
    import uuid
    uri = rdflib.BNode(f"careaction_{uuid.uuid4().hex[:8]}")
    g.add((uri, RDF.type, CRM["E7_Activity"]))
    g.add((uri, RDFS.label, _safe_literal(action_name)))
    if notes:
        g.add((uri, RDFS.comment, _safe_literal(notes)))
    return g


def vocabulary_term_to_rdf(g: Graph, vocab) -> Graph:
    """
    Add a Vocabulary entry as SDHSS concept.
    """
    import uuid
    uri = rdflib.BNode(f"vocab_{uuid.uuid4().hex[:8]}")
    g.add((uri, RDF.type, SDHSS["Concept"]))
    g.add((uri, RDFS.label, _safe_literal(vocab.term)))
    g.add((uri, SDHSS["canonicalForm"], _safe_literal(vocab.canonical_form)))
    g.add((uri, SDHSS["category"], _safe_literal(vocab.category)))
    if vocab.notes:
        g.add((uri, RDFS.comment, _safe_literal(vocab.notes)))
    return g


def closest_reading_to_rdf(g: Graph, document_uri, closest_reading: dict) -> Graph:
    """Attach a revisable editorial reading and its provenance to a document.

    The local terms deliberately say ``closestReading`` rather than implying a
    scholarly reference text or independently verified transcription.
    """
    reading = rdflib.BNode()
    g.add((reading, RDF.type, SDHSS["ClosestReading"]))
    g.add((reading, RDFS.label, Literal("Closest available reading")))
    g.add((reading, RDF.value, _safe_literal(closest_reading.get("text", ""))))
    g.add((reading, SDHSS["editorialStatus"],
           Literal("revisable editorial choice; not a reference text")))
    for candidate in closest_reading.get("candidates_offered", {}):
        g.add((reading, SDHSS["candidateOffered"], _safe_literal(candidate)))
    for chosen in closest_reading.get("chosen", []):
        g.add((reading, SDHSS["candidateChosen"], _safe_literal(chosen)))
    g.add((reading, SDHSS["combined"],
           Literal(bool(closest_reading.get("combined")))))
    if closest_reading.get("editor_pseudonym"):
        g.add((reading, SDHSS["editorPseudonym"],
               _safe_literal(closest_reading["editor_pseudonym"])))
    if closest_reading.get("confirmed_at"):
        g.add((reading, SDHSS["confirmedAt"],
               _safe_literal(closest_reading["confirmed_at"])))
    g.add((URIRef(document_uri), SDHSS["hasClosestReading"], reading))
    return g


# ── Hub-level export ─────────────────────────────────────────────────────────

def hub_to_graph(hub_instance) -> Graph:
    """
    Export an entire KnowledgeHub instance to an RDF graph.
    Returns a pre-fixed Graph with all entities serialized as Turtle.
    """
    g = Graph()
    _prefix_graph(g)

    # Use all_persons()/all_places()/all_vocabulary() (dict-based hub compat)
    for person in hub_instance.all_persons():
        person_to_rdf(g, person)

    for place in hub_instance.all_places():
        place_to_rdf(g, place)

    for vocab in hub_instance.all_vocabulary():
        vocabulary_term_to_rdf(g, vocab)

    return g


def hub_to_turtle(hub_instance, output_path: str = "knowledge_hub/data/hub.ttl") -> str:
    """
    Export hub to Turtle file.
    Returns the path the file was written to.
    """
    g = hub_to_graph(hub_instance)
    g.serialize(destination=output_path, format="turtle")
    return output_path


def entity_to_rdf(hub_instance, entity_dict: dict) -> Graph:
    """
    Export a single entity dict (as returned by Agent C) to RDF.
    Handles all 8 entity types.
    Entity dict shape: {type: str, name: str, wikidata_id: str, ...}
    """
    g = Graph()
    _prefix_graph(g)

    entity_type = entity_dict.get("type", "").upper()
    name = entity_dict.get("name", "")
    wikidata_id = entity_dict.get("wikidata_id")
    gnd_id = entity_dict.get("gnd_id")
    notes = entity_dict.get("notes", "")

    if entity_type == "PERSON":
        person = type("Person", (), entity_dict)()
        return person_to_rdf(g, person)

    elif entity_type == "PLACE":
        place = type("Place", (), entity_dict)()
        return place_to_rdf(g, place)

    elif entity_type == "ORGANISATION":
        return organisation_to_rdf(g, name, wikidata_id, gnd_id, notes)

    elif entity_type == "SOCIAL_GROUP":
        return social_group_to_rdf(g, name, wikidata_id, notes)

    elif entity_type == "CARE_ACTION":
        return care_action_to_rdf(g, name, notes)

    elif entity_type == "ROLE":
        import uuid
        uri = rdflib.BNode(f"role_{uuid.uuid4().hex[:8]}")
        g.add((uri, RDF.type, SDHSS["Role"]))
        g.add((uri, RDFS.label, _safe_literal(name)))
        return g

    elif entity_type == "DATE":
        import uuid
        uri = rdflib.BNode(f"date_{uuid.uuid4().hex[:8]}")
        g.add((uri, RDF.type, CRM["E50_Date"]))
        g.add((uri, RDFS.label, _safe_literal(name)))
        return g

    # Generic fallback: just label it
    import uuid
    uri = rdflib.BNode(f"entity_{uuid.uuid4().hex[:8]}")
    g.add((uri, RDFS.label, _safe_literal(name)))
    return g


# ── Corpus-centric export (KG-2, #328) ───────────────────────────────────────
#
# The hub-level functions above export the *hub*. Everything below exports the
# *corpus*: the documents we processed, the competing readings produced for
# them, and the entities each reading mentions. That is the value we add and
# that no authority database holds.
#
# Three rules drive the model:
#   1. Link, don't copy — authority ids arrive as owl:sameAs, never as mirrored
#      source fields (KG-1, #327).
#   2. A document has MANY readings and none of them is "the text". No triple
#      ever attaches symbolic content directly to a document.
#   3. A mention belongs to the READING it was extracted from, not to the
#      document — a different reading may not contain it at all.
#
# All nodes are minted as URIRefs from stable keys (never uuid4 BNodes), so the
# serialisation is byte-identical across runs and the graph stays diffable.

# CRM class per Agent C entity type.
_ENTITY_CLASS = {
    "PERSON":       "E21_Person",
    "PLACE":        "E53_Place",
    "ORG":          "E40_Legal_Body",
    "ORGANISATION": "E40_Legal_Body",
    "SOCIAL_GROUP": "E74_Group",
    "CARE_ACTOR":   "E21_Person",
    "CARE_ACTION":  "E7_Activity",
    "ROLE":         "E55_Type",
    "DATE":         "E52_Time-Span",
}

# Authority id fields carrying a PUBLIC URI. The URI itself comes from
# entity_index.authority_uri (KG-1, #327) rather than a second table here: the
# legacy hub namespaces above disagree with it (`www.hls-dhs-dss.ch/articles/`
# vs `hls-dhs-dss.ch/de/articles/`, `/entity/` vs `/wiki/`), and a graph whose
# owl:sameAs URIs did not match the ids KG-1 persists would not join up.
_AUTHORITY_FIELDS = ("gnd", "wikidata", "hls")


def _authority_uri(source: str, value: str) -> Optional[str]:
    """Public URI for an authority id — the same table KG-1 persists."""
    from entity_index import authority_uri
    return authority_uri(source, str(value))


def _slug(value: str) -> str:
    """Stable, URI-safe slug (umlauts transliterated, as in entity_index.py)."""
    s = (value or "").translate(str.maketrans(
        {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
         "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")


def document_uri(doc_id: str, ns: Namespace = AH) -> URIRef:
    return ns[f"document/{_slug(doc_id)}"]


def reading_uri(doc_id: str, key: str, ns: Namespace = AH) -> URIRef:
    return ns[f"reading/{_slug(doc_id)}/{_slug(key)}"]


def entity_uri(etype: str, name: str, ids: Optional[dict] = None,
               ns: Namespace = AH) -> URIRef:
    """Mint OUR URI for an entity.

    Keyed on the strongest authority id available so the node is stable across
    documents and spelling variants; falls back to the normalised name. The URI
    is always in our namespace — the authority id is attached with owl:sameAs,
    not used as the node's identity.
    """
    ids = ids or {}
    for field in ("gnd", "wikidata", "hls"):
        if ids.get(field):
            local = f"{field}-{_slug(str(ids[field]))}"
            break
    else:
        local = _slug(name)
    return ns[f"{_slug(etype) or 'entity'}/{local}"]


def _identifier_node(source: str, value: str, ns: Namespace = AH) -> URIRef:
    return ns[f"identifier/{_slug(source)}/{_slug(str(value))}"]


def _add_identifier(g: Graph, subject: URIRef, source: str, value: str,
                    ns: Namespace = AH) -> None:
    """Attach a source-local id as an E42 Identifier carrying a literal.

    Used for sources WITHOUT a public URI (hbls / kf / eos / hub). The id is
    preserved verbatim as a literal so it stays resolvable by a human or a
    later MCP call — but no external URI is invented for it.
    """
    node = _identifier_node(source, value, ns)
    g.add((subject, CRM["P1_is_identified_by"], node))
    g.add((node, RDF.type, CRM["E42_Identifier"]))
    g.add((node, RDFS.label, Literal(str(value))))
    g.add((node, CRM["P2_has_type"], Literal(source)))


def entity_node_to_rdf(g: Graph, entity: dict, ns: Namespace = AH) -> Optional[URIRef]:
    """Add one extracted entity as a typed CRM node and return its URI.

    Authority ids become ``owl:sameAs`` links to public URIs; ids from sources
    without a public URI (hbls / kf / eos / the local hub) become E42
    Identifiers carrying the literal id. No source payload is copied.
    """
    name = (entity.get("normalised") or entity.get("text") or "").strip()
    etype = (entity.get("type") or "").strip().upper()
    if not name or not etype:
        return None

    ids = {k: (entity.get(k) or "").strip() if isinstance(entity.get(k), str)
              else entity.get(k)
           for k in ("gnd", "wikidata", "hls", "hub_id")}
    uri = entity_uri(etype, name, ids, ns)

    g.add((uri, RDF.type, CRM[_ENTITY_CLASS.get(etype, "E1_CRM_Entity")]))
    g.add((uri, RDFS.label, _safe_literal(name)))
    g.add((uri, SDHSS["entityType"], Literal(etype)))

    # Public authority URIs → owl:sameAs (link, don't copy). A field with no
    # public URI pattern falls through to a local identifier below rather than
    # getting a fabricated one.
    for field in _AUTHORITY_FIELDS:
        value = ids.get(field)
        if not value:
            continue
        public = _authority_uri(field, value)
        if public:
            g.add((uri, OWL["sameAs"], URIRef(public)))
        else:
            _add_identifier(g, uri, field, value, ns)

    # Sources without a public URI keep a local identifier only.
    if ids.get("hub_id"):
        _add_identifier(g, uri, "hub", ids["hub_id"], ns)

    return uri


def _readings_of(doc: dict) -> list[tuple[str, str, dict]]:
    """Extract the competing readings of one pipeline record.

    Returns ``(key, text, meta)`` triples. Every per-engine recognition is a
    reading; the fused/working transcription is ALSO just a reading — it is the
    one Agent C actually read, so it carries the mentions, but it is never
    asserted as the document's text.
    """
    out: list[tuple[str, str, dict]] = []
    seen: set[str] = set()

    for i, rec in enumerate(doc.get("recognitions") or []):
        text = (rec.get("text") or "").strip()
        if not text or rec.get("error"):
            continue
        key = _slug(rec.get("engine") or "") or f"engine-{i}"
        if key in seen:                      # same engine twice → keep both
            key = f"{key}-{i}"
        seen.add(key)
        out.append((key, text, {
            "engine": rec.get("engine") or "",
            "model_id": rec.get("model_id") or "",
            "confidence": rec.get("confidence"),
        }))

    working = (doc.get("transcription") or "").strip()
    if working:
        key = "working"
        if key in seen:
            key = "working-fused"
        out.append((key, working, {
            "engine": (doc.get("a_meta") or {}).get("source") or "pipeline",
            "model_id": "",
            "confidence": (doc.get("a_meta") or {}).get("qa_score"),
            "working": True,
        }))
    return out


def document_to_rdf(g: Graph, doc: dict, ns: Namespace = AH) -> Optional[URIRef]:
    """Add one processed document, its readings, and their mentions."""
    doc_id = (doc.get("doc_id") or "").strip()
    if not doc_id:
        return None

    d_uri = document_uri(doc_id, ns)
    g.add((d_uri, RDF.type, CRM["E22_Human-Made_Object"]))
    g.add((d_uri, RDF.type, CRM["E31_Document"]))
    g.add((d_uri, RDFS.label, Literal(doc_id)))
    _add_identifier(g, d_uri, "archival_id", doc_id, ns)

    # #208 — the source image, when the deployment knows where it lives.
    if doc.get("source_url"):
        g.add((d_uri, SCHEMA["image"], URIRef(str(doc["source_url"]))))

    readings = _readings_of(doc)
    working_uri: Optional[URIRef] = None

    for key, text, meta in readings:
        r_uri = reading_uri(doc_id, key, ns)
        # P128 carries: the document carries the linguistic object. Note what is
        # NOT here — no symbolic content is ever attached to `d_uri` itself.
        g.add((d_uri, CRM["P128_carries"], r_uri))
        g.add((r_uri, RDF.type, CRM["E33_Linguistic_Object"]))
        g.add((r_uri, RDFS.label, Literal(f"Reading ({meta.get('engine') or key})")))
        g.add((r_uri, CRM["P190_has_symbolic_content"], _safe_literal(text)))
        if meta.get("engine"):
            g.add((r_uri, SDHSS["engine"], Literal(meta["engine"])))
        if meta.get("model_id"):
            g.add((r_uri, SDHSS["modelId"], Literal(meta["model_id"])))
        if meta.get("confidence") is not None:
            g.add((r_uri, SDHSS["confidence"], Literal(meta["confidence"])))
        if meta.get("working"):
            # The reading Agent C read. A working choice among candidates, not
            # an established text — full attribution is KG-3 (#329).
            g.add((r_uri, SDHSS["readingRole"], Literal("working")))
            working_uri = r_uri

    # Mentions belong to the reading they were extracted from. Agent C ran on
    # the working reading, so only that reading refers to the entities; claiming
    # the per-engine readings mention them would be a fabrication.
    entities = ((doc.get("entities") or {}).get("entities")
                if isinstance(doc.get("entities"), dict)
                else doc.get("entities")) or []
    if working_uri is not None:
        for ent in entities:
            e_uri = entity_node_to_rdf(g, ent, ns)
            if e_uri is not None:
                g.add((working_uri, CRM["P67_refers_to"], e_uri))

    return d_uri


def corpus_to_graph(outputs_dir: str | Path, ns: Namespace = AH) -> Graph:
    """Build the corpus graph from ``data/outputs/*_pipeline.json``.

    Pipeline records are the only complete unit: they carry the readings AND the
    entities extracted from them. A bare ``*_entities.json`` has no reading to
    anchor its mentions to, and inventing one would break the model's central
    honesty rule, so those files are skipped.
    """
    g = Graph()
    _prefix_graph(g)
    for path in sorted(Path(outputs_dir).rglob("*_pipeline.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(doc, dict):
            document_to_rdf(g, doc, ns)
    return g


def corpus_to_turtle(outputs_dir: str | Path,
                     destination: str | Path = "corpus.ttl",
                     ns: Namespace = AH) -> str:
    """Serialise the corpus graph to Turtle. Deterministic and re-runnable."""
    g = corpus_to_graph(outputs_dir, ns)
    data = g.serialize(format="turtle")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(data, encoding="utf-8")
    return str(destination)
