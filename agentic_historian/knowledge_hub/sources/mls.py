"""mls.py — KG-13 (ch-h-bot#543, M-6): das Musiklexikon der Schweiz in den Bestandsgraphen.

    python -m knowledge_hub.sources.mls --db /home/tobias/mcp/data/mls.db --out build/mls.nt --validate

Liest ``mls.db`` (Schema in ``mls_mcp/build_db.py``; DaSCH-Projekt 0807, über die
DSP-API geerntet, CC BY-NC-SA 4.0). Gemessen 19.10.2026 am Stand des Ingests vom
16.10.2026: 27 Lexika, 6 839 Lemmata, 17 084 Artikel, 4 461 Orte, 1 946
Lemma–Ort-Bezüge, 239 Tätigkeiten, 4 998 Lemma–Tätigkeit-Bezüge.

**Lemma nach Lemmatyp** (``lemma_type``, Listenknoten): Person 6 757 → E21,
Institution 22 → E74, Ort 8 → E53, Sache 34 → E55 (ein Thema wie bei Dodis:
«Jazz», «Volkslied», «Glocken»). 18 Lemmata haben keinen Typ: 3 davon tragen
Familien-/Vorname oder Geschlecht und werden E21 (``persons_untyped``), die
übrigen 15 («Stadtmusiker Bern», «Orgelbauer», «Minnesänger») E55
(``topics_untyped``). Jedes Lemma: ``owl:sameAs`` auf seine DaSCH-ARK, auf GND
(883 Lemmata; eines nennt dieselbe Id zweimal, das ist eine) und VIAF (784). ISNI führt die Quelle
nicht — es gibt keine solche Spalte.

**Namen** als E41 mit Typ «Familienname» (6 767), «Vorname» (6 327),
«Pseudonym» (72), «Variante» (203 Lemmata, eine 204. Zeile ist leer; mehrere
Werte mit `` | `` getrennt,
ein Komma *innerhalb* eines Werts bleibt stehen — ob «Wonecker, Litavicus» ein
Name oder zwei ist, sagt die Quelle nicht).

**Anfangs-/Enddatum mit Info.** Gemessen: ``start_date_info`` leer 5 405 (9 mit
Datum), «geboren» 1 186, «um» 90, «nachgewiesen» 61, «erwähnt» 27, «vor» 18,
«unbekannt» 14 (ohne Datum), «getauft» 13, «nach» 9, «Geburt» 8, «geboren um»
2, je 1 «erstmals erwähnt», «erbaut um», «Inauguration», «Gründungsdatum»,
«Gründung», «Fondation». ``end_date_info`` leer 5 803 (145 mit Datum, davon
133 «-»), «gestorben» 884, «unbekannt» 67, «nach» 19, «nachgewiesen» 18, «vor»
13, «um» 12, «Tod» 8, «erwähnt» 7, «gestorben um» 4, je 1 «verstorben»,
«letztmals erwähnt», «ca.», «20.03.2010». Daraus, für Personen:

- Geburt E67 aus «geboren», «Geburt», «geboren um», «um», «vor», «nach»,
  «getauft»; Tod E69 aus «gestorben», «Tod», «verstorben», «gestorben um»,
  «um», «ca.», «vor», «nach». «um/vor/nach» stehen im Lexikon an den
  Lebensdaten («nach 1400 – vor 1499», «um 1486 – ca. 1542»): «vor» setzt nur
  die Obergrenze, «nach» nur die Untergrenze, «um» das Jahr mit Notiz, «getauft»
  die Obergrenze der Geburt — so genau wie die Quelle, nie genauer.
- Alles andere mit Datum ist eine Zuschreibung E13 an das Lemma, typisiert mit
  der Info: «nachgewiesen», «erwähnt», «erstmals/letztmals erwähnt» (die
  Erstnennung des Issues), bei Institutionen «Gründung» usw. — und ein Datum
  ohne Info als «Anfangsdatum ohne Angabe» / «Enddatum ohne Angabe». Ein
  Nicht-Personen-Lemma hat keine Geburt; seine Daten sind immer E13.
- «-» (134) und «unbekannt» ohne Datum (81) werden gezählt, nicht gebaut.

Ergebnis am Vollbestand: 1 327 E67, 944 E69, 142 E13.

**Orte mit Bezug** (``lemma_locations.relation``, gemessen): Tätigkeitsort
1 724, Geburtsort 133, Todesort 77, leer 11, Gründung 1. Geburts- und Todesort
sind ``P7_took_place_at`` an der E67/E69 — dafür hat CRM eine Property, und
weder P74 noch C3 wäre es. Tätigkeitsort (und die eine «Gründung» einer
Institution) wird ``P74_has_current_or_former_residence``: der nächste Begriff
im Register für «Ort, an dem ein Akteur ansässig war/wirkte», bei einer Gruppe
ihr Sitz. C3 nicht: in SDHSS verbindet C3 zwei Personen (P17/P18 → E21), ein
Ort ist kein Ziel einer sozialen Beziehung. Bezüge ohne Art (11) und
Tätigkeitsorte an Ort-Lemmata (3: ein Ort hat keinen Wohnort) werden gezählt,
nicht gebaut. Drei Zeilen sind doppelt (1 946 Zeilen, 1 943 verschiedene).
Ergebnis: 133 Geburtsorte, 77 Todesorte, 1 719 P74.

**Tätigkeiten** als C13/C12: 4 998 Zeilen, 4 980 an Personen (4 962
verschiedene Paare = 4 962 C13); 18 hängen an Lemmata, die keine Person sind
(«Stadtmusiker Bern», Verlage, ein Ort) — gezählt, nicht gebaut (C13 → E21).

**Artikel** E31 mit ``P67`` auf ihr Lemma, Label «Lemma (Lexikon)», die
MLS-Artikelnummer als E42, die ARK als ``owl:sameAs``. Herkunft ist das
**Lexikon** des Artikels: 27 ``prov:Entity`` (``Build.sub_dataset``), jedes mit
seiner Zitierform als ``rdfs:comment`` und ``prov:wasDerivedFrom`` auf den
Bestand. Die Creation des Artikels trägt das Erscheinungsjahr des Lexikons
(23 der 27 haben eines, «Becker 1874» ist das einzige des 19. Jahrhunderts;
10 944 Artikel datiert); ohne Jahr bleibt der Artikel undatiert (6 140: MLS
Online, RISM Online, DT Online). Der Bearbeitungsstand (``state``) und
der HLS-Verweis (``is_reference``, 720) sind E55 an ``P2``. Der Artikeltext
geht nicht in den Graphen.

**HLS.** 722 Artikel tragen einen ``hls_code``; 721 davon haben die Form
``D``/``F`` + 4–6 Ziffern (666 mit fünf, 51 mit vier, 1 mit sechs, 2 mit
Zeilenumbruch, 1 «F47154»), eines ist «-none-». Geprüft gegen das HLS
(D9967 → 009967 Gall Morel, F47154 → 047154 Claude Dubuis, D46111 → 046111
Eduard Munzinger): die Ziffern, auf sechs Stellen aufgefüllt, sind die
HLS-Artikel-Id. Das Lemma bekommt ``owl:sameAs
https://hls-dhs-dss.ch/de/articles/<id>/`` — dieselbe Form wie KF und SSRQ.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path
from typing import Optional

from rdflib import Literal, URIRef, RDF, XSD

from knowledge_hub import bestandsgraph as bg
from knowledge_hub.sources._common import (
    ExportReport, git_commit, now, validate_graph, write_manifest, write_sorted_ntriples,
)

LABEL = "Musiklexikon der Schweiz"
HLS = "https://hls-dhs-dss.ch/de/articles/{id}/"
GND = "https://d-nb.info/gnd/{id}"
VIAF = "https://viaf.org/viaf/{id}"
SEP = " | "
_HLS_CODE = re.compile(r"^[DF](\d{4,6})$")
_DMY = re.compile(r"^(\?|\d{1,2})\.(\?|\d{1,2})\.(\d{3,4})$")
_YEAR = re.compile(r"^(\d{3,4})$")

#: Infos, die am Anfangsdatum einer Person die Geburt meinen — und wie.
BIRTH = {"geboren": "exact", "Geburt": "exact", "geboren um": "um", "um": "um",
         "vor": "vor", "nach": "nach", "getauft": "vor"}
#: … und am Enddatum den Tod.
DEATH = {"gestorben": "exact", "Tod": "exact", "verstorben": "exact", "gestorben um": "um",
         "um": "um", "ca.": "um", "vor": "vor", "nach": "nach"}
#: Infos, die selbst sagen, was das Datum ist — eine E13 mit diesem Typ.
ASSIGNED = {"nachgewiesen", "erwähnt", "erstmals erwähnt", "letztmals erwähnt",
            "Gründung", "Gründungsdatum", "Fondation", "Inauguration", "erbaut um",
            "geboren", "Geburt", "geboren um", "getauft", "gestorben", "Tod",
            "verstorben", "gestorben um"}


def _key(iri: str) -> str:
    """Der Schlüssel aus der DaSCH-IRI (``http://rdfh.ch/0807/<id>``)."""
    return iri.rstrip("/").rsplit("/", 1)[-1]


def _split(value: Optional[str]) -> list[str]:
    return [v.strip() for v in (value or "").split(SEP) if v.strip()]


def parse_date(raw: Optional[str]) -> Optional[tuple[str, str, Optional[str]]]:
    """(Anfang, Ende, Notiz) einer Datumsangabe der Quelle, oder None.

    «24.06.1831» und «9.4.1923» → der Tag; «?.02.1783» → der Monat; «?.?.1991»
    und «1831» → das Jahr; dreistellige Jahre («980») gelten. Ein unmöglicher
    Tag wird zum Jahr mit Notiz. «-» und alles andere → None.
    """
    s = (raw or "").strip()
    m = _DMY.match(s)
    if m:
        d, mo, y = m.group(1), m.group(2), int(m.group(3))
        if mo == "?":
            return f"{y:04d}-01-01", f"{y:04d}-12-31", None
        mo_i = int(mo)
        if not 1 <= mo_i <= 12:
            return f"{y:04d}-01-01", f"{y:04d}-12-31", f"unmöglicher Monat in der Quelle: {s}"
        if d == "?":
            last = (date(y + (mo_i == 12), mo_i % 12 + 1, 1) - date(y, mo_i, 1)).days
            return f"{y:04d}-{mo_i:02d}-01", f"{y:04d}-{mo_i:02d}-{last:02d}", None
        try:
            date(y, mo_i, int(d))
        except ValueError:
            return f"{y:04d}-01-01", f"{y:04d}-12-31", f"unmöglicher Tag in der Quelle: {s}"
        iso = f"{y:04d}-{mo_i:02d}-{int(d):02d}"
        return iso, iso, None
    m = _YEAR.match(s)
    if m:
        y = int(m.group(1))
        return f"{y:04d}-01-01", f"{y:04d}-12-31", None
    return None


def qualified(raw: str, mode: str, info: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Das Datum, wie die Info es einschränkt: «vor» nur Obergrenze, «nach» nur
    Untergrenze, «um» das Jahr mit Notiz. Unlesbares → nur eine Notiz."""
    parsed = parse_date(raw)
    if parsed is None:
        return None, None, f"Quelle: {info} {raw.strip()}".strip()
    a, z, note = parsed
    if mode == "exact":
        return a, z, note
    src = f"Quelle: {info} {raw.strip()}"
    if mode == "vor":
        return None, z, src
    if mode == "nach":
        return a, None, src
    return a, z, src          # «um»


def _span(b: bg.Build, subject: URIRef, begin: Optional[str], end: Optional[str],
          note: Optional[str]) -> None:
    """Die Zeitspanne als E52 an ``subject`` — Grenzen als xsd:date."""
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


def _assignment(b: bg.Build, subject: URIRef, which: str, kind: str,
                begin: Optional[str], end: Optional[str], note: Optional[str]) -> URIRef:
    """E13: «das Lexikon schreibt dem Lemma dieses Datum mit dieser Info zu»
    (nachgewiesen, erwähnt, Gründung …). ``which`` ist ``start`` oder ``end``."""
    u = URIRef(f"{subject}/date/{which}")
    b.g.add((u, RDF.type, bg.CRM["E13_Attribute_Assignment"]))
    b.g.add((u, bg.CRM["P140_assigned_attribute_to"], subject))
    b.g.add((u, bg.CRM["P177_assigned_property_of_type"], b.type_(kind, "date-info")))
    b.g.add((u, bg.PROV["wasDerivedFrom"], b.dataset_uri))
    _span(b, u, begin, end, note)
    return u


def _version(con: sqlite3.Connection, db: Path) -> str:
    try:
        row = con.execute("SELECT finished_at FROM ingest_runs WHERE finished_at IS NOT NULL "
                          "ORDER BY finished_at DESC LIMIT 1").fetchone()
    except sqlite3.Error:
        row = None
    when = f"Ingest {row[0]}" if row and row[0] else db.name
    return f"mls.db, {when} (DaSCH-Projekt 0807, DSP-API, CC BY-NC-SA 4.0)"


def export(db: Path, out: Path, *, limit: Optional[int] = None, validate: bool = False,
           shacl: bool = False, commit: Optional[str] = None) -> ExportReport:
    t0 = time.monotonic()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    q = con.execute
    b = bg.Build(bg.Dataset("mls", LABEL, _version(con, db)))
    rep = ExportReport(source="mls", built_at=now(), db=str(db), destination=str(out),
                       commit=commit or git_commit())
    counts = {"lexica": 0, "articles": 0, "articles_dated": 0, "articles_reference": 0,
              "articles_with_text": 0, "mentions": 0,
              "persons": 0, "persons_untyped": 0, "groups": 0, "places_lemma": 0,
              "topics": 0, "topics_untyped": 0, "places": 0,
              "names_family": 0, "names_given": 0, "names_pseudonym": 0, "names_variant": 0,
              "same_as_gnd": 0, "same_as_viaf": 0, "same_as_hls": 0, "lemmata_with_hls": 0,
              "births": 0, "deaths": 0, "date_assignments": 0,
              "birth_places": 0, "death_places": 0, "residences": 0,
              "occupation_links": 0, "occupations": 0}
    held = {"dash_dates": 0, "unknown_dates": 0, "hls_codes_unparsed": 0,
            "hls_codes_conflicting": 0, "relations_without_kind": 0,
            "relations_on_non_actors": 0, "occupations_on_non_persons": 0,
            "sub_lemmata": 0, "undated_articles": 0, "isni": "nicht in der Quelle"}

    nodes = {r["iri"]: r["label"] for r in q("SELECT iri, label FROM list_nodes")}
    lemma_types: dict[str, int] = {}      # gemessen, wie die Quelle typisiert («—» = ohne Typ)

    # ── Lexika: je eines ein Datensatz ────────────────────────────────────
    lexicon_uri: dict[str, URIRef] = {}
    lexicon_label: dict[str, str] = {}
    lexicon_year: dict[str, Optional[int]] = {}
    for x in q("SELECT * FROM lexica ORDER BY iri"):
        name = (x["label"] or x["shortname"] or _key(x["iri"])).strip()
        year = (x["year"] or "").strip()
        u = b.sub_dataset(_key(x["iri"]), f"{LABEL} — {name}",
                          version=year or None, uri=x["ark"],
                          comment=(x["citation_form"] or "").strip() or None)
        lexicon_uri[x["iri"]] = u
        lexicon_label[x["iri"]] = name
        lexicon_year[x["iri"]] = int(year) if year.isdigit() else None
        counts["lexica"] += 1

    # ── Orte (Tabelle locations) ──────────────────────────────────────────
    place_uri: dict[str, URIRef] = {}
    for loc in q("SELECT * FROM locations ORDER BY iri"):
        k = _key(loc["iri"])
        u = b.place(k, (loc["placename"] or "").strip() or None,
                    same_as=[loc["ark"]] if loc["ark"] else [],
                    identifier=("mls", loc["label"]) if loc["label"] else None)
        where = ", ".join(v.strip() for v in (loc["canton"], loc["country"]) if v and v.strip())
        if where:
            b.note(u, f"Kanton/Land: {where}")
        if loc["comment"]:
            b.note(u, loc["comment"].strip())
        place_uri[loc["iri"]] = u
        counts["places"] += 1

    # ── HLS-Codes je Lemma, aus den Artikeln ──────────────────────────────
    hls_of: dict[str, set[str]] = {}
    for a in q("SELECT lemma_iri, hls_code FROM articles WHERE hls_code IS NOT NULL AND hls_code <> ''"):
        m = _HLS_CODE.match(a["hls_code"].strip())
        if not m:
            held["hls_codes_unparsed"] += 1
            continue
        hls_of.setdefault(a["lemma_iri"], set()).add(m.group(1).zfill(6))
    held["hls_codes_conflicting"] = sum(1 for v in hls_of.values() if len(v) > 1)

    # ── Lemmata ───────────────────────────────────────────────────────────
    lemma_uri: dict[str, URIRef] = {}
    lemma_kind: dict[str, str] = {}
    lemma_label: dict[str, str] = {}
    births: set[URIRef] = set()
    deaths: set[URIRef] = set()
    for lm in q("SELECT * FROM lemmata ORDER BY iri"):
        k = _key(lm["iri"])
        label = (lm["label"] or lm["lemma_text"] or k).strip()
        ltype = nodes.get(lm["lemma_type"]) if lm["lemma_type"] else None
        lemma_types[ltype or "—"] = lemma_types.get(ltype or "—", 0) + 1
        same = [lm["ark"]] if lm["ark"] else []
        gnds = [GND.format(id=g) for g in dict.fromkeys(_split(lm["gnd"]))]
        viafs = [VIAF.format(id=v) for v in dict.fromkeys(_split(lm["viaf"]))]
        hls = [HLS.format(id=h) for h in sorted(hls_of.get(lm["iri"], ()))]
        same += gnds + viafs + hls
        if ltype is None:
            ltype = "Person" if (lm["family_name"] or lm["given_name"] or lm["sex"]) else "Sache"
            counts["persons_untyped" if ltype == "Person" else "topics_untyped"] += 1
        if ltype == "Person":
            u = b.person(k, label, same_as=same)
            counts["persons"] += 1
            if lm["sex"] and nodes.get(lm["sex"]):
                b.g.add((u, bg.CRM["P2_has_type"], b.type_(nodes[lm["sex"]], "sex")))
        elif ltype == "Institution":
            u = b.group(k, label, same_as=same)
            counts["groups"] += 1
        elif ltype == "Ort":
            u = b.place(k, label, same_as=same)
            counts["places_lemma"] += 1
        else:                                           # «Sache»
            u = b.topic(k, label, same_as=same)
            counts["topics"] += 1
        counts["same_as_gnd"] += len(gnds)
        counts["same_as_viaf"] += len(viafs)
        counts["same_as_hls"] += len(hls)
        counts["lemmata_with_hls"] += bool(hls)
        lemma_uri[lm["iri"]] = u
        lemma_kind[lm["iri"]] = ltype
        lemma_label[lm["iri"]] = label
        # Namen
        for col, kind, cnt in (("family_name", "Familienname", "names_family"),
                               ("given_name", "Vorname", "names_given"),
                               ("pseudonym", "Pseudonym", "names_pseudonym"),
                               ("variants", "Variante", "names_variant")):
            for i, text in enumerate(_split(lm[col])):
                b.name(u, text, kind=kind, key=f"{k}-{bg.slug(kind)}-{i}")
            counts[cnt] += bool(_split(lm[col]))
        if lm["comment"] and lm["comment"].strip():
            b.note(u, lm["comment"].strip())
        if lm["sub_lemma_iri"]:
            held["sub_lemmata"] += 1
        # Daten
        for which, raw, info, table in (("start", lm["start_date"], lm["start_date_info"], BIRTH),
                                        ("end", lm["end_date"], lm["end_date_info"], DEATH)):
            raw = (raw or "").strip()
            info = (info or "").strip()
            if raw in ("", "-"):
                if raw == "-":
                    held["dash_dates"] += 1
                elif info:
                    held["unknown_dates"] += 1
                continue
            if ltype == "Person" and info in table:
                a, z, n = qualified(raw, table[info], info)
                ev = b.birth(u) if which == "start" else b.death(u)
                (births if which == "start" else deaths).add(ev)
                _span(b, ev, a, z, n)
                continue
            parsed = parse_date(raw)
            a, z, n = parsed if parsed else (None, None, f"Quelle: {raw}")
            if info in ASSIGNED:
                kind = info
            elif not info:
                kind = "Anfangsdatum ohne Angabe" if which == "start" else "Enddatum ohne Angabe"
            else:
                kind = "Anfangsdatum" if which == "start" else "Enddatum"
                n = "; ".join(x for x in (n, f"Angabe: {info}") if x)
            _assignment(b, u, which, kind, a, z, n)
            counts["date_assignments"] += 1

    # ── Lemma–Ort ─────────────────────────────────────────────────────────
    seen_rel: set[tuple] = set()
    for ll in q("SELECT * FROM lemma_locations ORDER BY iri"):
        rel = (ll["relation"] or "").strip()
        lu, pu = lemma_uri.get(ll["lemma_iri"]), place_uri.get(ll["location_iri"])
        kind = lemma_kind.get(ll["lemma_iri"])
        if lu is None or pu is None:
            continue
        if not rel:
            held["relations_without_kind"] += 1
            continue
        if (ll["lemma_iri"], ll["location_iri"], rel) in seen_rel:
            continue
        seen_rel.add((ll["lemma_iri"], ll["location_iri"], rel))
        if rel in ("Geburtsort", "Todesort") and kind == "Person":
            ev = b.birth(lu, place=pu) if rel == "Geburtsort" else b.death(lu, place=pu)
            (births if rel == "Geburtsort" else deaths).add(ev)
            counts["birth_places" if rel == "Geburtsort" else "death_places"] += 1
        elif kind in ("Person", "Institution"):
            b.residence(lu, pu)
            counts["residences"] += 1
        else:
            held["relations_on_non_actors"] += 1
    counts["births"], counts["deaths"] = len(births), len(deaths)

    # ── Tätigkeiten ───────────────────────────────────────────────────────
    occupations = {r["iri"]: (r["occupation"] or "").strip() for r in q("SELECT iri, occupation FROM occupations")}
    embodiments: set[URIRef] = set()
    for lo in q("SELECT * FROM lemma_occupations ORDER BY iri"):
        role = occupations.get(lo["occupation_iri"])
        lu = lemma_uri.get(lo["lemma_iri"])
        if not role or lu is None:
            continue
        if lemma_kind.get(lo["lemma_iri"]) != "Person":
            held["occupations_on_non_persons"] += 1
            continue
        embodiments.add(b.occupation(lu, role, note=(lo["comment"] or "").strip() or None))
        counts["occupation_links"] += 1
    counts["occupations"] = len(embodiments)

    # ── Artikel ───────────────────────────────────────────────────────────
    articles = q("SELECT iri, ark, label, lemma_iri, lexicon_iri, text, short_info, pages, web_link, "
                 "state, is_reference FROM articles ORDER BY iri").fetchall()
    if limit is not None:
        articles = articles[:limit]
    for a in articles:
        lex = a["lexicon_iri"]
        year = lexicon_year.get(lex)
        title = f"{lemma_label.get(a['lemma_iri'], a['label'] or _key(a['iri']))} ({lexicon_label.get(lex, '?')})"
        u = b.document(_key(a["iri"]), title, same_as=[a["ark"]] if a["ark"] else [],
                       identifier=("mls", a["label"]) if a["label"] else None,
                       begin=year, end=year, derived_from=lexicon_uri.get(lex))
        counts["articles"] += 1
        counts["articles_dated"] += year is not None
        held["undated_articles"] += year is None
        if a["is_reference"]:
            b.g.add((u, bg.CRM["P2_has_type"], b.type_("HLS-Verweis", "document-type")))
            counts["articles_reference"] += 1
        state = (a["state"] or "").strip()
        if state and state != "-":
            b.g.add((u, bg.CRM["P2_has_type"], b.type_(state, "editorial-state")))
        if a["text"] and a["text"].strip():
            counts["articles_with_text"] += 1
        if a["pages"] and a["pages"].strip() not in ("", "-"):
            b.note(u, f"Seiten: {a['pages'].strip()}")
        if a["short_info"] and a["short_info"].strip():
            b.note(u, a["short_info"].strip())
        if a["web_link"] and a["web_link"].strip():
            b.note(u, f"Link: {a['web_link'].strip()}")
        target = lemma_uri.get(a["lemma_iri"])
        if target is not None:
            b.mention(u, target)
            counts["mentions"] += 1

    b.activity("Musiklexikon-der-Schweiz-Export (KG-13)", commit=rep.commit or None,
               started=rep.built_at, ended=now())
    con.close()
    _no_collisions(b, counts, limit)
    counts["lemma_types"] = dict(sorted(lemma_types.items(), key=lambda kv: (-kv[1], kv[0])))
    rep.counts = counts
    rep.withheld = held
    if validate:
        rep.validation = validate_graph(b.g, shacl=shacl)
    rep.triples = write_sorted_ntriples(b.g, out)
    rep.seconds = time.monotonic() - t0
    write_manifest(rep, out)
    return rep


def _no_collisions(b: bg.Build, counts: dict, limit: Optional[int]) -> None:
    """Die DaSCH-Ids unterscheiden Gross- und Kleinschreibung, ``slug`` nicht.
    Zwei Ids, die auf dieselbe URI fielen, würden still zu einem Knoten — hier
    wird gezählt, dass das nicht geschehen ist."""
    ns = str(b.ns) + "mls/"
    def n(kind: str, cls: str) -> int:
        return sum(1 for s in b.g.subjects(RDF.type, bg.CRM[cls]) if str(s).startswith(ns + kind + "/"))
    expected = {("person", "E21_Person"): counts["persons"], ("group", "E74_Group"): counts["groups"],
                ("place", "E53_Place"): counts["places"] + counts["places_lemma"],
                ("topic", "E55_Type"): counts["topics"]}
    if limit is None:
        expected[("document", "E31_Document")] = counts["articles"]
    for (kind, cls), want in expected.items():
        got = n(kind, cls)
        if got != want:
            raise RuntimeError(f"URI-Kollision bei {kind}: {want} Zeilen, {got} Knoten")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None, help="nur die ersten n Artikel")
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
