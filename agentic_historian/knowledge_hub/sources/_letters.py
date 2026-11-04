"""_letters.py — was die Briefeditionen im Bestandsgraphen teilen (KG-15, KG-16).

Bullinger (ch-h-bot#632) und Escher (ch-h-bot#619) sind beide Briefwechsel mit einem
Register, dessen Personen über ``person_links`` (link_metagrid.py der beiden Provider)
Kennungen tragen — die eigenen des Registers und die, die Metagrid sammelt. Drei Dinge
sind gleich und stehen darum hier:

* **Der Brief ist eine E31, sein Schreiben eine E65.** Absender sind P14 an der E65
  (``Build.author``), Empfänger eine E13-Rolle «Empfänger» (``Build.role``), der
  Schreibort P7 an der E65, das Datum P4 an der E65 — so genau wie die Quelle: ein Tag,
  eine Spanne, oder nur eine Grenze («nicht vor», «nicht nach»).
* **Jede Kennung eine Behauptung.** Eine E42 je (Schema, Wert, wer es sagt) mit
  ``prov:wasDerivedFrom`` auf ihre Quelle (das Register oder Metagrid); ``owl:sameAs``
  nur bei Konfidenz 1 und nur, wo das Schema eine URI hat. Ein Konflikt steht beidseitig
  als ``shb:conflictsWith`` — keine stille Wahl (wie KG-14, sikart.py).
* **Eine Nennung ist P67 plus eine E13 mit Herkunft.** P67 vom Brief auf die Entität, wie
  bei den anderen Beständen; dazu eine E13 «Nennung im Text» / «Nennung im Regest» mit der
  Zahl als Notiz und ``prov:wasDerivedFrom`` auf die Textschicht, aus der sie stammt.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Optional

from rdflib import Literal, URIRef, RDF, XSD

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.ontology.registry import SHB

#: Schema einer Kennung → URI-Muster; was hier fehlt, bleibt E42 ohne owl:sameAs.
URI_OF = {
    "hls": "https://hls-dhs-dss.ch/de/articles/{}/",
    "gnd": "https://d-nb.info/gnd/{}",
    "wikidata": "https://www.wikidata.org/entity/{}",
    "dodis": "https://dodis.ch/P{}",                   # Metagrid sends the number: «10290»
    "viaf": "https://viaf.org/viaf/{}",
    "histhub": "https://data.histhub.ch/person/{}",
}

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_ISO_YEAR = re.compile(r"^(\d{4})$")


def has_table(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                       (table,)).fetchone() is not None


def bound(raw: Optional[str], which: str) -> Optional[str]:
    """Ein ISO-Datum der Quelle als Grenze: «1564-04-28» bleibt, «1564-04» wird der erste
    bzw. letzte des Monats, «1564» der 1.1. bzw. 31.12. Anderes ist keine Grenze."""
    s = (raw or "").strip()
    if _ISO_DAY.match(s):
        return s
    m = _ISO_MONTH.match(s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if which == "start":
            return f"{y:04d}-{mo:02d}-01"
        nxt = (y + (mo == 12), mo % 12 + 1)
        return (date(nxt[0], nxt[1], 1) - timedelta(days=1)).isoformat()
    m = _ISO_YEAR.match(s)
    if m:
        return f"{int(m.group(1)):04d}-01-01" if which == "start" else f"{int(m.group(1)):04d}-12-31"
    return None


def span(b: bg.Build, subject: URIRef, begin: Optional[str], end: Optional[str],
         note: Optional[str] = None) -> None:
    """P4 mit P82a/P82b als xsd:date; ohne beide Grenzen nur die Notiz."""
    if begin is None and end is None:
        if note:
            b.note(subject, note)
        return
    u = URIRef(str(subject) + "/time-span")
    b.g.add((subject, bg.CRM["P4_has_time-span"], u))
    b.g.add((u, RDF.type, bg.CRM["E52_Time-Span"]))
    if begin:
        b.g.add((u, bg.CRM["P82a_begin_of_the_begin"], Literal(begin, datatype=XSD.date)))
    if end:
        b.g.add((u, bg.CRM["P82b_end_of_the_end"], Literal(end, datatype=XSD.date)))
    if note:
        b.note(u, note)


def written_at(b: bg.Build, creation: URIRef, place: URIRef) -> None:
    b.g.add((creation, bg.CRM["P7_took_place_at"], place))


def assertion(b: bg.Build, subject: URIRef, key: str, scheme: str, value: str, by: str,
              confidence: Optional[float], source: URIRef, counts: dict, held: dict) -> URIRef:
    """Eine E42 «<by> sagt: <scheme> <value>», mit ihrer Quelle; owl:sameAs nur bei
    Konfidenz 1 und bekanntem Schema."""
    e = b.ns[f"{bg.slug(b.ds.key)}/identifier/{bg.slug(scheme)}/{bg.slug(value)}/{bg.slug(by)}/{bg.slug(key)}"]
    b.g.add((subject, bg.CRM["P1_is_identified_by"], e))
    b.g.add((e, RDF.type, bg.CRM["E42_Identifier"]))
    b.g.add((e, bg.CRM["P190_has_symbolic_content"], Literal(value)))
    b.g.add((e, bg.CRM["P2_has_type"], b.type_(scheme, "identifier-source")))
    b.g.add((e, bg.PROV["wasDerivedFrom"], source))
    tag = f"{scheme}/{by}"
    counts["assertions"][tag] = counts["assertions"].get(tag, 0) + 1
    if confidence is None or confidence < 1.0:
        if confidence is not None:
            b.g.add((e, SHB["matchConfidence"], Literal(confidence, datatype=XSD.double)))
        held["below_1_not_sameas"] = held.get("below_1_not_sameas", 0) + 1
        return e
    pattern = URI_OF.get(scheme)
    if pattern is None:
        held["no_uri_scheme"] = held.get("no_uri_scheme", 0) + 1
        return e
    target = URIRef(pattern.format(value))
    if (subject, bg.OWL["sameAs"], target) not in b.g:
        b.g.add((subject, bg.OWL["sameAs"], target))
        counts["same_as"] += 1
        if scheme == "hls":
            counts["same_as_hls"] += 1
    return e


def conflicts(b: bg.Build, con: sqlite3.Connection, e42: dict, counts: dict) -> None:
    """Die beiden E42 eines Konflikts zeigen aufeinander (person_links.conflicts_with)."""
    for link_id, other in con.execute("SELECT link_id, conflicts_with FROM person_links "
                                      "WHERE conflicts_with IS NOT NULL"):
        a, z = e42.get(link_id), e42.get(other)
        if a is not None and z is not None:
            b.g.add((a, SHB["conflictsWith"], z))
            counts["conflict_edges"] += 1


def mention(b: bg.Build, doc: URIRef, entity: URIRef, key: str, kind: str, n: Optional[int],
            source: URIRef) -> URIRef:
    """P67 und die E13 dazu: «die Quelle nennt diese Entität in diesem Brief, n-mal, in
    dieser Schicht»."""
    b.mention(doc, entity)
    u = URIRef(f"{doc}/mention/{bg.slug(key)}")
    b.g.add((u, RDF.type, bg.CRM["E13_Attribute_Assignment"]))
    b.g.add((u, bg.CRM["P140_assigned_attribute_to"], doc))
    b.g.add((u, bg.CRM["P141_assigned"], entity))
    b.g.add((u, bg.CRM["P177_assigned_property_of_type"], b.type_(kind, "mention-type")))
    b.g.add((u, bg.PROV["wasDerivedFrom"], source))
    if n and n > 1:
        b.note(u, f"{n}-mal")
    return u
