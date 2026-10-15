"""dodis.py — KG-7 (#651): Dodis in den Bestandsgraphen.

    python -m knowledge_hub.sources.dodis --db /home/tobias/mcp/data/dodis.db --out build/dodis.nt
    python -m knowledge_hub.sources.dodis --db … --out … --limit 500 --validate

Liest ``dodis.db`` (Schema in ``dodis_mcp/build_db.py``, Open-Data-Export 2024)
direkt — die MCP-Schnittstelle liefert an ``get_document`` Namen ohne Ids.
Gemessen 14.10.2026: 48 001 Dokumente (47 236 datiert), 64 352 Personen ohne
GND/Wikidata, 12 326 Orte (12 110 mit Koordinaten, 3 217 mit Wikidata), 1 379
Themen; 341 553 Dokument–Person-Kanten (mention 251 883, signatory 31 791,
author 28 272, adressee 22 663, address_of_copy 6 944), 372 122 Orts- und
129 123 Themen-Kanten.

Mapping (docs/KG_BESTANDSGRAPH_MAPPING.md): Dokument E31 mit owl:sameAs
dodis.ch/<id>, Datierung an der E65 Creation (P4), Typ als E55; Person E21 mit
owl:sameAs dodis.ch/P<id>; jede Verknüpfung P67; author → E65/P14; die drei
anderen Rollen → E13 mit P177-Rollentyp; Orte E53 mit geo + Wikidata; Themen
E55 über P129. Eine Kante auf eine Id ohne Datensatz wird gezählt
(``withheld``), nie zu einem Knoten erfunden.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

DATASET = bg.Dataset(
    "dodis", "Dodis — Diplomatische Dokumente der Schweiz, Open Data",
    "dodis-opendata-2024.sql (Export 2024)", "https://opendata.dodis.ch/")
WD = "https://www.wikidata.org/entity/{id}"
#: Dodis-Rollen → was der Graph daraus macht. ``author`` ist Autorschaft (E65);
#: die drei anderen sind Zuweisungen (E13). Nur was hier steht, wird gebaut —
#: eine neue Rolle im Export ist eine Entscheidung, keine stille Übernahme.
ROLES = {"author": "author", "signatory": "role", "adressee": "role", "address_of_copy": "role"}
_DMY = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s*$")
_MY = re.compile(r"^\s*(\d{1,2})\.(\d{4})\s*$")
_Y = re.compile(r"^\s*(\d{4})\s*$")


def _date_bounds(doc_date: Optional[str], year: Optional[int]) -> tuple[Optional[str], Optional[str]]:
    """So genau wie die Quelle: «21.7.1955» → ein Tag, «7.1955» → ein Monat,
    «1955» oder nur ``year`` → ein Jahr. Alles andere ist kein Datum."""
    s = (doc_date or "").strip()
    m = _DMY.match(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            date(y, mo, d)
        except ValueError:
            pass            # «31.2.1955» ist kein Tag; das Jahr darunter bleibt
        else:
            return f"{y:04d}-{mo:02d}-{d:02d}", f"{y:04d}-{mo:02d}-{d:02d}"
    m = _MY.match(s)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            last = {1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31}[mo]
            return f"{y:04d}-{mo:02d}-01", f"{y:04d}-{mo:02d}-{last:02d}"
    m = _Y.match(s)
    if m:
        y = int(m.group(1))
        return f"{y:04d}-01-01", f"{y:04d}-12-31"
    if year:
        return f"{int(year):04d}-01-01", f"{int(year):04d}-12-31"
    return None, None


def export(db: Path, out: Path, *, limit: Optional[int] = None,
           validate: bool = False, shacl: bool = False) -> ExportReport:
    t0 = time.monotonic()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    b = bg.Build(DATASET)
    rep = ExportReport(source="dodis", built_at=now(), db=str(db), destination=str(out),
                       commit=git_commit())
    counts = {"documents": 0, "persons": 0, "places": 0, "topics": 0,
              "mentions_person": 0, "authors": 0, "roles": 0,
              "mentions_place": 0, "about": 0}
    held = {"dangling_persons": 0, "dangling_places": 0, "dangling_topics": 0,
            "unknown_roles": 0, "undated_documents": 0}

    # Dokumente, in Id-Reihenfolge; --limit nimmt die ersten n.
    docs = q("SELECT id, title, doc_date, year, doc_type FROM documents ORDER BY CAST(id AS INTEGER), id").fetchall()
    if limit is not None:
        docs = docs[:limit]
    doc_ids = {r["id"] for r in docs}
    doc_uri: dict[str, object] = {}
    for r in docs:
        begin, end = _date_bounds(r["doc_date"], r["year"])
        if begin is None:
            held["undated_documents"] += 1
        u = b.document(r["id"], (r["title"] or f"Dodis {r['id']}").strip(),
                       same_as=[f"https://dodis.ch/{r['id']}"])
        if begin is not None:
            _exact_span(b, b.creation(u), begin, end)
        if r["doc_type"]:
            b.g.add((u, bg.CRM["P2_has_type"], b.type_(r["doc_type"].strip(), "document-type")))
        doc_uri[r["id"]] = u
        counts["documents"] += 1

    # Personen: nur die, die ein gebautes Dokument verknüpft (bei --limit), sonst alle.
    persons = {r["id"]: r for r in q("SELECT id, name, last_name, first_name FROM persons").fetchall()}
    places = {r["id"]: r for r in q("SELECT id, name, country_code, latitude, longitude, wikidata_id FROM places").fetchall()}
    tags = {r["id"]: r for r in q("SELECT id, name FROM tags").fetchall()}
    person_uri: dict[str, object] = {}
    place_uri: dict[str, object] = {}
    topic_uri: dict[str, object] = {}

    def person_node(pid: str):
        if pid in person_uri:
            return person_uri[pid]
        p = persons.get(pid)
        if p is None:
            return None
        label = (p["name"] or " ".join(x for x in (p["first_name"], p["last_name"]) if x) or f"P{pid}").strip()
        u = b.person(pid, label, same_as=[f"https://dodis.ch/P{pid}"])
        person_uri[pid] = u
        counts["persons"] += 1
        return u

    def place_node(plid: str):
        if plid in place_uri:
            return place_uri[plid]
        p = places.get(plid)
        if p is None:
            return None
        # Dodis führt die Wikidata-Id ohne «Q» («30» für Q30, gemessen an der
        # Fixture); die URI braucht es.
        same = [WD.format(id=_qid(p["wikidata_id"]))] if p["wikidata_id"] else []
        lat = p["latitude"] if p["latitude"] is not None else None
        lon = p["longitude"] if p["longitude"] is not None else None
        u = b.place(plid, (p["name"] or "").strip() or None, lat=lat, lon=lon, same_as=same)
        place_uri[plid] = u
        counts["places"] += 1
        return u

    def topic_node(tid: str):
        if tid in topic_uri:
            return topic_uri[tid]
        t = tags.get(tid)
        if t is None:
            return None
        u = b.topic(tid, (t["name"] or f"T{tid}").strip())
        topic_uri[tid] = u
        counts["topics"] += 1
        return u

    for r in q("SELECT document_id, person_id, relationship_type FROM document_persons "
               "ORDER BY document_id, person_id, relationship_type").fetchall():
        du = doc_uri.get(r["document_id"])
        if du is None:
            continue
        pu = person_node(r["person_id"])
        if pu is None:
            held["dangling_persons"] += 1
            continue
        b.mention(du, pu)
        counts["mentions_person"] += 1
        kind = ROLES.get(r["relationship_type"])
        if kind == "author":
            b.author(du, pu); counts["authors"] += 1
        elif kind == "role":
            b.role(du, pu, r["relationship_type"]); counts["roles"] += 1
        elif r["relationship_type"] != "mention":
            held["unknown_roles"] += 1
    for r in q("SELECT document_id, place_id FROM document_places ORDER BY document_id, place_id").fetchall():
        du = doc_uri.get(r["document_id"])
        if du is None:
            continue
        plu = place_node(r["place_id"])
        if plu is None:
            held["dangling_places"] += 1
            continue
        b.mention(du, plu); counts["mentions_place"] += 1
    for r in q("SELECT document_id, tag_id FROM document_tags ORDER BY document_id, tag_id").fetchall():
        du = doc_uri.get(r["document_id"])
        if du is None:
            continue
        tu = topic_node(r["tag_id"])
        if tu is None:
            held["dangling_topics"] += 1
            continue
        b.about(du, tu); counts["about"] += 1
    if limit is None:
        # Ohne --limit gehören auch Personen/Orte/Themen ohne Dokument in den Graphen:
        # sie sind Teil des Bestands, nur unverknüpft.
        for pid in sorted(persons, key=lambda x: (len(x), x)):
            person_node(pid)
        for plid in sorted(places, key=lambda x: (len(x), x)):
            place_node(plid)
        for tid in sorted(tags, key=lambda x: (len(x), x)):
            topic_node(tid)
    b.activity("Dodis-Export (KG-7)", commit=rep.commit or None, started=rep.built_at, ended=now())
    con.close()

    rep.counts = counts
    rep.withheld = held
    if validate:
        rep.validation = validate_graph(b.g, shacl=shacl)
    rep.triples = write_sorted_ntriples(b.g, out)
    rep.seconds = time.monotonic() - t0
    write_manifest(rep, out)
    return rep


def _qid(raw: str) -> str:
    v = str(raw).strip()
    return v if v.upper().startswith("Q") else f"Q{v}"


def _exact_span(b: bg.Build, subject, begin: str, end: str) -> None:
    """Tag- oder Monatsgenau: P82a/P82b direkt, nicht über das Jahr."""
    from rdflib import Literal, URIRef, RDF, XSD
    u = URIRef(str(subject) + "/time-span")
    b.g.add((subject, bg.CRM["P4_has_time-span"], u))
    b.g.add((u, RDF.type, bg.CRM["E52_Time-Span"]))
    b.g.add((u, bg.CRM["P82a_begin_of_the_begin"], Literal(begin, datatype=XSD.date)))
    b.g.add((u, bg.CRM["P82b_end_of_the_end"], Literal(end, datatype=XSD.date)))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Dokumente")
    ap.add_argument("--validate", action="store_true", help="KG-6-Prüfung (ohne SHACL) mitlaufen lassen")
    ap.add_argument("--shacl", action="store_true", help="auch SHACL — nur für kleine Graphen")
    args = ap.parse_args(argv)
    rep = export(args.db, args.out, limit=args.limit, validate=args.validate, shacl=args.shacl)
    print(rep.render())
    return 0 if (not args.validate or rep.validation.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
