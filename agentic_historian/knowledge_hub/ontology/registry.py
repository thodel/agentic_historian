"""registry.py — die Begriffe, die Bestands- und Korpusgraph verwenden dürfen (KG-6, #650).

Jeder Begriff ist genau eines von vieren:

- ``ontome``   in einem OntoMe-Schnappschuss oder im CIDOC-RDFS nachgewiesen —
               die URI muss dort stehen, sonst scheitert ``test_registry``.
- ``w3c``      ein Begriff aus rdf/rdfs/owl/prov/wgs84, deren Vokabulare OntoMe
               nicht führt und die wir nicht nachbauen.
- ``foreign``  ein fremdes, stabiles Vokabular ausserhalb von W3C und OntoMe
               (schema.org), ebenfalls nicht nachgebaut — ein einziger Begriff.
- ``ours``     in unserem Namensraum ``shb:`` erklärt, mit Label, Erläuterung,
               Domain und Range — damit die Prüfung sie behandeln kann wie
               jeden anderen.

Für den Bestandsgraphen (Dodis, SSRQ, Königsfelden, MLS) braucht es **keinen**
eigenen Begriff: alles, was CIDOC fehlt, hat SDHSS. Die Rubrik ``ours`` trägt
den Korpusgraphen (KG-2/3/5): was unsere Pipeline über eine Lesung, einen
Erkennungslauf, eine Fusionsentscheidung oder die Care-Einstufung sagt, sagt
keine Quelle und keine Ontologie — das sind unsere Begriffe, und seit KG-12
(#659) stehen sie unter ``https://tei.dh.unibe.ch/ontology/`` statt unter einer
URI, die SDHSS gehört. Wo CIDOC oder SDHSS den Begriff hat, steht er nicht hier:
die Auswahl der nächsten Lesung ist eine E13 Attribute Assignment, der Typ
einer Entität hängt als E55 an P2, eine Organisation ist E74 mit sdh:P99.

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
SCHEMA = Namespace("https://schema.org/")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
#: Unser Namensraum für Begriffe. Nicht ``ah:`` (das sind die Instanzen unter
#: ``/id/``), und nicht ``sdhss:`` — das gehört SDHSS.
SHB = Namespace("https://tei.dh.unibe.ch/ontology/")

PREFIXES = {
    "crm": str(CRM), "sdh": str(SDH), "sdh-slc": str(SDH_SLC), "crm-sup": str(CRM_SUP),
    "prov": str(PROV), "owl": str(OWL), "rdf": str(RDF_NS), "rdfs": str(RDFS_NS),
    "geo": str(GEO), "xsd": str(XSD), "shb": str(SHB), "schema": str(SCHEMA), "skos": str(SKOS),
}

LITERAL = "rdfs:Literal"
BOOL = str(XSD["boolean"])
INT = str(XSD["integer"])
DOUBLE = str(XSD["double"])
DATETIME = str(XSD["dateTime"])


@dataclass(frozen=True)
class Term:
    uri: str
    kind: str                 # "class" | "property"
    source: str               # "ontome" | "w3c" | "foreign" | "ours"
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
    _c(CRM, "E1_CRM_Entity", "ontome", "Korpus: Rückfall für einen Agent-C-Typ, den _ENTITY_CLASS nicht kennt"),
    _c(CRM, "E31_Document", "ontome", "Regest (Dodis), Eintrag (KF), Edition (SSRQ); Korpus: die bearbeitete Seite"),
    _c(CRM, "E22_Human-Made_Object", "ontome", "Korpus: die Seite ist auch ein Ding — Träger der Lesungen (P128)"),
    _c(CRM, "E33_Linguistic_Object", "ontome", "Korpus: eine Lesung — jede Erkennung, die Arbeitsfassung, "
       "die Kombination eines Historikers; nie «der Text» des Dokuments"),
    _c(CRM, "E21_Person", "ontome", "auch der pseudonyme Historiker, der eine Lesung auswählt (Korpus)"),
    _c(CRM, "E53_Place", "ontome"),
    _c(CRM, "E74_Group", "ontome", "Familien, Orden, Gerichte, Zünfte, Gemeinden; Korpus: ORG und SOCIAL_GROUP. "
       "E40 Legal Body gibt es in 7.1.3 nicht mehr — der Typ hängt als crm-sup:C9 daran"),
    _c(CRM, "E7_Activity", "ontome", "Korpus: CARE_ACTION — eine Fürsorgehandlung als Entität"),
    _c(CRM, "E55_Type", "ontome", "Dodis-Themen (P129), Rollen (P177), Namenstypen, Id-Quellen; "
       "Korpus: Agent-C-Typ (P2), ROLE-Entitäten, die Lesungsrolle «closest» (P141)"),
    _c(CRM, "E41_Appellation", "ontome", "Namensvarianten mit Sprache und Typ (std/orig)"),
    _c(CRM, "E42_Identifier", "ontome", "quellenlokale Ids ohne öffentliche URI"),
    _c(CRM, "E65_Creation", "ontome", "Autorschaft bei Dodis: E65 P94 Dokument, P14 Person"),
    _c(CRM, "E67_Birth", "ontome"),
    _c(CRM, "E69_Death", "ontome"),
    _c(CRM, "E52_Time-Span", "ontome"),
    _c(CRM, "E13_Attribute_Assignment", "ontome", "die Rollen signatory/adressee/address_of_copy: "
       "«der Datensatz weist Person P für Dokument D die Rolle R zu» — nicht erfunden, zugewiesen. "
       "Korpus: die Auswahl der nächsten Lesung — P140 die Lesung, P141 der Typ «closest», "
       "P14 der Historiker (statt der erfundenen sdhss:EditorialSelection, KG-12)"),
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
    _p(CRM, "P2_has_type", "ontome", "Range ist E55, **kein Literal** — der Korpusexporter schrieb bis KG-12 "
       "Literale an E42; jetzt E55: Id-Quelle, Agent-C-Typ"),
    _p(CRM, "P3_has_note", "ontome", "Fundstelle (SSRQ occurence), Bemerkungen"),
    _p(CRM, "P4_has_time-span", "ontome"),
    _p(CRM, "P7_took_place_at", "ontome", "Geburts-/Sterbeort"),
    _p(CRM, "P14_carried_out_by", "ontome", "Autorschaft (E65); Korpus: der Historiker an der E13"),
    _p(CRM, "P67_refers_to", "ontome", "die Erwähnung: Dokument → Person/Ort/Gruppe; E31 ⊂ E89"),
    _p(CRM, "P74_has_current_or_former_residence", "ontome", "SSRQ residence, KF Wohnort"),
    _p(CRM, "P82a_begin_of_the_begin", "ontome"),
    _p(CRM, "P82b_end_of_the_end", "ontome"),
    _p(CRM, "P94_has_created", "ontome"),
    _p(CRM, "P98_brought_into_life", "ontome"),
    _p(CRM, "P100_was_death_of", "ontome"),
    _p(CRM, "P107_has_current_or_former_member", "ontome", "Familie → Mitglied (ohne Zeit); mit Zeit: C5"),
    _p(CRM, "P128_carries", "ontome", "Korpus: das Dokument (E22) trägt seine Lesungen (E33) — alle, keine als «den Text»"),
    _p(CRM, "P129_is_about", "ontome", "Dodis-Thema"),
    _p(CRM, "P140_assigned_attribute_to", "ontome", "Korpus: E13 → die ausgewählte Lesung (ersetzt sdhss:selectedIn)"),
    _p(CRM, "P141_assigned", "ontome", "Korpus: E13 → E55 «closest»"),
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
    _p(SDH, "P99", "ontome", "has group type: E74 → crm-sup:C9; Korpus: ORG / SOCIAL_GROUP"),
    _p(SDH_SLC, "P26", "ontome", "is embodiment by: C13 → E21"),
    _p(SDH_SLC, "P33", "ontome", "is embodiment of: C13 → C12"),
    # ── W3C ──────────────────────────────────────────────────────────────────
    _p(RDF_NS, "type", "w3c"),
    _p(RDFS_NS, "label", "w3c", range=LITERAL),
    _p(RDFS_NS, "comment", "w3c", range=LITERAL),
    _p(OWL, "sameAs", "w3c", "öffentliche URI der Quelle: dodis.ch, Register-URI, Wikidata, GND, HLS, VIAF, DaSCH-ARK (MLS)"),
    _c(PROV, "Entity", "w3c", "der Datensatz (Dodis Open-Data 2024, SSRQ-Dump, KF-Edition, die 27 Lexika des MLS; "
       "Korpus: der data/outputs-Lauf)"),
    _c(PROV, "Activity", "w3c", "der Bau; Korpus: Erkennungslauf, Fusionsentscheidung, Auswahl"),
    _c(PROV, "Agent", "w3c", "Korpus: wer eine Lesung verantwortet — Modell oder Historiker"),
    _c(PROV, "SoftwareAgent", "w3c", "Korpus: das Erkennungsmodell"),
    _c(PROV, "Person", "w3c", "Korpus: der pseudonyme Historiker (zugleich E21)"),
    _p(SKOS, "closeMatch", "w3c", "KG-14: ein Abgleich mit Konfidenz < 1 — nie owl:sameAs; heute ungenutzt, "
       "weil das HBLS keine URI je Person hat (die Konfidenz steht an der E42)"),
    _p(PROV, "wasDerivedFrom", "w3c", "jeder Knoten → sein Datensatz; Korpus: kombinierte Lesung → ihre Quellen"),
    _p(PROV, "wasGeneratedBy", "w3c", "Korpus: Lesung → Erkennungslauf, kombinierte Lesung → E13"),
    _p(PROV, "used", "w3c", "Korpus: Lauf → Modell, E13 → die gewählten Lesungen"),
    _p(PROV, "wasAttributedTo", "w3c", "Korpus: Lesung → Modell bzw. Historiker"),
    _p(PROV, "generatedAtTime", "w3c", range=LITERAL),
    _p(PROV, "startedAtTime", "w3c", range=LITERAL),
    _p(PROV, "endedAtTime", "w3c", range=LITERAL),
    _p(PROV, "wasAssociatedWith", "w3c"),
    _p(PROV, "value", "w3c", range=LITERAL),
    _p(GEO, "lat", "w3c", "wgs84 — zusätzlich zu P168, weil QLever und Karten es so lesen", range=LITERAL),
    _p(GEO, "long", "w3c", range=LITERAL),
    # ── Fremd, nicht nachgebaut ──────────────────────────────────────────────
    _p(SCHEMA, "image", "foreign", "das Quellbild der Seite (#208) — schema.org, weil jeder Konsument "
       "den Begriff kennt; die CIDOC-Alternative wäre P138i_has_representation → E36",
       domain=str(CRM["E31_Document"])),
    # ── Unsere Begriffe (KG-12, #659) ────────────────────────────────────────
    #
    # Was hier steht, sagt unsere Pipeline — nicht die Quelle und nicht eine
    # Ontologie. Alles Literale an Lesung, Lauf, Modell, Entscheidung oder
    # Dokument; Klassen nur für die drei Aktivitäten, die PROV allein nicht
    # unterscheidet. Jeder Begriff mit Domain und Range, damit validate.py ihn
    # prüft wie einen CIDOC-Begriff.
    #
    # Klassen
    _c(SHB, "RecognitionRun", "ours", "ein Lauf eines Erkennungsmodells über eine Seite — die prov:Activity, "
       "die eine Lesung erzeugt hat (KG-3)", label="Erkennungslauf"),
    _c(SHB, "RecognitionModel", "ours", "ein Erkennungsmodell (Engine + Modell-Id) als prov:SoftwareAgent — "
       "ein eigener Knoten, damit «alles, was escriptmask gelesen hat» ein Hop ist", label="Erkennungsmodell"),
    _c(SHB, "FusionDecision", "ours", "die Entscheidung der Pipeline, Lesungen zu verschmelzen oder "
       "getrennt zu lassen (#300) — mit gemessener CER und Grund", label="Fusionsentscheidung"),
    # Lesung (E33)
    _p(SHB, "readingRole", "ours", "«working»: die Lesung, die Agent C gelesen hat; «closest»: die von einem "
       "Historiker gewählte — eine Rolle in unserer Pipeline, keine Aussage über den Text",
       domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL, label="Rolle der Lesung"),
    _p(SHB, "closestReading", "ours", "true an der Lesung, die ein Historiker als die nächste gewählt hat — "
       "der abfragbare Griff; die Auswahl selbst ist die E13", domain=str(CRM["E33_Linguistic_Object"]),
       range=BOOL, label="nächste Lesung"),
    _p(SHB, "editorialStatus", "ours", "«revisable_editorial_choice» — der Status der Auswahl, revidierbar",
       domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL, label="editorischer Status"),
    _p(SHB, "editorialCaveat", "ours", "der Vorbehalt, der mit der Auswahl reist: keine Behauptung von "
       "Richtigkeit, kein Referenztext (#326)", domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL,
       label="editorischer Vorbehalt"),
    # Lesung und Modell / Lauf — dieselbe Zahl an zwei Knoten, darum ohne Domain
    _p(SHB, "engine", "ours", "Name der Erkennungs-Engine (trocr, kraken, vlm …) — an der Lesung und am Modell",
       range=LITERAL, label="Engine"),
    _p(SHB, "modelId", "ours", "Modell-Id der Engine — an der Lesung und am Modell", range=LITERAL,
       label="Modell-Id"),
    _p(SHB, "confidence", "ours", "die Konfidenz, die die Engine für ihre Lesung meldet — an der Lesung und am Lauf",
       range=DOUBLE, label="Konfidenz"),
    # Erkennungslauf
    _p(SHB, "timingMs", "ours", "Laufzeit des Erkennungslaufs in Millisekunden",
       domain=str(SHB["RecognitionRun"]), range=INT, label="Laufzeit (ms)"),
    # Fusionsentscheidung
    _p(SHB, "hasFusionDecision", "ours", "Dokument → seine Fusionsentscheidung",
       domain=str(CRM["E31_Document"]), range=str(SHB["FusionDecision"]), label="hat Fusionsentscheidung"),
    _p(SHB, "fusionStrategy", "ours", "die Strategie der Pipeline (vote, vote+no-merge …)",
       domain=str(SHB["FusionDecision"]), range=LITERAL, label="Fusionsstrategie"),
    _p(SHB, "merged", "ours", "ob die Lesungen verschmolzen wurden; false heisst: bewusst getrennt gelassen",
       domain=str(SHB["FusionDecision"]), range=BOOL, label="verschmolzen"),
    _p(SHB, "agreementCer", "ours", "die gemessene paarweise CER zwischen den Lesungen",
       domain=str(SHB["FusionDecision"]), range=DOUBLE, label="paarweise CER"),
    _p(SHB, "decisionReason", "ours", "warum nicht verschmolzen wurde, in einem Satz",
       domain=str(SHB["FusionDecision"]), range=LITERAL, label="Grund"),
    _p(SHB, "arbitrated", "ours", "Zahl der Zeilen, die ein LLM geschlichtet hat (#406)",
       domain=str(SHB["FusionDecision"]), range=INT, label="geschlichtet"),
    _p(SHB, "llmSkipped", "ours", "ob die LLM-Schlichtung übersprungen wurde",
       domain=str(SHB["FusionDecision"]), range=BOOL, label="LLM übersprungen"),
    # Historiker (E21)
    _p(SHB, "pseudonymous", "ours", "true: das Label ist ein Pseudonym, kein Name — die Plattform-Id wird "
       "oberhalb des Exports pseudonymisiert (KG-3)", domain=str(CRM["E21_Person"]), range=BOOL,
       label="pseudonym"),
    # Dokument (E31): Datierung und Jahrhundert (KG-5)
    _p(SHB, "dating", "ours", "die Datierung, wie die Quelle oder Agent B sie schreibt — wörtlich",
       domain=str(CRM["E31_Document"]), range=LITERAL, label="Datierung"),
    _p(SHB, "datingUncertain", "ours", "true, wenn die Datierung ein Urteil ist (Flag oder «um», «ca.», «wohl»)",
       domain=str(CRM["E31_Document"]), range=BOOL, label="Datierung unsicher"),
    _p(SHB, "datingNote", "ours", "Agent Bs Notiz zur Datierung", domain=str(CRM["E31_Document"]),
       range=LITERAL, label="Notiz zur Datierung"),
    _p(SHB, "century", "ours", "das Jahrhundert, das century_of aus der Datierung gerechnet hat — unsere "
       "Arithmetik, nicht die Aussage des Dokuments", domain=str(CRM["E31_Document"]), range=INT,
       label="Jahrhundert"),
    _p(SHB, "centuryUncertain", "ours", "so unsicher wie die Datierung, am abgeleiteten Wert wiederholt",
       domain=str(CRM["E31_Document"]), range=BOOL, label="Jahrhundert unsicher"),
    # Dokument (E31): Care-Einstufung, dreiwertig (#631)
    _p(SHB, "careClassified", "ours", "ob ein Modell die Seite überhaupt eingestuft hat",
       domain=str(CRM["E31_Document"]), range=BOOL, label="care-eingestuft"),
    _p(SHB, "careRelated", "ours", "die Einstufung: fürsorgerelevant oder nicht — nur, wenn eingestuft",
       domain=str(CRM["E31_Document"]), range=BOOL, label="fürsorgerelevant"),
    _p(SHB, "careType", "ours", "Art der Fürsorge aus dem kontrollierten Vokabular (Almosen, Spital …)",
       domain=str(CRM["E31_Document"]), range=LITERAL, label="Fürsorgeart"),
    # Hub-Export (rdf_export.hub_to_graph, closest_reading_to_rdf) — nicht Teil
    # des Korpusgraphen, aber auch nicht mehr unter sdhss:
    _p(SHB, "canonicalForm", "ours", "Hub-Vokabular: die Normalform eines Begriffs",
       domain=str(CRM["E55_Type"]), range=LITERAL, label="Normalform"),
    _p(SHB, "category", "ours", "Hub-Vokabular: die Kategorie eines Begriffs (care_role, social_group …)",
       domain=str(CRM["E55_Type"]), range=LITERAL, label="Kategorie"),
    _p(SHB, "candidateOffered", "ours", "Hub-Export: eine zur Wahl gestellte Lesung, nach Engine",
       domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL, label="angebotener Kandidat"),
    _p(SHB, "candidateChosen", "ours", "Hub-Export: eine gewählte Lesung, nach Engine",
       domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL, label="gewählter Kandidat"),
    _p(SHB, "combined", "ours", "Hub-Export: ob der Historiker mehrere Lesungen kombiniert hat",
       domain=str(CRM["E33_Linguistic_Object"]), range=BOOL, label="kombiniert"),
    _p(SHB, "editorPseudonym", "ours", "Hub-Export: das Pseudonym des Historikers als Literal — der "
       "Korpusgraph führt ihn als E21 an der E13", domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL,
       label="Pseudonym des Editors"),
    # Bestandsgraph, KG-14 (SIKART): Kennungen als Behauptungen mit Herkunft
    _p(SHB, "matchConfidence", "ours", "KG-14: die an einer gelesenen Stichprobe gemessene Trefferquote eines "
       "Abgleichs, an seiner E42 — eine Behauptung der Quelle hat keine (sie sagt es)",
       domain=str(CRM["E42_Identifier"]), range=DOUBLE, label="Konfidenz des Abgleichs"),
    _p(SHB, "conflictsWith", "ours", "KG-14: zwei Kennungen desselben Schemas für dieselbe Person, von zwei "
       "Quellen verschieden behauptet — beide stehen, beidseitig verwiesen; keine stille Wahl",
       domain=str(CRM["E42_Identifier"]), range=str(CRM["E42_Identifier"]), label="widerspricht"),
    _p(SHB, "confirmedAt", "ours", "Hub-Export: Zeitpunkt der Bestätigung — der Korpusgraph trägt ihn als "
       "prov:endedAtTime an der E13", domain=str(CRM["E33_Linguistic_Object"]), range=LITERAL,
       label="bestätigt am"),
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
