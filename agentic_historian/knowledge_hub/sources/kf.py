"""kf.py — KG-8 (#652): Königsfelden in den Bestandsgraphen.

    python -m knowledge_hub.sources.kf --db /home/tobias/mcp/data/kf.db --out build/kf.nt --validate

Liest ``kf.db`` (Schema in ``kf_mcp/build_db.py``; Edition «Die Urkunden und
Akten des Klosters und der Hofmeisterei Königsfelden 1300–1658»). Gemessen
15.10.2026: 1 557 Einträge (1 326 datiert), 43 401 Spans, 5 260 Personen (0 mit
HLS-Id, 512 mit Lebensdaten, 5 257 mit Familie), 1 333 Orte (237 HLS, 170 GND,
384 geo), 2 270 Organisationen.

**Was ein Verweis ist, und was nicht.** Ein Span mit ``ref`` ist nur dann eine
Erwähnung, wenn ``ref`` eine Register-Id ist — ``per``/``loc``/``org`` plus
sechs Ziffern — und das Register sie kennt. Die Messung hat gezeigt, warum das
nicht selbstverständlich ist: 5 856 ``persName``-Spans tragen als ``ref`` die
Kürzel der Bearbeiterinnen (``sste`` = Sabine Stettler, ``ch``, ``mn``, ``fh``,
``KF``, ``automatisch``) — Verantwortlichkeiten aus dem TEI-Kopf, keine
Erwähnungen im Text. Sie werden gezählt (``withheld.editorial_refs``) und
nicht zu Personen gemacht. 22 Verweise wie ``per02583`` (fünf Ziffern) und 43
wie ``loc00019`` treffen kein Register — ``dangling_refs``. Ein ``org002627\\t``
mit Tabulator wird gestrippt und trifft.

Mapping (docs/KG_BESTANDSGRAPH_MAPPING.md): Eintrag E31 mit E42 (Quelle
``kf``), Label = ``short_id``, Datierung an der E65 Creation; jeder Span mit
Register-Ziel P67, die Schreibung im Dokument als E41 (Typ ``orig``), die
normalisierte Form (``norm``) als E41 (Typ ``norm``); Person mit Namen als
E41 ``std``, Familie als C5 Membership + P107, Beruf als C13/C12, Geburt/Tod
E67/E69 so genau wie die Quelle («01.05.1218» ein Tag, «vor 07.04.1363» nur
eine Obergrenze, «1349/1350» eine Spanne, «1351?» ein Jahr mit Notiz); Ort
E53 mit geo, HLS, GND; Organisation E74 mit ``desc_de`` als Gruppentyp.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Optional

from rdflib import Literal, URIRef, RDF, XSD

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

DATASET = bg.Dataset(
    "kf", "Die Urkunden und Akten des Klosters und der Hofmeisterei Königsfelden (1300–1658)",
    "kf.db, 30.09.2026 (Editionsprojekt, TEI)")
HLS = "https://hls-dhs-dss.ch/de/articles/{id}/"
GND = "https://d-nb.info/gnd/{id}"
_REGISTER_ID = re.compile(r"^(per|loc|org)\d{6}$")
_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")
_YEAR = re.compile(r"^(\d{4})\??$")
_SPAN = re.compile(r"^(\d{4})/(\d{4})$")
_BEFORE = re.compile(r"^(vor|ante)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})$", re.I)
_AFTER = re.compile(r"^(nach|post)\s+(\d{1,2})\.(\d{1,2})\.(\d{4})$", re.I)


def life_date(raw: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(Anfang, Ende, Notiz) — so genau wie die Quelle, nie genauer.

    «01.05.1218» → ein Tag. «vor 07.04.1363» → nur eine Obergrenze. «nach …»
    → nur eine Untergrenze. «1349/1350» → eine Spanne. «1351?» → das Jahr,
    das Fragezeichen als Notiz. Alles andere → nur eine Notiz.
    """
    s = (raw or "").strip()
    if not s:
        return None, None, None
    m = _DMY.match(s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            date(y, mo, d)
        except ValueError:
            return f"{y:04d}-01-01", f"{y:04d}-12-31", f"unmöglicher Tag in der Quelle: {s}"
        return f"{y:04d}-{mo:02d}-{d:02d}", f"{y:04d}-{mo:02d}-{d:02d}", None
    m = _BEFORE.match(s)
    if m:
        d, mo, y = int(m.group(2)), int(m.group(3)), int(m.group(4))
        return None, f"{y:04d}-{mo:02d}-{d:02d}", f"Quelle: {s}"
    m = _AFTER.match(s)
    if m:
        d, mo, y = int(m.group(2)), int(m.group(3)), int(m.group(4))
        return f"{y:04d}-{mo:02d}-{d:02d}", None, f"Quelle: {s}"
    m = _SPAN.match(s)
    if m:
        return f"{int(m.group(1)):04d}-01-01", f"{int(m.group(2)):04d}-12-31", None
    m = _YEAR.match(s)
    if m:
        y = int(m.group(1))
        return f"{y:04d}-01-01", f"{y:04d}-12-31", (f"Quelle: {s}" if s.endswith("?") else None)
    return None, None, f"Quelle: {s}"


def _event_span(b: bg.Build, subject: URIRef, begin: Optional[str], end: Optional[str],
                note: Optional[str]) -> None:
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


def _geo(raw: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)\s*$", raw or "")
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def export(db: Path, out: Path, *, limit: Optional[int] = None, validate: bool = False,
           shacl: bool = False, commit: Optional[str] = None) -> ExportReport:
    t0 = time.monotonic()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    b = bg.Build(DATASET)
    rep = ExportReport(source="kf", built_at=now(), db=str(db), destination=str(out),
                       commit=commit or git_commit())
    counts = {"documents": 0, "persons": 0, "places": 0, "groups": 0,
              "mentions_person": 0, "mentions_place": 0, "mentions_group": 0,
              "names_orig": 0, "names_norm": 0, "memberships": 0, "occupations": 0,
              "births": 0, "deaths": 0}
    held: dict = {"dangling_refs": 0, "editorial_refs": 0, "editorial_by_prefix": {},
                  "undated_documents": 0, "missing_families": 0, "unparsed_spans": 0}

    persons = {r["id"]: r for r in q("SELECT * FROM persons").fetchall()}
    places = {r["id"]: r for r in q("SELECT * FROM places").fetchall()}
    orgs = {r["id"]: r for r in q("SELECT * FROM orgs").fetchall()}
    person_uri: dict[str, URIRef] = {}
    place_uri: dict[str, URIRef] = {}
    group_uri: dict[str, URIRef] = {}
    editorial = Counter()

    def group_node(oid: str) -> Optional[URIRef]:
        if oid in group_uri:
            return group_uri[oid]
        o = orgs.get(oid)
        if o is None:
            return None
        u = b.group(oid, (o["name"] or oid).strip(), group_type=(o["desc_de"] or "").strip() or None,
                    identifier=("kf", oid))
        group_uri[oid] = u
        counts["groups"] += 1
        return u

    def person_node(pid: str) -> Optional[URIRef]:
        if pid in person_uri:
            return person_uri[pid]
        p = persons.get(pid)
        if p is None:
            return None
        label = (p["main_name"] or p["full_name"] or " ".join(x for x in (p["forename"], p["surname"]) if x) or pid).strip()
        u = b.person(pid, label, identifier=("kf", pid))
        person_uri[pid] = u
        counts["persons"] += 1
        std = " ".join(x for x in (p["forename"], p["surname"]) if x).strip()
        if std and std != label:
            b.name(u, std, kind="std", key=f"{pid}-std")
        if p["org_ref"]:
            fam = group_node(p["org_ref"].strip())
            if fam is not None:
                b.membership(u, fam, "Familie"); counts["memberships"] += 1
            else:
                held["missing_families"] += 1
        if p["occupation"]:
            b.occupation(u, p["occupation"].strip()); counts["occupations"] += 1
        if p["birth"]:
            a, z, n = life_date(p["birth"])
            ev = b.birth(u); _event_span(b, ev, a, z, n); counts["births"] += 1
        if p["death"]:
            a, z, n = life_date(p["death"])
            ev = b.death(u); _event_span(b, ev, a, z, n); counts["deaths"] += 1
        if p["note"]:
            b.note(u, p["note"].strip())
        return u

    def place_node(lid: str) -> Optional[URIRef]:
        if lid in place_uri:
            return place_uri[lid]
        p = places.get(lid)
        if p is None:
            return None
        lat, lon = _geo(p["geo"])
        same = []
        if p["hls_id"]:
            same.append(HLS.format(id=str(p["hls_id"]).strip().zfill(6)))
        if p["gnd_id"]:
            same.append(GND.format(id=str(p["gnd_id"]).strip()))
        u = b.place(lid, (p["name_de"] or p["name_fr"] or "").strip() or None, lat=lat, lon=lon,
                    same_as=same, identifier=("kf", lid))
        if p["name_fr"] and p["name_fr"] != p["name_de"]:
            b.name(u, p["name_fr"].strip(), lang="fr", kind="std", key=f"{lid}-fr")
        place_uri[lid] = u
        counts["places"] += 1
        return u

    entries = q("SELECT id, title, short_id, year, pages FROM entries ORDER BY id").fetchall()
    if limit is not None:
        entries = entries[:limit]
    for e in entries:
        u = b.document(e["id"], (e["short_id"] or e["id"]).strip(), identifier=("kf", e["id"]),
                       begin=e["year"], end=e["year"])
        if e["title"] and e["title"].strip() != (e["short_id"] or "").strip():
            b.name(u, e["title"].strip(), kind="std", key=f"{e['id']}-title")
        if e["year"] is None:
            held["undated_documents"] += 1
        if e["pages"]:
            b.note(u, f"Seiten: {e['pages'].strip()}")
        counts["documents"] += 1
        spans = q("SELECT id, class, ref, text, norm FROM spans WHERE entry_id=? AND ref IS NOT NULL AND ref<>'' "
                  "ORDER BY id", (e["id"],)).fetchall()
        for s in spans:
            ref = (s["ref"] or "").strip()
            if not _REGISTER_ID.match(ref):
                if s["class"] == "persName" and re.match(r"^[A-Za-z?.]+$", ref):
                    held["editorial_refs"] += 1; editorial[ref] += 1
                else:
                    held["unparsed_spans"] += 1
                continue
            kind = ref[:3]
            target = (person_node if kind == "per" else place_node if kind == "loc" else group_node)(ref)
            if target is None:
                held["dangling_refs"] += 1
                continue
            b.mention(u, target)
            counts["mentions_person" if kind == "per" else "mentions_place" if kind == "loc" else "mentions_group"] += 1
            text = (s["text"] or "").strip()
            if text:
                b.name(target, text, kind="orig", key=f"{ref}-orig-{s['id']}"); counts["names_orig"] += 1
            if s["norm"] and s["norm"].strip():
                b.name(target, s["norm"].strip(), kind="norm", key=f"{ref}-norm-{s['id']}"); counts["names_norm"] += 1
    if limit is None:
        for pid in sorted(persons):
            person_node(pid)
        for lid in sorted(places):
            place_node(lid)
        for oid in sorted(orgs):
            group_node(oid)
    held["editorial_by_prefix"] = dict(editorial.most_common())
    b.activity("Königsfelden-Export (KG-8)", commit=rep.commit or None, started=rep.built_at, ended=now())
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
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Einträge")
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
