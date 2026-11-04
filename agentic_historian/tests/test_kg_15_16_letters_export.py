"""KG-15 (ch-h-bot#632, B-6) und KG-16 (ch-h-bot#619, E-6): zwei Briefwechsel → Bestandsgraph.

Die Fixtures (``fixtures/bestandsgraph/{bullinger,escher}.json``) sind echte Zeilen der beiden
Provider-Datenbanken vom 04.11.2026, **nur Metadaten** — kein Brieftext (Bullinger steht
unter CC BY-NC-ND). Bullinger: die Briefe 88 (Edition), 2913 (HTR), 6120 (Transkription),
10064 (Edition mit Regest, an zwei Gruppen); Escher: drei Briefe Eschers von 1850 an
Personen mit HLS-Artikel, einer ohne Tagesdatum, einer mit zweitem Datum.

Gesichert wird: der Absender schreibt (P14 an der E65), der Empfänger ist eine Rolle, der
Schreibort P7, das Datum so genau wie die Quelle; die Herkunft des Briefs ist seine
Textklasse, die einer Nennung ihre Schicht; jede Kennung hat ihre Quelle, sameAs nur bei
Konfidenz 1 und bekanntem Schema, ein Konflikt steht beidseitig; SHACL ist grün; und die
Abnahmefrage von E-6 läuft.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from rdflib import Literal, URIRef, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_hub import bestandsgraph as bg             # noqa: E402
from knowledge_hub.ontology import registry               # noqa: E402
from knowledge_hub.sources import _letters as L           # noqa: E402
from knowledge_hub.sources import bullinger, escher       # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph"
CRM, PROV, OWL, SHB = registry.CRM, registry.PROV, registry.OWL, registry.SHB


def _db(tmp: Path, name: str, tables: dict) -> Path:
    """Die Tabellen der Fixture als SQLite-Datenbank; Spalten aus den Zeilen selbst."""
    d = json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))
    path = tmp / f"{name}.db"
    con = sqlite3.connect(path)
    for table, ddl in tables.items():
        con.execute(ddl)
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r.get(c) for c in cols) for r in d.get(table, [])])
    con.commit()
    con.close()
    return path


LINKS_DDL = ("CREATE TABLE person_links (link_id INTEGER PRIMARY KEY, person_key TEXT, scheme TEXT, value TEXT, "
             "url TEXT, asserted_by TEXT, confidence REAL, method TEXT, retrieved_at TEXT, conflicts_with INTEGER)")
B_TABLES = {
    "documents": "CREATE TABLE documents (id INTEGER PRIMARY KEY, type TEXT, source TEXT, text_class TEXT, title TEXT, "
                 "permalink TEXT, sender TEXT, sender_place TEXT, addressee TEXT, addressee_place TEXT, date_when TEXT, "
                 "date_from TEXT, date_to TEXT, date_not_before TEXT, date_not_after TEXT, date_cert TEXT, year INTEGER, "
                 "year_end INTEGER, lang TEXT)",
    "mentions": "CREATE TABLE mentions (document_id INTEGER, kind TEXT, ref TEXT, n INTEGER, in_regest INTEGER)",
    "person_refs": "CREATE TABLE person_refs (ref TEXT PRIMARY KEY, person_id TEXT)",
    "persons": "CREATE TABLE persons (id TEXT PRIMARY KEY, ref TEXT, surname TEXT, forename TEXT, name TEXT, aliases TEXT, "
               "gnd TEXT, wikipedia TEXT, hls TEXT, gender TEXT, n_sent INTEGER, n_received INTEGER, n_mentioned INTEGER)",
    "organizations": "CREATE TABLE organizations (id TEXT PRIMARY KEY, org_id TEXT, type TEXT, name TEXT, place TEXT)",
    "places": "CREATE TABLE places (id TEXT PRIMARY KEY, name TEXT, district TEXT, country TEXT, lat REAL, lon REAL, "
              "geonames TEXT, n_mentioned INTEGER)",
    "person_links": LINKS_DDL,
    "ingest_runs": "CREATE TABLE ingest_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, "
                   "source_commit TEXT, source_dir TEXT, counts TEXT, note TEXT)",
}
E_TABLES = {
    "letters": "CREATE TABLE letters (id TEXT PRIMARY KEY, b_id TEXT, title TEXT, sender_key TEXT, sender_name TEXT, "
               "addressee_key TEXT, addressee_name TEXT, roles_from TEXT, role_conflict INTEGER, place_name TEXT, "
               "date_when TEXT, date_when2 TEXT, date_text TEXT, year INTEGER, year_end INTEGER, lang TEXT, permalink TEXT)",
    "letter_mentions": "CREATE TABLE letter_mentions (letter_id TEXT, kind TEXT, key TEXT, name TEXT, n INTEGER)",
    "letter_refs": "CREATE TABLE letter_refs (letter_id TEXT, type TEXT, target TEXT, text TEXT)",
    "persons": "CREATE TABLE persons (xml_id TEXT PRIMARY KEY, key TEXT, forename TEXT, surname TEXT, type TEXT, "
               "birth TEXT, death TEXT, gnd TEXT, hls_url TEXT, hls_id TEXT, idnos TEXT, note TEXT, bibl TEXT, "
               "n_sent INTEGER, n_received INTEGER, n_mentioned INTEGER)",
    "places": "CREATE TABLE places (xml_id TEXT PRIMARY KEY, key TEXT, name TEXT, add_text TEXT, type TEXT, lat REAL, "
              "lon REAL, hls_url TEXT, hls_id TEXT, desc TEXT, n_mentioned INTEGER)",
    "person_links": LINKS_DDL,
    "ingest_runs": "CREATE TABLE ingest_runs (id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, "
                   "source_commit TEXT, source_dir TEXT, note TEXT, counts TEXT)",
}

BULLINGER_P = URIRef(str(bg.AH) + "bullinger/person/p495")


@pytest.fixture(scope="module")
def b_graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("kg15")
    path = _db(tmp, "bullinger", B_TABLES)
    con = sqlite3.connect(path)
    # Ein Konflikt von Hand, wie in KG-14: Metagrid nennt für Bullinger eine andere GND.
    own = con.execute("SELECT link_id FROM person_links WHERE person_key = 'P495' AND scheme = 'gnd' "
                      "AND asserted_by = 'bullinger'").fetchone()[0]
    cur = con.execute("INSERT INTO person_links (person_key, scheme, value, url, asserted_by, confidence, method, "
                      "retrieved_at, conflicts_with) VALUES ('P495', 'gnd', '999999999', 'https://d-nb.info/gnd/999999999', "
                      "'metagrid', 1.0, 'metagrid concordance', '2026-11-04T20:00:00Z', ?)", (own,))
    con.execute("UPDATE person_links SET conflicts_with = ? WHERE link_id = ?", (cur.lastrowid, own))
    con.commit()
    con.close()
    out = tmp / "bullinger.nt"
    rep = bullinger.export(path, out, validate=True, shacl=True, commit="test")
    g = bg.Build(bg.Dataset("x", "x", "x")).g
    g.parse(out, format="nt")
    return rep, g


@pytest.fixture(scope="module")
def e_graph(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("kg16")
    path = _db(tmp, "escher", E_TABLES)
    out = tmp / "escher.nt"
    rep = escher.export(path, out, validate=True, shacl=True, commit="test")
    g = bg.Build(bg.Dataset("x", "x", "x")).g
    g.parse(out, format="nt")
    return rep, g


def _doc(source: str, key: str) -> URIRef:
    return URIRef(f"{bg.AH}{source}/document/{bg.slug(key)}")


def _creation(doc: URIRef) -> URIRef:
    return URIRef(str(doc) + "/creation")


def _span(g, creation):
    ts = g.value(creation, CRM["P4_has_time-span"])
    return (g.value(ts, CRM["P82a_begin_of_the_begin"]), g.value(ts, CRM["P82b_end_of_the_end"]))


# ── KG-15 Bullinger ──────────────────────────────────────────────────────

def test_bullinger_is_valid_and_carries_no_text(b_graph):
    rep, g = b_graph
    assert rep.validation["ok"] and rep.validation.get("shacl")
    assert rep.counts["documents"] == 4
    longest = max((len(str(o)) for o in g.objects() if isinstance(o, Literal)), default=0)
    assert longest < 400, "kein Brieftext im Graphen, nur Metadaten (ND)"


def test_a_letter_is_written_by_its_sender_at_its_place_on_its_day(b_graph):
    _, g = b_graph
    d = _doc("bullinger", "6120")
    c = _creation(d)
    assert (d, RDF.type, CRM["E31_Document"]) in g
    assert (d, OWL["sameAs"], URIRef("https://www.bullinger-digital.ch/letter/6120")) in g
    assert (c, CRM["P14_carried_out_by"], URIRef(str(bg.AH) + "bullinger/person/p4037")) in g
    assert g.value(c, CRM["P7_took_place_at"]) == URIRef(str(bg.AH) + "bullinger/place/l93")
    assert _span(g, c) == (Literal("1564-04-28", datatype=XSD.date), Literal("1564-04-28", datatype=XSD.date))
    role = URIRef(f"{d}/role/empfaenger/p495")
    assert (role, CRM["P141_assigned"], BULLINGER_P) in g


def test_a_group_can_receive_and_a_place_can_be_where_it_arrived(b_graph):
    _, g = b_graph
    d = _doc("bullinger", "10064")
    receivers = {g.value(r, CRM["P141_assigned"]) for r in g.subjects(CRM["P140_assigned_attribute_to"], d)
                 if g.value(r, CRM["P177_assigned_property_of_type"]) and
                 str(g.value(g.value(r, CRM["P177_assigned_property_of_type"]), RDFS.label)) == "Empfänger"}
    assert receivers and all((u, RDF.type, CRM["E74_Group"]) in g for u in receivers)
    received_at = [r for r in g.subjects(CRM["P140_assigned_attribute_to"], _doc("bullinger", "2913"))
                   if str(g.value(g.value(r, CRM["P177_assigned_property_of_type"]), RDFS.label)) == "Empfangsort"]
    assert received_at and g.value(received_at[0], CRM["P141_assigned"]) == URIRef(str(bg.AH) + "bullinger/place/l587")


def test_the_letter_comes_from_its_text_class_and_a_mention_from_its_layer(b_graph):
    _, g = b_graph
    ds = lambda k: URIRef(f"{bg.AH}dataset/bullinger/{k}")  # noqa: E731
    assert g.value(_doc("bullinger", "2913"), PROV["wasDerivedFrom"]) == ds("htr")
    assert g.value(_doc("bullinger", "6120"), PROV["wasDerivedFrom"]) == ds("transcription")
    assert g.value(_doc("bullinger", "88"), PROV["wasDerivedFrom"]) == ds("edition")
    d = _doc("bullinger", "88")                      # an edition letter with a regest
    kinds = {}
    for m in g.subjects(CRM["P140_assigned_attribute_to"], d):
        t = g.value(m, CRM["P177_assigned_property_of_type"])
        label = str(g.value(t, RDFS.label))
        if label.startswith("Nennung"):
            kinds.setdefault(label, set()).add(g.value(m, PROV["wasDerivedFrom"]))
            assert (d, CRM["P67_refers_to"], g.value(m, CRM["P141_assigned"])) in g
    assert kinds["Nennung im Regest"] == {ds("regest")}
    assert kinds["Nennung im Text"] == {ds("edition")}


def test_every_identifier_has_its_source_and_a_conflict_points_both_ways(b_graph):
    rep, g = b_graph
    ids = list(g.objects(BULLINGER_P, CRM["P1_is_identified_by"]))
    gnd = [e for e in ids if str(g.value(g.value(e, CRM["P2_has_type"]), RDFS.label)) == "gnd"]
    assert len(gnd) == 2 and all(g.value(e, PROV["wasDerivedFrom"]) for e in gnd)
    a, z = gnd
    assert (a, SHB["conflictsWith"], z) in g and (z, SHB["conflictsWith"], a) in g
    assert (BULLINGER_P, OWL["sameAs"], URIRef("https://hls-dhs-dss.ch/de/articles/010443/")) in g
    wiki = [e for e in ids if str(g.value(g.value(e, CRM["P2_has_type"]), RDFS.label)) == "wikipedia"]
    assert wiki and not any(str(o).startswith("https://de.wikipedia") for o in g.objects(BULLINGER_P, OWL["sameAs"]))
    assert rep.counts["conflict_edges"] == 2


def test_bullinger_dates_are_as_exact_as_the_source():
    row = dict(date_when=None, date_from="1556-05", date_to=None, date_not_before=None, date_not_after=None,
               date_cert="low", year=1556, year_end=1556)
    assert bullinger.letter_dates(row) == ("1556-05-01", None, "Datierung: low")
    row.update(date_from=None, date_not_after="1556-06-30")
    assert bullinger.letter_dates(row) == (None, "1556-06-30", "Datierung: low")
    row.update(date_not_after=None, date_cert="high")
    assert bullinger.letter_dates(row) == ("1556-01-01", "1556-12-31", "nur das Jahr")
    assert L.bound("1564-02", "end") == "1564-02-29" and L.bound("x", "start") is None


# ── KG-16 Escher ─────────────────────────────────────────────────────────

def test_escher_is_valid_and_dates_follow_the_edition(e_graph):
    rep, g = e_graph
    assert rep.validation["ok"] and rep.validation.get("shacl")
    d = json.loads((FIX / "escher.json").read_text(encoding="utf-8"))
    two = next(r for r in d["letters"] if r["date_when2"])
    assert _span(g, _creation(_doc("escher", two["id"]))) == (
        Literal(two["date_when"], datatype=XSD.date), Literal(two["date_when2"], datatype=XSD.date))
    undated = next(r for r in d["letters"] if not r["date_when"])
    begin, end = _span(g, _creation(_doc("escher", undated["id"])))
    assert begin is None or str(begin).endswith("-01-01")


def test_the_e6_acceptance_query_runs(e_graph):
    """«Alle Briefe von Escher an Personen mit HLS-Artikel zwischen 1850 und 1860.»"""
    _, g = e_graph
    q = """
    PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
    PREFIX owl: <http://www.w3.org/2002/07/owl#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
    SELECT DISTINCT ?letter ?hls WHERE {
      ?creation crm:P94_has_created ?letter ; crm:P14_carried_out_by ?escher ;
                crm:P4_has_time-span/crm:P82a_begin_of_the_begin ?begin .
      ?escher crm:P1_is_identified_by/crm:P190_has_symbolic_content "Escher (vom Glas) Alfred" .
      ?role crm:P140_assigned_attribute_to ?letter ; crm:P141_assigned ?addr .
      ?addr owl:sameAs ?hls .
      FILTER(STRSTARTS(STR(?hls), "https://hls-dhs-dss.ch/"))
      FILTER(?begin >= "1850-01-01"^^xsd:date && ?begin <= "1860-12-31"^^xsd:date)
    }"""
    rows = list(g.query(q))
    assert len({r[0] for r in rows}) == 3


def test_metagrid_and_the_edition_are_separate_sources_and_conflicts_are_kept(e_graph):
    rep, g = e_graph
    srcs = {str(o) for e in g.subjects(RDF.type, CRM["E42_Identifier"]) for o in g.objects(e, PROV["wasDerivedFrom"])}
    assert any(s.endswith("/register-tei-idno") for s in srcs) and any("/metagrid-" in s for s in srcs)
    assert rep.counts["conflict_edges"] >= 2 and rep.counts["conflict_edges"] % 2 == 0
    assert rep.counts["letter_to_letter"] >= 1
