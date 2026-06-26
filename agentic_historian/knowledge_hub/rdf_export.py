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


# ── Hub-level export ─────────────────────────────────────────────────────────

def hub_to_graph(hub_instance) -> Graph:
    """
    Export an entire KnowledgeHub instance to an RDF graph.
    Returns a pre-fixed Graph with all entities serialized as Turtle.
    """
    g = Graph()
    _prefix_graph(g)

    for person in hub_instance.persons:
        person_to_rdf(g, person)

    for place in hub_instance.places:
        place_to_rdf(g, place)

    for vocab in hub_instance.vocabulary:
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