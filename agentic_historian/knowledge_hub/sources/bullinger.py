"""bullinger.py — KG-15 (ch-h-bot#632, B-6): der Briefwechsel Heinrich Bullingers im Bestandsgraphen.

    python -m knowledge_hub.sources.bullinger --db /home/tobias/mcp/data/bullinger.db --out build/bullinger.nt --validate

Liest ``bullinger.db`` (Schema in ``bullinger_mcp/build_db.py``, Kennungen aus
``link_metagrid.py``, B-5 #631; Quelle stazh/bullinger-korpus-tei, Texte CC BY-NC-ND 3.0 CH —
in den Graphen kommt **kein Text**, nur Metadaten, Register und Nennungen). Gemessen
04.11.2026: 13 114 Dokumente (12 162 Briefe), 6 043 Personen, 337 Organisationen,
3 077 Orte, 90 773 Personen- und 81 277 Ortsnennungen (je Brief und Schicht).

**Brief → E31** mit E42 (Quelle «bullinger», «Brief 6120»), ``owl:sameAs`` auf den Permalink,
Typ der Quelle (Brief, Beilage, Verweis …) als P2. Die E31 stammt (``prov:wasDerivedFrom``)
aus dem Teildatensatz ihrer **Textklasse** — Edition, Transkription, HTR, Metadaten —, denn
das ist die Herkunft dessen, was über den Brief gesagt wird. Das Schreiben ist die E65:
Absender P14 (Person E21 oder Gruppe E74 — «Geistliche von Zürich» schreibt auch), Schreibort
P7, Datum P4 so genau wie die Quelle (``date_when`` ein Tag; ``date_from``/``date_to`` eine
Spanne; ``date_not_before``/``date_not_after`` nur eine Grenze), die Sicherheit der Datierung
als Notiz, wo sie nicht «high» ist. Empfänger sind E13 «Empfänger» (``Build.role``), der
Empfangsort eine E13 «Empfangsort».

**Personen E21, Organisationen E74** mit Register-Id als E42, Varianten als E41 «alias». Jede
Kennung aus ``person_links`` ist eine Behauptung mit Herkunft (Register oder Metagrid), siehe
``_letters.assertion``; ohne die Tabelle gelten GND und HLS der Spalten als Behauptung des
Registers.

**Nennungen P67 mit Herkunft.** ``mentions`` zählt je Brief, Entität und Schicht (Text oder
Regest); jede Zeile wird P67 plus E13 «Nennung im Text»/«Nennung im Regest», abgeleitet aus der
Textklasse bzw. dem Regest. Die Issue-Formulierung «Satz» kann der Graph nicht einlösen: die
Datenbank zählt Nennungen je Brief, nicht je Satz — das steht im Bericht (``withheld``).
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

from rdflib import URIRef

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources import _letters as L
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

LABEL = "Bullinger Digital — Briefwechsel Heinrich Bullingers (1523–1575)"
REPO = "https://github.com/stazh/bullinger-korpus-tei"
CLASS_LABEL = {"edition": "Edition", "transcription": "Transkription", "htr": "HTR", "none": "Metadaten"}
SOURCES = {"bullinger": ("register", "Register von Bullinger Digital (persons.xml)"),
           "metagrid": ("metagrid", "Metagrid — Konkordanz je Person (api.metagrid.ch)")}


def _version(con: sqlite3.Connection, db: Path) -> str:
    try:
        row = con.execute("SELECT source_commit, finished_at FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    if not row:
        return db.name
    return f"bullinger.db, Ingest {row[1]} aus stazh/bullinger-korpus-tei@{(row[0] or '')[:12]} (Texte CC BY-NC-ND 3.0 CH)"


def _refs(raw: Optional[str]) -> list[str]:
    try:
        return [str(r) for r in json.loads(raw or "[]") if r]
    except (TypeError, ValueError):
        return []


def letter_dates(row) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(Anfang, Ende, Notiz) — ein Tag, eine Spanne oder nur Grenzen, nie genauer als die Quelle."""
    when, frm, to = row["date_when"], row["date_from"], row["date_to"]
    nb, na = row["date_not_before"], row["date_not_after"]
    if when:
        begin, end = L.bound(when, "start"), L.bound(when, "end")
    elif frm or to:
        begin, end = L.bound(frm, "start"), L.bound(to, "end")
    else:
        begin, end = L.bound(nb, "start"), L.bound(na, "end")
    cert = (row["date_cert"] or "").strip()
    note = f"Datierung: {cert}" if cert and cert != "high" else None
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
    b = bg.Build(bg.Dataset("bullinger", LABEL, _version(con, db), uri=REPO))
    rep = ExportReport(source="bullinger", built_at=now(), db=str(db), destination=str(out),
                       commit=commit or git_commit())
    counts = {"documents": 0, "persons": 0, "groups": 0, "places": 0, "aliases": 0,
              "authors": 0, "addressees": 0, "written_at": 0, "received_at": 0, "dated": 0,
              "mentions_person": 0, "mentions_place": 0, "same_as": 0, "same_as_hls": 0,
              "assertions": {}, "conflict_edges": 0}
    held = {"dangling_refs": 0, "undated_documents": 0, "mention_granularity": "Brief und Schicht, nicht Satz",
            "links_table": "gelesen" if L.has_table(con, "person_links") else "fehlt"}

    class_ds = {k: b.sub_dataset(k, f"{LABEL} — {v}") for k, v in CLASS_LABEL.items()}
    regest_ds = b.sub_dataset("regest", f"{LABEL} — Regesten der Edition (HBBW)")
    link_ds: dict = {}

    def source_of(by: str, method: str) -> URIRef:
        if (by, method) not in link_ds:
            key, label = SOURCES.get(by, (by, by))
            link_ds[(by, method)] = b.sub_dataset(f"{key}-{bg.slug(method)[:40]}", label, version=method)
        return link_ds[(by, method)]

    # ── Orte, Personen, Organisationen ───────────────────────────────────
    place_uri: dict[str, URIRef] = {}
    for p in q("SELECT * FROM places"):
        u = b.place(p["id"], p["name"], lat=p["lat"], lon=p["lon"], identifier=("bullinger", p["id"]))
        if p["geonames"]:
            b.identifier(u, "geonames", p["geonames"])
        where = ", ".join(x for x in (p["district"], p["country"]) if x and x != p["name"])
        if where:
            b.note(u, where)
        place_uri[p["id"]] = u
        counts["places"] += 1

    actor_uri: dict[str, URIRef] = {}          # ref («p495», «og26pl441») → E21/E74
    person_uri: dict[str, URIRef] = {}         # id («P495») → E21
    for p in q("SELECT * FROM persons ORDER BY id"):
        u = b.person(p["id"], p["name"] or p["id"], identifier=("bullinger", p["id"]))
        for alias in json.loads(p["aliases"] or "[]"):
            if alias and alias != p["name"]:
                b.name(u, alias, kind="alias", key=f"{p['id']}-alias-{alias}")
                counts["aliases"] += 1
        person_uri[p["id"]] = u
        counts["persons"] += 1
    for ref, pid in q("SELECT ref, person_id FROM person_refs"):
        if pid in person_uri:
            actor_uri[ref] = person_uri[pid]
    for o in q("SELECT * FROM organizations"):
        u = b.group(o["id"], o["name"] or o["id"], group_type=o["type"] or None, identifier=("bullinger", o["id"]))
        actor_uri[o["id"]] = u
        counts["groups"] += 1

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
        own = source_of("bullinger", "tei idno")
        for p in q("SELECT id, gnd, hls FROM persons WHERE gnd IS NOT NULL OR hls IS NOT NULL"):
            if p["gnd"]:
                L.assertion(b, person_uri[p["id"]], p["id"], "gnd", p["gnd"], "bullinger", 1.0, own, counts, held)
            m = re.search(r"/articles/(\d{6})", p["hls"] or "")
            hls = m.group(1) if m else None
            if hls:
                L.assertion(b, person_uri[p["id"]], p["id"], "hls", hls, "bullinger", 1.0, own, counts, held)

    # ── Briefe ───────────────────────────────────────────────────────────
    doc_uri: dict[int, URIRef] = {}
    rows = q("SELECT id, type, text_class, title, permalink, sender, sender_place, addressee, addressee_place, "
             "date_when, date_from, date_to, date_not_before, date_not_after, date_cert, year, year_end "
             "FROM documents ORDER BY id").fetchall()
    if limit is not None:
        rows = rows[:limit]
    for r in rows:
        ds = class_ds.get(r["text_class"], b.dataset_uri)
        d = b.document(str(r["id"]), r["title"] or f"Brief {r['id']}", same_as=[r["permalink"]],
                       identifier=("bullinger", f"Brief {r['id']}"), derived_from=ds)
        if r["type"]:
            b.g.add((d, bg.CRM["P2_has_type"], b.type_(r["type"], "document-type")))
        doc_uri[r["id"]] = d
        counts["documents"] += 1
        creation = b.creation(d, ds)
        begin, end, note = letter_dates(r)
        L.span(b, creation, begin, end, note)
        if begin or end:
            counts["dated"] += 1
        else:
            held["undated_documents"] += 1
        for ref in _refs(r["sender"]):
            actor = actor_uri.get(ref)
            if actor is None:
                held["dangling_refs"] += 1
                continue
            b.author(d, actor)
            counts["authors"] += 1
        for ref in _refs(r["addressee"]):
            actor = actor_uri.get(ref)
            if actor is None:
                held["dangling_refs"] += 1
                continue
            b.role(d, actor, "Empfänger")
            counts["addressees"] += 1
        for ref in _refs(r["sender_place"]):
            if ref in place_uri:
                L.written_at(b, creation, place_uri[ref])
                counts["written_at"] += 1
            else:
                held["dangling_refs"] += 1
        for ref in _refs(r["addressee_place"]):
            if ref in place_uri:
                b.role(d, place_uri[ref], "Empfangsort")
                counts["received_at"] += 1
            else:
                held["dangling_refs"] += 1

    # ── Nennungen ────────────────────────────────────────────────────────
    classes = {r["id"]: r["text_class"] for r in rows}
    for m in q("SELECT document_id, kind, ref, n, in_regest FROM mentions"):
        d = doc_uri.get(m["document_id"])
        if d is None:
            continue
        target = (actor_uri.get(m["ref"]) if m["kind"] == "person" else place_uri.get(m["ref"]))
        if target is None:
            held["dangling_refs"] += 1
            continue
        where = "Regest" if m["in_regest"] else "Text"
        src = regest_ds if m["in_regest"] else class_ds.get(classes[m["document_id"]], b.dataset_uri)
        L.mention(b, d, target, f"{m['kind']}-{m['ref']}-{where}", f"Nennung im {where}", m["n"], src)
        counts["mentions_person" if m["kind"] == "person" else "mentions_place"] += 1

    b.activity("Bullinger-Export (KG-15)", commit=rep.commit or None, started=rep.built_at, ended=now())
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
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Dokumente")
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
