"""registry.py — die Begriffe, die der Bestandsgraph verwenden darf (KG-6, #650).

Jeder Begriff ist genau eines von dreien:

- ``ontome``   in einem OntoMe-Schnappschuss oder im CIDOC-RDFS nachgewiesen —
               die URI muss dort stehen, sonst scheitert ``test_registry``.
- ``w3c``      ein Begriff aus rdf/rdfs/owl/prov/wgs84, deren Vokabulare OntoMe
               nicht führt und die wir nicht nachbauen.
- ``ours``     in unserem Namensraum erklärt, mit Label, Erläuterung, Domain und
               Range — damit die Prüfung sie behandeln kann wie jeden anderen.

Für den Bestandsgraphen (Dodis, SSRQ, Königsfelden) braucht es **keinen**
eigenen Begriff: alles, was CIDOC fehlt, hat SDHSS. Die Rubrik ``ours`` ist
für das, was später kommt — und für den Umzug der 40 erfundenen ``sdhss:``-
Begriffe des Korpusexporters (KG-12).

Die Mapping-Tabelle in ``docs/KG_BESTANDSGRAPH_MAPPING.md`` sagt je Quellfeld,
welcher Begriff warum; dieses Modul ist ihre maschinenlesbare Seite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from rdflib import Namespace

CRM = Namespace("http://www.cidoc-crm.org/cidoc-crm/")
SDH = Namespace("https://sdhss.org/ontology/core/")
SDH_SLC = Namespace("https://sdhss.org/ontology/social-life-core/")
CRM_SUP = Namespace("https://sdhss.org/ontology/crm-supplement/")
PROV = Namespace("http://www.w3.org/ns/prov#")
OWL = Namespace("http://www.w3.org/2002/07/owl#")
RDF_NS = Namespace("http://www.w3.org/1999/02/22-rdf-syntax-ns#")
RDFS_NS = Namespace("http://www.w3.org/2000/01/rdf-schema#")
GEO = Namespace("http://www.w3.org/2003/01/geo/wgs84_pos#")
XSD = Namespace("http://www.w3.org/2001/XMLSchema#")
#: Unser Namensraum für Begriffe. Nicht ``ah:`` (das sind die Instanzen unter
#: ``/id/``), und nicht ``sdhss:`` — das gehört SDHSS.
SHB = Namespace("https://tei.dh.unibe.ch/ontology/")

PREFIXES = {
    "crm": str(CRM), "sdh": str(SDH), "sdh-slc": str(SDH_SLC), "crm-sup": str(CRM_SUP),
    "prov": str(PROV), "owl": str(OWL), "rdf": str(RDF_NS), "rdfs": str(RDFS_NS),
    "geo": str(GEO), "xsd": str(XSD), "shb": str(SHB),
}

LITERAL = "rdfs:Literal"


@dataclass(frozen=True)
class Term:
    uri: str
    kind: str                 # "class" | "property"
    source: str               # "ontome" | "w3c" | "ours"
    label: str = ""
    note: str = ""            # ours: die Erläuterung; ontome/w3c: warum wir ihn brauchen
    domain: Optional[str] = None   # ours/w3c-Properties: Klassen-URI oder LITERAL
    range: Optional[str] = None


def _c(ns, local, source, note="", label=""):
    return Term(uri=str(ns[local]), kind="class", source=source, note=note, label=label)


def _p(ns, local, source, note="", domain=None, range=None, label=""):
    return Term(uri=str(ns[local]), kind="property", source=source, note=note,
                domain=domain, range=range, label=label)


TERMS: tuple[Term, ...] = (
    # ── Klassen aus CIDOC CRM 7.1.3 ─────────────────────────────────────────
    _c(CRM, "E31_Document", "ontome", "Regest (Dodis), Eintrag (KF), Edition (SSRQ)"),
    _c(CRM, "E21_Person", "ontome"),
    _c(CRM, "E53_Place", "ontome"),
    _c(CRM, "E74_Group", "ontome", "Familien, Orden, Gerichte, Zünfte, Gemeinden; "
       "E40 Legal Body gibt es in 7.1.3 nicht mehr — der Typ hängt als crm-sup:C9 daran"),
    _c(CRM, "E55_Type", "ontome", "Dodis-Themen (P129), Rollen (P177), Namenstypen, Id-Quellen"),
    _c(CRM, "E41_Appellation", "ontome", "Namensvarianten mit Sprache und Typ (std/orig)"),
    _c(CRM, "E42_Identifier", "ontome", "quellenlokale Ids ohne öffentliche URI"),
    _c(CRM, "E65_Creation", "ontome", "Autorschaft bei Dodis: E65 P94 Dokument, P14 Person"),
    _c(CRM, "E67_Birth", "ontome"),
    _c(CRM, "E69_Death", "ontome"),
    _c(CRM, "E52_Time-Span", "ontome"),
    _c(CRM, "E13_Attribute_Assignment", "ontome", "die Rollen signatory/adressee/address_of_copy: "
       "«der Datensatz weist Person P für Dokument D die Rolle R zu» — nicht erfunden, zugewiesen"),
    # ── Klassen aus SDHSS (OntoMe) ───────────────────────────────────────────
    _c(SDH_SLC, "C9", "ontome", "Relationship: Ehe, Verlobung, Partnerschaft — zwei P20-Partner"),
    _c(SDH_SLC, "C10", "ontome", "Type of Persons' Interaction: Ehe / Verlobung / Scheidung …"),
    _c(SDH_SLC, "C3", "ontome", "Social Relationship: Geschwister, Vetter, Nachbar, Vormund, Pate …"),
    _c(SDH_SLC, "C4", "ontome", "Social Relationship Type"),
    _c(SDH_SLC, "C5", "ontome", "Membership: memberOf, citizenOf, Familienzugehörigkeit"),
    _c(SDH_SLC, "C6", "ontome", "Membership Type"),
    _c(SDH_SLC, "C12", "ontome", "Actor's Social Role: ein Beruf/Amt als Rolle"),
    _c(SDH_SLC, "C13", "ontome", "Social Role Embodiment: diese Person in dieser Rolle, mit Zeit"),
    _c(CRM_SUP, "C9", "ontome", "Group Type: Familie, Orden, Gericht … (SSRQ org_type)"),
    # ── Properties aus CIDOC CRM 7.1.3 ──────────────────────────────────────
    _p(CRM, "P1_is_identified_by", "ontome"),
    _p(CRM, "P2_has_type", "ontome", "Range ist E55, **kein Literal** — der Korpusexporter verstösst dagegen (KG-12)"),
    _p(CRM, "P3_has_note", "ontome", "Fundstelle (SSRQ occurence), Bemerkungen"),
    _p(CRM, "P4_has_time-span", "ontome"),
    _p(CRM, "P7_took_place_at", "ontome", "Geburts-/Sterbeort"),
    _p(CRM, "P14_carried_out_by", "ontome"),
    _p(CRM, "P67_refers_to", "ontome", "die Erwähnung: Dokument → Person/Ort/Gruppe; E31 ⊂ E89"),
    _p(CRM, "P74_has_current_or_former_residence", "ontome", "SSRQ residence, KF Wohnort"),
    _p(CRM, "P82a_begin_of_the_begin", "ontome"),
    _p(CRM, "P82b_end_of_the_end", "ontome"),
    _p(CRM, "P94_has_created", "ontome"),
    _p(CRM, "P98_brought_into_life", "ontome"),
    _p(CRM, "P100_was_death_of", "ontome"),
    _p(CRM, "P107_has_current_or_former_member", "ontome", "Familie → Mitglied (ohne Zeit); mit Zeit: C5"),
    _p(CRM, "P129_is_about", "ontome", "Dodis-Thema"),
    _p(CRM, "P140_assigned_attribute_to", "ontome"),
    _p(CRM, "P141_assigned", "ontome"),
    _p(CRM, "P152_has_parent", "ontome", "SSRQ fatherOf/motherOf, umgekehrt gelesen"),
    _p(CRM, "P168_place_is_defined_by", "ontome", "WKT «POINT(lon lat)»"),
    _p(CRM, "P177_assigned_property_of_type", "ontome", "die Rolle als E55"),
    _p(CRM, "P190_has_symbolic_content", "ontome", "der Namenstext an der E41"),
    # ── Properties aus SDHSS (OntoMe) ────────────────────────────────────────
    _p(SDH_SLC, "P20", "ontome", "had partner: C9 → E21, genau zwei"),
    _p(SDH_SLC, "P21", "ontome", "has type of interaction: C9 → C10"),
    _p(SDH_SLC, "P16", "ontome", "has relationship type: C3 → C4"),
    _p(SDH_SLC, "P17", "ontome", "has relationship source: C3 → E21"),
    _p(SDH_SLC, "P18", "ontome", "has relationship target: C3 → E21"),
    _p(SDH_SLC, "P1", "ontome", "was a membership of: C5 → E21"),
    _p(SDH_SLC, "P2", "ontome", "was membership in: C5 → E74"),
    _p(SDH_SLC, "P3", "ontome", "has membership type: C5 → C6"),
    _p(SDH, "P99", "ontome", "has group type: E74 → crm-sup:C9"),
    _p(SDH_SLC, "P26", "ontome", "is embodiment by: C13 → E21"),
    _p(SDH_SLC, "P33", "ontome", "is embodiment of: C13 → C12"),
    # ── W3C ──────────────────────────────────────────────────────────────────
    _p(RDF_NS, "type", "w3c"),
    _p(RDFS_NS, "label", "w3c", range=LITERAL),
    _p(RDFS_NS, "comment", "w3c", range=LITERAL),
    _p(OWL, "sameAs", "w3c", "öffentliche URI der Quelle: dodis.ch, Register-URI, Wikidata, GND, HLS"),
    _c(PROV, "Entity", "w3c", "der Datensatz (Dodis Open-Data 2024, SSRQ-Dump, KF-Edition)"),
    _c(PROV, "Activity", "w3c", "der Bau"),
    _p(PROV, "wasDerivedFrom", "w3c", "jeder Knoten → sein Datensatz"),
    _p(PROV, "wasGeneratedBy", "w3c"),
    _p(PROV, "generatedAtTime", "w3c", range=LITERAL),
    _p(PROV, "startedAtTime", "w3c", range=LITERAL),
    _p(PROV, "endedAtTime", "w3c", range=LITERAL),
    _p(PROV, "wasAssociatedWith", "w3c"),
    _p(PROV, "value", "w3c", range=LITERAL),
    _p(GEO, "lat", "w3c", "wgs84 — zusätzlich zu P168, weil QLever und Karten es so lesen", range=LITERAL),
    _p(GEO, "long", "w3c", range=LITERAL),
)

BY_URI: dict[str, Term] = {t.uri: t for t in TERMS}
CLASSES: frozenset[str] = frozenset(t.uri for t in TERMS if t.kind == "class")
PROPERTIES: frozenset[str] = frozenset(t.uri for t in TERMS if t.kind == "property")


def term(uri: str) -> Optional[Term]:
    return BY_URI.get(str(uri))


def curie(uri: str) -> str:
    """Kurzform für Meldungen — nie für den Graphen selbst."""
    u = str(uri)
    for pre, ns in sorted(PREFIXES.items(), key=lambda kv: -len(kv[1])):
        if u.startswith(ns):
            return f"{pre}:{u[len(ns):]}"
    return u
