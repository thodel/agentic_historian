"""bestandsgraph.py — die Bausteine des Bestandsgraphen (KG-6, #650).

Ein ``Build`` hält einen rdflib-Graphen und prägt Knoten für Dokumente,
Personen, Orte, Gruppen, Themen und ihre Beziehungen — mit genau den Begriffen
aus ``knowledge_hub.ontology.registry``. Die Exporte KG-7/8/9 rufen diese
Methoden und schreiben keine Tripel von Hand; so kann kein Export einen
Begriff einführen, den das Register nicht kennt.

Drei Regeln:

1. **Jeder Knoten trägt seine Herkunft.** ``prov:wasDerivedFrom`` zeigt auf den
   Datensatz, aus dem er stammt (Dodis Open-Data 2024, SSRQ-Dump, KF-Edition).
   Der Graph sagt nicht, was wahr ist, sondern was welche Quelle behauptet.
2. **Keine Blank Nodes.** Alle URIs kommen aus stabilen Schlüsseln, damit zwei
   Läufe byteidentisch sind und ein Diff etwas bedeutet (die Regel aus KG-2).
3. **Nichts wird erfunden.** Eine öffentliche URI wird ``owl:sameAs``; eine
   quellenlokale Id wird E42 Identifier mit Literal; ein Ort ohne Namen bekommt
   keinen; ein Typ ohne Quelle keinen Namen.

URIs liegen unter ``https://tei.dh.unibe.ch/id/<quelle>/<art>/<schlüssel>``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from rdflib import Graph, Literal, Namespace, URIRef, RDF, RDFS, XSD

from knowledge_hub.ontology.registry import (
    CRM, SDH, SDH_SLC, CRM_SUP, PROV, OWL, GEO, PREFIXES,
)

AH = Namespace("https://tei.dh.unibe.ch/id/")


def slug(value: str) -> str:
    s = (value or "").translate(str.maketrans(
        {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"}))
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-") or "x"


@dataclass(frozen=True)
class Dataset:
    """Der Datensatz, aus dem ein Bestand stammt — als prov:Entity im Graphen."""
    key: str          # «dodis», «ssrq», «kf»
    label: str
    version: str      # «Open-Data-Export 2024», «ssrq__fuseki_042810.ttl (28.06.2026)»
    uri: Optional[str] = None   # die Quelle, wenn sie eine URI hat


class Build:
    def __init__(self, dataset: Dataset, ns: Namespace = AH):
        self.g = Graph()
        for pre, uri in PREFIXES.items():
            self.g.bind(pre, uri)
        self.g.bind("ah", ns)
        self.ns = ns
        self.ds = dataset
        self.dataset_uri = ns[f"dataset/{slug(dataset.key)}"]
        g = self.g
        g.add((self.dataset_uri, RDF.type, PROV["Entity"]))
        g.add((self.dataset_uri, RDFS.label, Literal(dataset.label)))
        g.add((self.dataset_uri, PROV["value"], Literal(dataset.version)))
        if dataset.uri:
            g.add((self.dataset_uri, OWL["sameAs"], URIRef(dataset.uri)))

    # ── Prägen ────────────────────────────────────────────────────────────
    def _uri(self, kind: str, key: str) -> URIRef:
        return self.ns[f"{slug(self.ds.key)}/{kind}/{slug(key)}"]

    def _node(self, kind: str, key: str, cls: URIRef, label: Optional[str]) -> URIRef:
        u = self._uri(kind, key)
        self.g.add((u, RDF.type, cls))
        if label:
            self.g.add((u, RDFS.label, Literal(label)))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        return u

    def type_(self, label: str, scheme: str = "type") -> URIRef:
        """Ein E55 Type aus dem Vokabular der Quelle (Rolle, Thema, Namenstyp,
        Id-Quelle). Geschlüsselt nach Schema + Label, einmal je Build."""
        u = self.ns[f"{slug(self.ds.key)}/{slug(scheme)}/{slug(label)}"]
        if (u, RDF.type, CRM["E55_Type"]) not in self.g:
            self.g.add((u, RDF.type, CRM["E55_Type"]))
            self.g.add((u, RDFS.label, Literal(label)))
            self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        return u

    def identifier(self, subject: URIRef, source: str, value: str) -> URIRef:
        """Quellenlokale Id als E42 mit Literal — nie als erfundene URI."""
        u = self.ns[f"{slug(self.ds.key)}/identifier/{slug(source)}/{slug(value)}"]
        self.g.add((subject, CRM["P1_is_identified_by"], u))
        self.g.add((u, RDF.type, CRM["E42_Identifier"]))
        self.g.add((u, CRM["P190_has_symbolic_content"], Literal(value)))
        self.g.add((u, CRM["P2_has_type"], self.type_(source, "identifier-source")))
        return u

    def same_as(self, subject: URIRef, uris: Iterable[str]) -> None:
        for uri in uris:
            if uri:
                self.g.add((subject, OWL["sameAs"], URIRef(uri)))

    # ── Entitäten ─────────────────────────────────────────────────────────
    def document(self, key: str, label: str, *, same_as: Iterable[str] = (),
                 identifier: Optional[tuple[str, str]] = None,
                 begin: Optional[int] = None, end: Optional[int] = None) -> URIRef:
        """Die Datierung hängt **nicht** am Dokument: P4 verlangt eine E2 Temporal
        Entity, und ein Dokument ist keine. Sie hängt an seiner E65 Creation —
        dem Ausstellen der Urkunde, dem Schreiben des Regests. Die Prüfung hat
        das beim ersten Bau gemeldet (``domain E31 → E2``); so ist es richtig."""
        u = self._node("document", key, CRM["E31_Document"], label)
        self.same_as(u, same_as)
        if identifier:
            self.identifier(u, *identifier)
        if begin is not None or end is not None:
            self.timespan(self.creation(u), begin, end)
        return u

    def creation(self, doc: URIRef) -> URIRef:
        """Die eine E65 Creation je Dokument — Datierung und Autorschaft teilen sie."""
        u = URIRef(str(doc) + "/creation")
        if (u, RDF.type, CRM["E65_Creation"]) not in self.g:
            self.g.add((u, RDF.type, CRM["E65_Creation"]))
            self.g.add((u, CRM["P94_has_created"], doc))
            self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        return u

    def person(self, key: str, label: str, *, same_as: Iterable[str] = (),
               identifier: Optional[tuple[str, str]] = None) -> URIRef:
        u = self._node("person", key, CRM["E21_Person"], label)
        self.same_as(u, same_as)
        if identifier:
            self.identifier(u, *identifier)
        return u

    def place(self, key: str, label: Optional[str], *, lat: Optional[float] = None,
              lon: Optional[float] = None, same_as: Iterable[str] = (),
              identifier: Optional[tuple[str, str]] = None) -> URIRef:
        u = self._node("place", key, CRM["E53_Place"], label)
        self.same_as(u, same_as)
        if identifier:
            self.identifier(u, *identifier)
        if lat is not None and lon is not None:
            self.g.add((u, GEO["lat"], Literal(lat, datatype=XSD.decimal)))
            self.g.add((u, GEO["long"], Literal(lon, datatype=XSD.decimal)))
            self.g.add((u, CRM["P168_place_is_defined_by"], Literal(f"POINT({lon} {lat})")))
        return u

    def group(self, key: str, label: str, *, group_type: Optional[str] = None,
              same_as: Iterable[str] = (), identifier: Optional[tuple[str, str]] = None) -> URIRef:
        u = self._node("group", key, CRM["E74_Group"], label)
        self.same_as(u, same_as)
        if identifier:
            self.identifier(u, *identifier)
        if group_type:
            t = self.ns[f"{slug(self.ds.key)}/group-type/{slug(group_type)}"]
            if (t, RDF.type, CRM_SUP["C9"]) not in self.g:
                self.g.add((t, RDF.type, CRM_SUP["C9"]))
                self.g.add((t, RDFS.label, Literal(group_type)))
                self.g.add((t, PROV["wasDerivedFrom"], self.dataset_uri))
            self.g.add((u, SDH["P99"], t))
        return u

    def topic(self, key: str, label: str, *, same_as: Iterable[str] = ()) -> URIRef:
        u = self._node("topic", key, CRM["E55_Type"], label)
        self.same_as(u, same_as)
        return u

    # ── Namen, Zeit, Notizen ──────────────────────────────────────────────
    def name(self, subject: URIRef, text: str, *, lang: Optional[str] = None,
             kind: str = "std", key: Optional[str] = None) -> URIRef:
        """E41 Appellation mit dem Text als P190, der Art als P2 (std/orig)."""
        u = self.ns[f"{slug(self.ds.key)}/name/{slug(key or (str(subject).rsplit('/', 1)[1] + '-' + text))}"]
        self.g.add((subject, CRM["P1_is_identified_by"], u))
        self.g.add((u, RDF.type, CRM["E41_Appellation"]))
        self.g.add((u, CRM["P190_has_symbolic_content"],
                    Literal(text, lang=lang) if lang else Literal(text)))
        self.g.add((u, CRM["P2_has_type"], self.type_(kind, "name-type")))
        return u

    def timespan(self, subject: URIRef, begin: Optional[int], end: Optional[int]) -> URIRef:
        u = URIRef(str(subject) + "/time-span")
        self.g.add((subject, CRM["P4_has_time-span"], u))
        self.g.add((u, RDF.type, CRM["E52_Time-Span"]))
        if begin is not None:
            self.g.add((u, CRM["P82a_begin_of_the_begin"], Literal(f"{int(begin):04d}-01-01", datatype=XSD.date)))
        if end is not None:
            self.g.add((u, CRM["P82b_end_of_the_end"], Literal(f"{int(end):04d}-12-31", datatype=XSD.date)))
        elif begin is not None:
            self.g.add((u, CRM["P82b_end_of_the_end"], Literal(f"{int(begin):04d}-12-31", datatype=XSD.date)))
        return u

    def note(self, subject: URIRef, text: str) -> None:
        self.g.add((subject, CRM["P3_has_note"], Literal(text)))

    # ── Dokument ↔ Entität ────────────────────────────────────────────────
    def mention(self, doc: URIRef, entity: URIRef) -> None:
        """Die Erwähnung: das Dokument verweist auf die Entität. P67 vom
        Dokument, weil es hier keine Lesung gibt — der Datensatz selbst
        verweist."""
        self.g.add((doc, CRM["P67_refers_to"], entity))

    def about(self, doc: URIRef, topic: URIRef) -> None:
        self.g.add((doc, CRM["P129_is_about"], topic))

    def author(self, doc: URIRef, person: URIRef) -> URIRef:
        u = self.creation(doc)
        self.g.add((u, CRM["P14_carried_out_by"], person))
        return u

    def role(self, doc: URIRef, person: URIRef, role: str) -> URIRef:
        """«Der Datensatz weist Person P für Dokument D die Rolle R zu» — E13,
        zugewiesen und nicht behauptet. Für Dodis signatory/adressee/
        address_of_copy; Autorschaft ist E65 (``author``)."""
        u = URIRef(f"{doc}/role/{slug(role)}/{str(person).rsplit('/', 1)[1]}")
        self.g.add((u, RDF.type, CRM["E13_Attribute_Assignment"]))
        self.g.add((u, CRM["P140_assigned_attribute_to"], doc))
        self.g.add((u, CRM["P141_assigned"], person))
        self.g.add((u, CRM["P177_assigned_property_of_type"], self.type_(role, "role")))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        return u

    # ── Beziehungen zwischen Personen und Gruppen ─────────────────────────
    def parent(self, child: URIRef, parent: URIRef) -> None:
        self.g.add((child, CRM["P152_has_parent"], parent))

    def partnership(self, a: URIRef, b: URIRef, kind: str, *, note: Optional[str] = None) -> URIRef:
        """sdh-slc:C9 Relationship mit genau zwei P20-Partnern und der Art als
        C10 (Ehe, Verlobung, Scheidung …). Symmetrisch: dieselbe URI für (a,b)
        und (b,a)."""
        x, y = sorted((str(a), str(b)))
        u = self.ns[f"{slug(self.ds.key)}/partnership/{slug(kind)}/{x.rsplit('/', 1)[1]}--{y.rsplit('/', 1)[1]}"]
        self.g.add((u, RDF.type, SDH_SLC["C9"]))
        self.g.add((u, SDH_SLC["P20"], a))
        self.g.add((u, SDH_SLC["P20"], b))
        t = self.ns[f"{slug(self.ds.key)}/interaction-type/{slug(kind)}"]
        if (t, RDF.type, SDH_SLC["C10"]) not in self.g:
            self.g.add((t, RDF.type, SDH_SLC["C10"]))
            self.g.add((t, RDFS.label, Literal(kind)))
            self.g.add((t, PROV["wasDerivedFrom"], self.dataset_uri))
        self.g.add((u, SDH_SLC["P21"], t))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        if note:
            self.note(u, note)
        return u

    def relation(self, source: URIRef, target: URIRef, kind: str, *, note: Optional[str] = None) -> URIRef:
        """sdh-slc:C3 Social Relationship, gerichtet (Quelle → Ziel), Art als C4."""
        u = self.ns[f"{slug(self.ds.key)}/relation/{slug(kind)}/{str(source).rsplit('/', 1)[1]}--{str(target).rsplit('/', 1)[1]}"]
        self.g.add((u, RDF.type, SDH_SLC["C3"]))
        self.g.add((u, SDH_SLC["P17"], source))
        self.g.add((u, SDH_SLC["P18"], target))
        t = self.ns[f"{slug(self.ds.key)}/relationship-type/{slug(kind)}"]
        if (t, RDF.type, SDH_SLC["C4"]) not in self.g:
            self.g.add((t, RDF.type, SDH_SLC["C4"]))
            self.g.add((t, RDFS.label, Literal(kind)))
            self.g.add((t, PROV["wasDerivedFrom"], self.dataset_uri))
        self.g.add((u, SDH_SLC["P16"], t))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        if note:
            self.note(u, note)
        return u

    def membership(self, person: URIRef, group: URIRef, kind: str, *,
                   begin: Optional[int] = None, end: Optional[int] = None) -> URIRef:
        """sdh-slc:C5 Membership (memberOf, citizenOf, Familie) — mit Zeit, wo
        die Quelle eine hat. Dazu immer P107 an der Gruppe, damit die einfache
        Frage «wer gehört dazu» ohne den Umweg über C5 geht."""
        u = self.ns[f"{slug(self.ds.key)}/membership/{slug(kind)}/{str(person).rsplit('/', 1)[1]}--{str(group).rsplit('/', 1)[1]}"]
        self.g.add((u, RDF.type, SDH_SLC["C5"]))
        self.g.add((u, SDH_SLC["P1"], person))
        self.g.add((u, SDH_SLC["P2"], group))
        t = self.ns[f"{slug(self.ds.key)}/membership-type/{slug(kind)}"]
        if (t, RDF.type, SDH_SLC["C6"]) not in self.g:
            self.g.add((t, RDF.type, SDH_SLC["C6"]))
            self.g.add((t, RDFS.label, Literal(kind)))
            self.g.add((t, PROV["wasDerivedFrom"], self.dataset_uri))
        self.g.add((u, SDH_SLC["P3"], t))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        self.g.add((group, CRM["P107_has_current_or_former_member"], person))
        if begin is not None or end is not None:
            self.timespan(u, begin, end)
        return u

    def residence(self, person: URIRef, place: URIRef) -> None:
        self.g.add((person, CRM["P74_has_current_or_former_residence"], place))

    def birth(self, person: URIRef, *, when: Optional[str] = None,
              place: Optional[URIRef] = None) -> URIRef:
        u = URIRef(str(person) + "/birth")
        self.g.add((u, RDF.type, CRM["E67_Birth"]))
        self.g.add((u, CRM["P98_brought_into_life"], person))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        if when:
            self._date_span(u, when)
        if place is not None:
            self.g.add((u, CRM["P7_took_place_at"], place))
        return u

    def death(self, person: URIRef, *, when: Optional[str] = None,
              place: Optional[URIRef] = None) -> URIRef:
        u = URIRef(str(person) + "/death")
        self.g.add((u, RDF.type, CRM["E69_Death"]))
        self.g.add((u, CRM["P100_was_death_of"], person))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        if when:
            self._date_span(u, when)
        if place is not None:
            self.g.add((u, CRM["P7_took_place_at"], place))
        return u

    def occupation(self, person: URIRef, role: str, *, begin: Optional[int] = None,
                   end: Optional[int] = None, group: Optional[URIRef] = None,
                   note: Optional[str] = None) -> URIRef:
        """sdh-slc:C13 Social Role Embodiment: diese Person in dieser Rolle
        (C12), mit Zeit. Ein Beruf aus KF ist ein String, einer aus SSRQ ein
        Schlüssel mit Zeitraum und Institution — beide landen hier."""
        u = self.ns[f"{slug(self.ds.key)}/embodiment/{str(person).rsplit('/', 1)[1]}/{slug(role)}"]
        self.g.add((u, RDF.type, SDH_SLC["C13"]))
        self.g.add((u, SDH_SLC["P26"], person))
        r = self.ns[f"{slug(self.ds.key)}/social-role/{slug(role)}"]
        if (r, RDF.type, SDH_SLC["C12"]) not in self.g:
            self.g.add((r, RDF.type, SDH_SLC["C12"]))
            self.g.add((r, RDFS.label, Literal(role)))
            self.g.add((r, PROV["wasDerivedFrom"], self.dataset_uri))
        self.g.add((u, SDH_SLC["P33"], r))
        self.g.add((u, PROV["wasDerivedFrom"], self.dataset_uri))
        if begin is not None or end is not None:
            self.timespan(u, begin, end)
        if note:
            self.note(u, note)
        if group is not None:
            self.note(u, f"in {group}")
        return u

    def _date_span(self, subject: URIRef, when: str) -> None:
        """«1505-02-05», «1342», «1289-1321» — so genau wie die Quelle."""
        u = URIRef(str(subject) + "/time-span")
        self.g.add((subject, CRM["P4_has_time-span"], u))
        self.g.add((u, RDF.type, CRM["E52_Time-Span"]))
        m = re.fullmatch(r"(\d{3,4})-(\d{3,4})", when.strip())
        if m:
            self.g.add((u, CRM["P82a_begin_of_the_begin"], Literal(f"{int(m.group(1)):04d}-01-01", datatype=XSD.date)))
            self.g.add((u, CRM["P82b_end_of_the_end"], Literal(f"{int(m.group(2)):04d}-12-31", datatype=XSD.date)))
            return
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when.strip()):
            self.g.add((u, CRM["P82a_begin_of_the_begin"], Literal(when.strip(), datatype=XSD.date)))
            self.g.add((u, CRM["P82b_end_of_the_end"], Literal(when.strip(), datatype=XSD.date)))
            return
        if re.fullmatch(r"\d{3,4}", when.strip()):
            y = int(when.strip())
            self.g.add((u, CRM["P82a_begin_of_the_begin"], Literal(f"{y:04d}-01-01", datatype=XSD.date)))
            self.g.add((u, CRM["P82b_end_of_the_end"], Literal(f"{y:04d}-12-31", datatype=XSD.date)))
            return
        # Alles andere bleibt, was es ist: eine Notiz, keine erfundene Zahl.
        self.g.add((u, CRM["P3_has_note"], Literal(when)))

    # ── Abschluss ─────────────────────────────────────────────────────────
    def activity(self, label: str, *, commit: Optional[str] = None,
                 started: Optional[str] = None, ended: Optional[str] = None) -> URIRef:
        """Der Bau als prov:Activity — der Datensatz wurde durch ihn in den
        Graphen überführt."""
        u = self.ns[f"{slug(self.ds.key)}/build/{slug(commit or 'local')}"]
        self.g.add((u, RDF.type, PROV["Activity"]))
        self.g.add((u, RDFS.label, Literal(label)))
        if started:
            self.g.add((u, PROV["startedAtTime"], Literal(started, datatype=XSD.dateTime)))
        if ended:
            self.g.add((u, PROV["endedAtTime"], Literal(ended, datatype=XSD.dateTime)))
        if commit:
            self.g.add((u, PROV["value"], Literal(commit)))
        return u
