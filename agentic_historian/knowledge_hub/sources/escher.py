"""escher.py — KG-16 (ch-h-bot#619, E-6): die Digitale Briefedition Alfred Escher im Bestandsgraphen.

    python -m knowledge_hub.sources.escher --db /home/tobias/mcp/data/escher.db --out build/escher.nt --validate

Liest ``escher.db`` (Schema in ``escher_mcp/build_db.py``, Kennungen aus ``link_metagrid.py``,
E-5 #618; Quelle stazh/briefedition-escher, CC BY-SA 4.0 — in den Graphen kommen Metadaten,
Register und Nennungen, kein Brieftext). Gemessen 04.11.2026: 5 018 Briefe, 3 710 Personen
(678 Korrespondenten), 2 216 Orte, 21 035 Personen- und 30 499 Ortsnennungen, 3 601
Verweise von Brief zu Brief.

**Brief → E31** mit E42 (Quelle «escher», «B0133»), ``owl:sameAs`` auf den Permalink. Das
Schreiben ist die E65: Absender P14, Schreibort P7, Datum P4 (``date_when``, ein zweites
Datum ``date_when2`` macht eine Spanne; ``date_text`` als Notiz, wo die Edition unsicher
ist — «20. Mai [1878?]»). Empfänger E13 «Empfänger». Die Rollen kommen aus dem Titel der
Edition, nicht aus dem TEI (E-1: dort sind sie in 97 % vertauscht); ``role_conflict`` steht
als Notiz am Brief.

**Personen E21, Orte E53** mit dem Schlüssel des Registers als E42, Orte mit Koordinaten
und HLS-Artikel. Kennungen als Behauptungen mit Herkunft (Edition oder Metagrid), wie
``_letters.assertion``. Der Schreibort ist in der Datenbank ein Name: er wird über den
Schlüssel, sonst über einen eindeutigen Namen dem Register zugeordnet, nie geraten.

**Nennungen P67 mit Herkunft** (E13 «Nennung im Text», abgeleitet aus der Lesefassung), und
Verweise von Brief zu Brief (``letter_refs`` vom Typ «letter») als P67 zwischen E31.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from rdflib import URIRef

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources import _letters as L
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

LABEL = "Digitale Briefedition Alfred Escher (1831–1882)"
REPO = "https://github.com/stazh/briefedition-escher"
SOURCES = {"escher": ("register", "Register der Edition (people.xml)"),
           "metagrid": ("metagrid", "Metagrid — Konkordanz je Person (api.metagrid.ch)")}


def _version(con: sqlite3.Connection, db: Path) -> str:
    try:
        row = con.execute("SELECT source_commit, finished_at FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return db.name
    return f"escher.db, Ingest {row[1]} aus stazh/briefedition-escher@{(row[0] or '')[:12]} (CC BY-SA 4.0)"


def letter_dates(row) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(Anfang, Ende, Notiz): ein Tag, mit zweitem Datum eine Spanne; die Schreibweise der
    Edition als Notiz, wo sie eine Unsicherheit trägt («[1878?]», «ca.»)."""
    begin = L.bound(row["date_when"], "start")
    end = L.bound(row["date_when2"] or row["date_when"], "end")
    text = (row["date_text"] or "").strip()
    uncertain = any(c in text for c in "[?") or "ca." in text
    note = f"Datierung der Edition: {text}" if text and uncertain else None
    if begin is None and end is None and row["year"]:
        begin, end = f"{int(row['year']):04d}-01-01", f"{int(row['year_end'] or row['year']):04d}-12-31"
        note = "; ".join(x for x in (note, "nur das Jahr") if x)
    return begin, end, note


def export(db: Path, out: Path, *, limit: Optional[int] = None, validate: bool = False,
           shacl: bool = False, commit: Optional[str] = None) -> ExportReport:
    t0 = time.monotonic()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    b = bg.Build(bg.Dataset("escher", LABEL, _version(con, db), uri=REPO))
    rep = ExportReport(source="escher", built_at=now(), db=str(db), destination=str(out),
                       commit=commit or git_commit())
    counts = {"documents": 0, "persons": 0, "places": 0, "authors": 0, "addressees": 0,
              "written_at": 0, "dated": 0, "mentions_person": 0, "mentions_place": 0,
              "letter_to_letter": 0, "same_as": 0, "same_as_hls": 0, "same_as_place_hls": 0,
              "assertions": {}, "conflict_edges": 0, "role_conflicts_noted": 0}
    held = {"unregistered_correspondents": 0, "place_of_writing_unmatched": 0,
            "place_of_writing_ambiguous": 0, "dangling_mentions": 0, "undated_documents": 0,
            "links_table": "gelesen" if L.has_table(con, "person_links") else "fehlt"}
    text_ds = b.sub_dataset("lesefassung", f"{LABEL} — Lesefassung der Briefe")
    link_ds: dict = {}

    def source_of(by: str, method: str) -> URIRef:
        if (by, method) not in link_ds:
            key, label = SOURCES.get(by, (by, by))
            link_ds[(by, method)] = b.sub_dataset(f"{key}-{bg.slug(method)[:40]}", label, version=method)
        return link_ds[(by, method)]

    # ── Orte und Personen ────────────────────────────────────────────────
    place_uri: dict[str, URIRef] = {}
    by_name: dict[str, list[str]] = {}
    for p in q("SELECT * FROM places"):
        same = [L.URI_OF["hls"].format(p["hls_id"])] if p["hls_id"] else []
        u = b.place(p["key"] or p["xml_id"], p["name"] or p["key"], lat=p["lat"], lon=p["lon"],
                    same_as=same, identifier=("escher", p["key"] or p["xml_id"]))
        counts["same_as_place_hls"] += bool(same)
        place_uri[p["key"]] = u
        by_name.setdefault(p["name"], []).append(p["key"])
        counts["places"] += 1

    person_uri: dict[str, URIRef] = {}
    for p in q("SELECT * FROM persons WHERE key IS NOT NULL ORDER BY key"):
        label = " ".join(x for x in (p["forename"], p["surname"]) if x) or p["key"]
        u = b.person(p["key"], label, identifier=("escher", p["key"]))
        if label != p["key"]:
            b.name(u, p["key"], kind="Registerschlüssel", key=f"{p['key']}-key")
        life = " – ".join(x for x in (p["birth"], p["death"]) if x)
        if life:
            b.note(u, f"Lebensdaten laut Register: {life}")
        person_uri[p["key"]] = u
        counts["persons"] += 1

    # ── Kennungen als Behauptungen ───────────────────────────────────────
    e42: dict[int, URIRef] = {}
    if L.has_table(con, "person_links"):
        for lk in q("SELECT * FROM person_links ORDER BY link_id"):
            u = person_uri.get(lk["person_key"])
            if u is None:
                continue
            e42[lk["link_id"]] = L.assertion(b, u, lk["person_key"], lk["scheme"], lk["value"], lk["asserted_by"],
                                             lk["confidence"], source_of(lk["asserted_by"], lk["method"]), counts, held)
        L.conflicts(b, con, e42, counts)
    else:
        own = source_of("escher", "tei idno")
        for p in q("SELECT key, gnd, hls_id FROM persons WHERE key IS NOT NULL AND (gnd IS NOT NULL OR hls_id IS NOT NULL)"):
            if p["gnd"]:
                L.assertion(b, person_uri[p["key"]], p["key"], "gnd", p["gnd"], "escher", 1.0, own, counts, held)
            if p["hls_id"]:
                L.assertion(b, person_uri[p["key"]], p["key"], "hls", p["hls_id"], "escher", 1.0, own, counts, held)

    # ── Briefe ───────────────────────────────────────────────────────────
    doc_uri: dict[str, URIRef] = {}
    rows = q("SELECT id, b_id, title, sender_key, sender_name, addressee_key, addressee_name, role_conflict, "
             "place_name, date_when, date_when2, date_text, year, year_end, permalink FROM letters ORDER BY id").fetchall()
    if limit is not None:
        rows = rows[:limit]
    for r in rows:
        d = b.document(r["id"], r["title"] or r["b_id"], same_as=[r["permalink"]] if r["permalink"] else [],
                       identifier=("escher", r["b_id"] or r["id"]), derived_from=text_ds)
        doc_uri[r["id"]] = d
        counts["documents"] += 1
        creation = b.creation(d, text_ds)
        begin, end, note = letter_dates(r)
        L.span(b, creation, begin, end, note)
        counts["dated"] += bool(begin or end)
        held["undated_documents"] += not (begin or end)
        if r["role_conflict"]:
            b.note(d, "Absender und Empfänger nach dem Titel der Edition; das TEI nennt sie vertauscht (E-1)")
            counts["role_conflicts_noted"] += 1
        for key, name, role in ((r["sender_key"], r["sender_name"], None),
                                (r["addressee_key"], r["addressee_name"], "Empfänger")):
            person = person_uri.get(key) if key else None
            if person is None:
                if key or name:
                    held["unregistered_correspondents"] += 1
                continue
            if role:
                b.role(d, person, role)
                counts["addressees"] += 1
            else:
                b.author(d, person)
                counts["authors"] += 1
        name = (r["place_name"] or "").strip()
        if name:
            key = name if name in place_uri else None
            if key is None:
                keys = by_name.get(name, [])
                if len(keys) == 1:
                    key = keys[0]
                elif len(keys) > 1:
                    held["place_of_writing_ambiguous"] += 1
                else:
                    held["place_of_writing_unmatched"] += 1
            if key:
                L.written_at(b, creation, place_uri[key])
                counts["written_at"] += 1

    # ── Nennungen und Verweise ───────────────────────────────────────────
    for m in q("SELECT letter_id, kind, key, n FROM letter_mentions"):
        d = doc_uri.get(m["letter_id"])
        if d is None:
            continue
        target = person_uri.get(m["key"]) if m["kind"] == "person" else place_uri.get(m["key"])
        if target is None:
            held["dangling_mentions"] += 1
            continue
        L.mention(b, d, target, f"{m['kind']}-{m['key']}", "Nennung im Text", m["n"], text_ds)
        counts["mentions_person" if m["kind"] == "person" else "mentions_place"] += 1
    seen = Counter()
    for ref in q("SELECT letter_id, target FROM letter_refs WHERE type = 'letter'"):
        a, z = doc_uri.get(ref["letter_id"]), doc_uri.get(ref["target"])
        if a is not None and z is not None and a != z and not seen[(a, z)]:
            b.mention(a, z)
            seen[(a, z)] += 1
            counts["letter_to_letter"] += 1

    b.activity("Escher-Export (KG-16)", commit=rep.commit or None, started=rep.built_at, ended=now())
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
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Briefe")
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
