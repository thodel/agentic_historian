"""sikart.py — KG-14 (ch-h-bot#582, S-7): SIKART Künstlernamen in den Bestandsgraphen.

    python -m knowledge_hub.sources.sikart --db /home/tobias/mcp/data/sikart.db --out build/sikart.nt --validate

Liest ``sikart.db`` (Schema in ``sikart_mcp/build_db.py`` und ``link_authorities.py``;
SIK-ISEA, ``personen.csv``, CC0). Gemessen 30.10.2026: 17 665 Einträge — 17 441
Personen, 150 Kollektive, 74 Künstlerfamilien —, 26 456 Daten in 13 Typen, Orte zu
start/end, 29 029 Kennungen SIKARTs (Wikidata 17 657, GND 9 855, HLS 1 517), dazu die
Tabelle ``links`` aus S-6 (#581): Behauptungen Wikidatas, Metagrids und unsere Abgleiche.

**Person → E21, Kollektiv/Familie → E74** mit Gruppentyp (crm-sup:C9 «Künstlerkollektiv»,
«Künstlerfamilie»). Die SIKART-Id ist E42 (``P1_is_identified_by``, Quelle «sikart»), der
Permalink ``owl:sameAs``. Name als E41 «Name», die Sortierform als E41 «Sortierform».

**Daten nach SIKARTs Typ, nie nach ihrer Stellung.** ``Geburtsdatum`` → E67 Birth,
``Todesdatum`` → E69 Death — und **nur** diese beiden: ``Taufdatum`` und ``Begräbnisdatum``
sind Taufe und Begräbnis, nicht Geburt und Tod, und werden E13 Attribute Assignment mit
dem Typ der Quelle. ``Erstdatum``/``Letztdatum`` (und die Epochen-Formen) sind
Wirkungsdaten: **eine** E7 Activity je Person («Tätigkeit, nachgewiesen») mit P4 Time-Span
von der ersten zur letzten Erwähnung. ``Geburtsnachweisdatum``/``Todesnachweisdatum``/
``Nachweisdatum`` sind Erwähnungen, keine Ereignisse: E13 mit dem Typ der Quelle, wie das
MLS «nachgewiesen» hält (keine eigene sdhss-Qualifikation: OntoMe hat dafür keinen
Begriff, und ein Begriff ohne Quelle wird nicht erfunden). ``Gründungsdatum``/
``Auflösdatum`` der Gruppen ebenso E13. Die Kürzung lebender Personen (#577 b) steht schon
in der Datenbank; der Graph kann nicht mehr sagen als sie.

**Orte nur als E41 am Ereignis** (Epic 14, Stolperstein 5): «Basel (BS)» ist ein Name, keine
E53 ohne Id. Geburts-/Todesort hängen an der E67/E69, Erst-/Letzt-Ort an der E7, die
Nachweisorte an ihrer E13.

**Identität je Behauptung.** Jede Kennung — SIKARTs eigene wie die aus ``links`` — ist eine
E42 mit ``P190`` Wert, ``P2`` Schema und ``prov:wasDerivedFrom`` auf **ihre** Quelle: die
CSV (ETag, Datum) für SIKART, die SPARQL-Abfrage (Datum) für Wikidata, der Metagrid-Aufruf,
der Abgleich (Methode, Datum) für unsere Treffer. Dazu, wo die Quelle eine URI hat und die
Behauptung Konfidenz 1 trägt, ``owl:sameAs`` von der Person auf Wikidata, GND, HLS-Artikel,
``dodis.ch/P<id>``, VIAF, histhub, Theaterlexikon. Ein Abgleich mit Konfidenz < 1 (HBLS,
0,99 an einer gelesenen Stichprobe) wird **nicht** ``owl:sameAs``: seine E42 trägt
``shb:matchConfidence``, und da das HBLS keine URI je Person hat, bleibt es die E42. Ein
Konflikt (S-6: 161) wird **beidseitig** geschrieben, die beiden E42 zeigen mit
``shb:conflictsWith`` aufeinander; keine stille Wahl.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from rdflib import Literal, URIRef, RDF, XSD

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.ontology.registry import SHB, SKOS
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

LABEL = "SIKART Künstlernamen"
CSV_URL = "https://data.sik-isea.ch/recherche/v1/personen.csv"
URI_OF = {
    "wikidata": "https://www.wikidata.org/entity/{}",
    "gnd": "https://d-nb.info/gnd/{}",
    "hls": "https://hls-dhs-dss.ch/de/articles/{}/",
    "dodis": "https://dodis.ch/P{}",
    "viaf": "https://viaf.org/viaf/{}",
    "metagrid-histhub": "https://data.histhub.ch/person/{}",
    "theaterlexikon": "https://tls.theaterwissenschaft.ch/wiki/{}",
    "wd-pre1500": "https://www.wikidata.org/entity/{}",
}
GROUP_TYPE = {"collective": "Künstlerkollektiv", "family": "Künstlerfamilie"}
BIRTH, DEATH = "Geburtsdatum", "Todesdatum"
ACTIVITY = {"Erstdatum", "Letztdatum", "Epochenerstdatum", "Epochenletztdatum"}
#: Quelle einer Behauptung in ``links`` → Schlüssel und Label des Teildatensatzes.
SOURCES = {
    "sikart": ("csv", "SIKART personen.csv (SIK-ISEA, CC0)"),
    "wikidata": ("wikidata-sparql", "Wikidata — SPARQL P781 → P902 P227 P701 P1362 P570"),
    "metagrid": ("metagrid", "Metagrid — widget/sikart/person/<id>.json"),
    "match": ("match", "Abgleich (link_authorities.py)"),
}


def _iso(year: Optional[int], iso: Optional[str], which: str) -> Optional[str]:
    if iso:
        return iso
    if year is None:
        return None
    return f"{int(year):04d}-01-01" if which == "start" else f"{int(year):04d}-12-31"


def _span(b: bg.Build, subject: URIRef, begin: Optional[str], end: Optional[str],
          note: Optional[str] = None) -> None:
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


def _assignment(b: bg.Build, subject: URIRef, which: str, kind: str, raw: str,
                begin: Optional[str], end: Optional[str]) -> URIRef:
    """E13: «SIKART schreibt dieser Person dieses Datum mit diesem Typ zu»."""
    u = URIRef(f"{subject}/date/{which}")
    b.g.add((u, RDF.type, bg.CRM["E13_Attribute_Assignment"]))
    b.g.add((u, bg.CRM["P140_assigned_attribute_to"], subject))
    b.g.add((u, bg.CRM["P177_assigned_property_of_type"], b.type_(kind, "date-type")))
    b.g.add((u, bg.PROV["wasDerivedFrom"], b.dataset_uri))
    _span(b, u, begin, end, None if (begin or end) else f"Quelle: {kind} {raw}")
    return u


def _activity(b: bg.Build, person: URIRef) -> URIRef:
    u = URIRef(f"{person}/activity")
    if (u, RDF.type, bg.CRM["E7_Activity"]) not in b.g:
        b.g.add((u, RDF.type, bg.CRM["E7_Activity"]))
        b.g.add((u, bg.CRM["P14_carried_out_by"], person))
        b.g.add((u, bg.CRM["P2_has_type"], b.type_("Tätigkeit, nachgewiesen", "activity-type")))
        b.g.add((u, bg.PROV["wasDerivedFrom"], b.dataset_uri))
    return u


def _version(con: sqlite3.Connection, db: Path) -> tuple[str, Optional[str], Optional[str]]:
    try:
        row = con.execute("SELECT finished_at, etag, last_modified FROM ingest_runs "
                          "WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return f"{db.name}", None, None
    return f"sikart.db, Ingest {row[0]} (personen.csv, Last-Modified {row[2]}, ETag {row[1]}; CC0)", row[1], row[2]


def _has(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


def export(db: Path, out: Path, *, limit: Optional[int] = None, validate: bool = False,
           shacl: bool = False, commit: Optional[str] = None) -> ExportReport:
    t0 = time.monotonic()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    version, etag, last_modified = _version(con, db)
    b = bg.Build(bg.Dataset("sikart", LABEL, version, uri=CSV_URL))
    rep = ExportReport(source="sikart", built_at=now(), db=str(db), destination=str(out),
                       commit=commit or git_commit())
    counts = {"persons": 0, "groups": 0, "names": 0, "identifiers_sikart": 0,
              "births": 0, "deaths": 0, "activities": 0, "date_assignments": 0,
              "place_names": 0, "same_as": 0, "same_as_hls": 0, "persons_with_hls": 0,
              "assertions": {}, "close_matches": 0, "conflict_edges": 0, "redacted": 0}
    held = {"dates_without_year": 0, "links_without_uri_scheme": 0,
            "matches_below_1_not_sameas": 0, "links_table": "fehlt" if not _has(con, "links") else "gelesen"}

    # ── Teildatensätze je Quelle einer Behauptung ─────────────────────────
    csv_ds = b.sub_dataset("csv", f"{LABEL} — personen.csv",
                           version=" · ".join(x for x in (last_modified, etag) if x) or None, uri=CSV_URL)
    source_ds: dict[str, URIRef] = {"sikart": csv_ds}
    if _has(con, "links"):
        for by, method, first, last in q(
                "SELECT asserted_by, method, MIN(retrieved_at), MAX(retrieved_at) FROM links "
                "WHERE asserted_by != 'sikart' GROUP BY asserted_by, method"):
            key, label = SOURCES.get(by, (by, by))
            k = f"{key}-{bg.slug(method)[:60]}"
            source_ds[(by, method)] = b.sub_dataset(k, f"{label}", version=f"{method}; {first} – {last}")

    # ── Personen und Gruppen ──────────────────────────────────────────────
    person_uri: dict[str, URIRef] = {}
    rows = q("SELECT id, permalink, name, name_sort, kind, vita, redacted FROM persons ORDER BY id").fetchall()
    if limit is not None:
        rows = rows[:limit]
    for p in rows:
        pid = p["id"]
        if p["kind"] == "person":
            u = b.person(pid, p["name"], same_as=[p["permalink"]], identifier=("sikart", pid))
            counts["persons"] += 1
        else:
            u = b.group(pid, p["name"], group_type=GROUP_TYPE.get(p["kind"], p["kind"]),
                        same_as=[p["permalink"]], identifier=("sikart", pid))
            counts["groups"] += 1
        counts["identifiers_sikart"] += 1
        b.name(u, p["name"], kind="Name", key=f"{pid}-name")
        if p["name_sort"] and p["name_sort"] != p["name"]:
            b.name(u, p["name_sort"], kind="Sortierform", key=f"{pid}-sort")
            counts["names"] += 1
        counts["names"] += 1
        if p["vita"]:
            b.note(u, p["vita"])
        if p["redacted"]:
            b.note(u, "lebende Person: Tag, Monat und Geburtsort zurückgehalten, das Jahr steht (ch-h-bot#577 b)")
            counts["redacted"] += 1
        person_uri[pid] = u

    # ── Daten und Orte ────────────────────────────────────────────────────
    places = {}
    for pl in q("SELECT person_id, role, type, raw FROM places"):
        places[(pl["person_id"], pl["role"])] = (pl["type"], pl["raw"])
    for d in q("SELECT person_id, role, type, raw, year, iso FROM dates ORDER BY person_id, role DESC"):
        u = person_uri.get(d["person_id"])
        if u is None:
            continue
        which = d["role"]
        if d["year"] is None:
            held["dates_without_year"] += 1
        begin = _iso(d["year"], d["iso"], "start")
        end = _iso(d["year"], d["iso"], "end")
        typ = d["type"]
        is_person = (u, RDF.type, bg.CRM["E21_Person"]) in b.g
        if typ in (BIRTH, DEATH) and not is_person:
            # SIKART gibt sieben Gruppen ein Geburts- oder Todesdatum; eine Gruppe wird
            # nicht geboren (P98/P100 verlangen E21) — die Angabe bleibt als E13 mit ihrem Typ.
            ev = _assignment(b, u, which, typ, d["raw"], begin, end)
            counts["date_assignments"] += 1
            held["birth_death_on_groups"] = held.get("birth_death_on_groups", 0) + 1
        elif typ == BIRTH:
            ev = b.birth(u)
            _span(b, ev, begin, end)
            counts["births"] += 1
        elif typ == DEATH:
            ev = b.death(u)
            _span(b, ev, begin, end)
            counts["deaths"] += 1
        elif typ in ACTIVITY:
            ev = _activity(b, u)
            ts = URIRef(str(ev) + "/time-span")
            if (ts, RDF.type, bg.CRM["E52_Time-Span"]) not in b.g:
                b.g.add((ev, bg.CRM["P4_has_time-span"], ts))
                b.g.add((ts, RDF.type, bg.CRM["E52_Time-Span"]))
                counts["activities"] += 1
            if which == "start" and begin:
                b.g.add((ts, bg.CRM["P82a_begin_of_the_begin"], Literal(begin, datatype=XSD.date)))
            elif which == "end" and end:
                b.g.add((ts, bg.CRM["P82b_end_of_the_end"], Literal(end, datatype=XSD.date)))
            b.note(ts, f"{typ} {d['raw']}")
        else:
            ev = _assignment(b, u, which, typ, d["raw"], begin, end)
            counts["date_assignments"] += 1
        place = places.pop((d["person_id"], which), None)
        if place:
            b.name(ev, place[1], kind=place[0], key=f"{d['person_id']}-{which}-place")
            counts["place_names"] += 1
    # Ein Ort ohne Datum derselben Rolle hängt an einer E13 «Ort ohne Datum».
    for (pid, which), (typ, raw) in places.items():
        u = person_uri.get(pid)
        if u is None:
            continue
        ev = _assignment(b, u, which, typ, raw, None, None)
        b.name(ev, raw, kind=typ, key=f"{pid}-{which}-place")
        counts["place_names"] += 1

    # ── Kennungen: jede Behauptung eine E42 mit ihrer Herkunft ────────────
    def assertion(pid: str, scheme: str, value: str, by: str, method: str, conf: Optional[float],
                  ds: URIRef) -> Optional[URIRef]:
        u = person_uri.get(pid)
        if u is None:
            return None
        e = b.ns[f"sikart/identifier/{bg.slug(scheme)}/{bg.slug(value)}/{bg.slug(by)}"]
        b.g.add((u, bg.CRM["P1_is_identified_by"], e))
        b.g.add((e, RDF.type, bg.CRM["E42_Identifier"]))
        b.g.add((e, bg.CRM["P190_has_symbolic_content"], Literal(value)))
        b.g.add((e, bg.CRM["P2_has_type"], b.type_(scheme, "identifier-source")))
        b.g.add((e, bg.PROV["wasDerivedFrom"], ds))
        counts["assertions"][f"{scheme}/{by}"] = counts["assertions"].get(f"{scheme}/{by}", 0) + 1
        if conf is None or conf < 1.0:
            # Ein Abgleich unter 1 wird kein owl:sameAs — die Konfidenz steht an der E42.
            if conf is not None:
                b.g.add((e, SHB["matchConfidence"], Literal(conf, datatype=XSD.double)))
            held["matches_below_1_not_sameas"] += 1
            return e
        pattern = URI_OF.get(scheme)
        if pattern is None:
            held["links_without_uri_scheme"] += 1
            return e
        target = URIRef(pattern.format(value))
        if (u, bg.OWL["sameAs"], target) not in b.g:
            b.g.add((u, bg.OWL["sameAs"], target))
            counts["same_as"] += 1
            if scheme == "hls":
                counts["same_as_hls"] += 1
        return e

    e42: dict[int, URIRef] = {}
    with_hls: set[str] = set()
    if _has(con, "links"):
        for lk in q("SELECT link_id, person_id, scheme, value, asserted_by, confidence, method, conflicts_with "
                    "FROM links ORDER BY link_id"):
            ds = source_ds.get("sikart" if lk["asserted_by"] == "sikart" else (lk["asserted_by"], lk["method"]),
                               b.dataset_uri)
            e = assertion(lk["person_id"], lk["scheme"], lk["value"], lk["asserted_by"], lk["method"],
                          lk["confidence"], ds)
            if e is not None:
                e42[lk["link_id"]] = e
                if lk["scheme"] == "hls" and (lk["confidence"] or 0) >= 1.0:
                    with_hls.add(lk["person_id"])
        for lk in q("SELECT link_id, conflicts_with FROM links WHERE conflicts_with IS NOT NULL"):
            a, z = e42.get(lk["link_id"]), e42.get(lk["conflicts_with"])
            if a is not None and z is not None:
                b.g.add((a, SHB["conflictsWith"], z))
                counts["conflict_edges"] += 1
    else:
        for i in q("SELECT person_id, scheme, value FROM identifiers ORDER BY person_id, scheme"):
            assertion(i["person_id"], i["scheme"], i["value"], "sikart", "csv", 1.0, csv_ds)
            if i["scheme"] == "hls":
                with_hls.add(i["person_id"])
    counts["persons_with_hls"] = len(with_hls)
    counts["close_matches"] = held["matches_below_1_not_sameas"]

    b.activity("SIKART-Export (KG-14)", commit=rep.commit or None, started=rep.built_at, ended=now())
    con.close()
    rep.counts = counts
    rep.withheld = held
    if validate:
        rep.validation = validate_graph(b.g, shacl=shacl)
    rep.triples = write_sorted_ntriples(b.g, out)
    rep.seconds = time.monotonic() - t0
    write_manifest(rep, out)
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Personen")
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--shacl", action="store_true")
    ap.add_argument("--commit", default=None)
    args = ap.parse_args(argv)
    rep = export(args.db, args.out, limit=args.limit, validate=args.validate, shacl=args.shacl,
                 commit=args.commit)
    print(rep.render())
    return 0 if (not args.validate or rep.validation.get("ok")) else 1


if __name__ == "__main__":
    sys.exit(main())
