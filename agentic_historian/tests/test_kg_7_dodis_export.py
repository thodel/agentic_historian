"""KG-7 (#651): Dodis in den Bestandsgraphen — gegen eine dodis.db aus der Fixture.

Die Fixture (``tests/fixtures/bestandsgraph/dodis.json``, fünf Dokumente mit
allem, was daran hängt, Seed 20261014) wird hier in eine SQLite mit dem
Schema aus ``dodis_mcp/build_db.py`` geschrieben, damit der Export dieselben
Tabellen liest wie in der Produktion.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_7_dodis_export.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef, RDF, Literal

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub.ontology import registry           # noqa: E402
from knowledge_hub.sources import dodis               # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph" / "dodis.json"

SCHEMA = """
CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT, summary TEXT, doc_date TEXT, year INTEGER,
    lang_code TEXT, doc_type TEXT, classification TEXT, is_scan INTEGER);
CREATE TABLE persons (id TEXT PRIMARY KEY, last_name TEXT, first_name TEXT, name TEXT);
CREATE TABLE places (id TEXT PRIMARY KEY, name TEXT, country_code TEXT, latitude REAL, longitude REAL, wikidata_id TEXT);
CREATE TABLE tags (id TEXT PRIMARY KEY, name TEXT);
CREATE TABLE document_persons (document_id TEXT, person_id TEXT, relationship_type TEXT NOT NULL);
CREATE TABLE document_places (document_id TEXT, place_id TEXT);
CREATE TABLE document_tags (document_id TEXT, tag_id TEXT);
"""


def _db(tmp_path: Path, mutate=None) -> Path:
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(d)
    path = tmp_path / "dodis.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r["id"], r["title"], r.get("summary"), r["doc_date"], r["year"], r.get("lang_code"),
                      r["doc_type"], r.get("classification"), r.get("is_scan")) for r in d["documents"]])
    con.executemany("INSERT INTO persons VALUES (?,?,?,?)",
                    [(r["id"], r["last_name"], r["first_name"], r["name"]) for r in d["persons"]])
    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?)",
                    [(r["id"], r["name"], r["country_code"], r["latitude"], r["longitude"], r["wikidata_id"])
                     for r in d["places"]])
    con.executemany("INSERT INTO tags VALUES (?,?)", [(r["id"], r["name"]) for r in d["tags"]])
    con.executemany("INSERT INTO document_persons VALUES (?,?,?)",
                    [(r["document_id"], r["person_id"], r["relationship_type"]) for r in d["document_persons"]])
    con.executemany("INSERT INTO document_places VALUES (?,?)",
                    [(r["document_id"], r["place_id"]) for r in d["document_places"]])
    con.executemany("INSERT INTO document_tags VALUES (?,?)",
                    [(r["document_id"], r["tag_id"]) for r in d["document_tags"]])
    con.commit(); con.close()
    return path


def _sql(path: Path, q: str):
    con = sqlite3.connect(path)
    try:
        return con.execute(q).fetchone()[0]
    finally:
        con.close()


@pytest.fixture()
def built(tmp_path):
    db = _db(tmp_path)
    out = tmp_path / "dodis.nt"
    rep = dodis.export(db, out, validate=True, shacl=True)
    g = Graph().parse(str(out), format="nt")
    return db, out, rep, g


# ── Zählungen = SQL ─────────────────────────────────────────────────────────

def test_die_zaehlungen_im_manifest_sind_die_sql_zaehlungen(built):
    db, out, rep, g = built
    assert rep.counts["documents"] == _sql(db, "SELECT count(*) FROM documents")
    assert rep.counts["persons"] == _sql(db, "SELECT count(*) FROM persons")
    assert rep.counts["places"] == _sql(db, "SELECT count(*) FROM places")
    assert rep.counts["topics"] == _sql(db, "SELECT count(*) FROM tags")
    assert rep.counts["mentions_person"] == _sql(db, "SELECT count(*) FROM document_persons")
    assert rep.counts["authors"] == _sql(db, "SELECT count(*) FROM document_persons WHERE relationship_type='author'")
    assert rep.counts["roles"] == _sql(db, "SELECT count(*) FROM document_persons WHERE relationship_type IN ('signatory','adressee','address_of_copy')")
    assert rep.counts["mentions_place"] == _sql(db, "SELECT count(*) FROM document_places")
    assert rep.counts["about"] == _sql(db, "SELECT count(*) FROM document_tags")
    assert not any(rep.withheld.values()), rep.withheld
    # Und die Zahlen stehen auch im Graphen, nicht nur im Bericht.
    assert len(set(g.subjects(RDF.type, registry.CRM["E31_Document"]))) == rep.counts["documents"]
    assert len(set(g.subjects(RDF.type, registry.CRM["E21_Person"]))) == rep.counts["persons"]
    assert len(set(g.subjects(RDF.type, registry.CRM["E13_Attribute_Assignment"]))) == rep.counts["roles"]
    assert (out.parent / "dodis.nt.manifest.json").exists()


def test_der_graph_ist_valide_mit_shacl(built):
    _, _, rep, _ = built
    assert rep.validation["ok"], rep.validation
    assert rep.validation["shacl"] is True


def test_zweimal_gebaut_ist_byteidentisch_bis_auf_die_bauzeit(tmp_path):
    """Alles ausser den zwei Zeitstempeln der prov:Activity — die sagen, wann
    gebaut wurde, und sollen sich unterscheiden. Auf idhefix gemessen: der
    zweite Vollbau unterschied sich vom ersten in genau diesen Zeilen (und in
    den zwei korrigierten Februar-Daten)."""
    db = _db(tmp_path)
    a = tmp_path / "a.nt"; b = tmp_path / "b.nt"
    dodis.export(db, a); dodis.export(db, b)
    strip = lambda p: [l for l in p.read_text(encoding="utf-8").splitlines()  # noqa: E731
                       if "prov#startedAtTime" not in l and "prov#endedAtTime" not in l]
    assert strip(a) == strip(b)
    assert len(strip(a)) == len(a.read_text(encoding="utf-8").splitlines()) - 2


# ── Das Mapping im Einzelnen ────────────────────────────────────────────────

def test_wikidata_ids_bekommen_ihr_q(built):
    """Dodis führt «30» für Q30. Ein owl:sameAs auf …/entity/30 wäre eine
    URI, die es nicht gibt."""
    _, _, _, g = built
    same = {str(o) for o in g.objects(None, registry.OWL["sameAs"]) if "wikidata" in str(o)}
    assert same and all("/entity/Q" in u for u in same), same


def test_die_datierung_ist_so_genau_wie_die_quelle(built):
    db, _, _, g = built
    dates = {}
    for ts in g.subjects(RDF.type, registry.CRM["E52_Time-Span"]):
        a = next(g.objects(ts, registry.CRM["P82a_begin_of_the_begin"]))
        z = next(g.objects(ts, registry.CRM["P82b_end_of_the_end"]))
        dates[str(ts)] = (str(a), str(z))
    # «12.6.1967» → ein Tag; «2005» → ein Jahr
    assert ("1967-06-12", "1967-06-12") in dates.values()
    assert ("2005-01-01", "2005-12-31") in dates.values()
    # und keine am Dokument selbst
    for d in g.subjects(RDF.type, registry.CRM["E31_Document"]):
        assert (d, registry.CRM["P4_has_time-span"], None) not in g


def test_jedes_dokument_zeigt_auf_dodis_ch(built):
    _, _, _, g = built
    for d in g.subjects(RDF.type, registry.CRM["E31_Document"]):
        same = [str(o) for o in g.objects(d, registry.OWL["sameAs"])]
        assert len(same) == 1 and same[0].startswith("https://dodis.ch/")


def test_autor_ist_e65_und_unterzeichner_ist_e13(built):
    _, _, rep, g = built
    creations = list(g.subjects(RDF.type, registry.CRM["E65_Creation"]))
    with_author = [c for c in creations if (c, registry.CRM["P14_carried_out_by"], None) in g]
    assert len(with_author) >= 1
    roles = {str(next(g.objects(t, URIRef("http://www.w3.org/2000/01/rdf-schema#label"))))
             for a in g.subjects(RDF.type, registry.CRM["E13_Attribute_Assignment"])
             for t in g.objects(a, registry.CRM["P177_assigned_property_of_type"])}
    assert roles <= {"signatory", "adressee", "address_of_copy"} and roles


# ── Gegenproben ─────────────────────────────────────────────────────────────

def test_eine_kante_auf_eine_unbekannte_person_wird_gezaehlt_nicht_erfunden(tmp_path):
    def mutate(d):
        d["document_persons"].append({"document_id": d["documents"][0]["id"], "person_id": "999999999",
                                      "relationship_type": "mention"})
        d["document_places"].append({"document_id": d["documents"][0]["id"], "place_id": "999999999"})
    db = _db(tmp_path, mutate)
    rep = dodis.export(db, tmp_path / "x.nt")
    assert rep.withheld["dangling_persons"] == 1 and rep.withheld["dangling_places"] == 1
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    assert not [s for s in g.subjects() if str(s).endswith("/999999999")]


def test_eine_unbekannte_rolle_wird_gezaehlt_nicht_stillschweigend_uebernommen(tmp_path):
    def mutate(d):
        d["document_persons"].append({"document_id": d["documents"][0]["id"],
                                      "person_id": d["persons"][0]["id"], "relationship_type": "translator"})
    db = _db(tmp_path, mutate)
    rep = dodis.export(db, tmp_path / "x.nt")
    assert rep.withheld["unknown_roles"] == 1
    # Die Erwähnung bleibt — eine Person, die das Dokument nennt, wird nicht
    # verschwiegen, nur ihre Rolle nicht geraten.
    assert rep.counts["mentions_person"] == _sql(db, "SELECT count(*) FROM document_persons")


def test_limit_nimmt_die_ersten_dokumente_und_nur_ihre_entitaeten(tmp_path):
    db = _db(tmp_path)
    rep = dodis.export(db, tmp_path / "x.nt", limit=2)
    assert rep.counts["documents"] == 2
    assert rep.counts["persons"] <= _sql(db, "SELECT count(*) FROM persons")
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    for p in g.subjects(RDF.type, registry.CRM["E21_Person"]):
        assert list(g.subjects(registry.CRM["P67_refers_to"], p)), "Person ohne Dokument trotz --limit"


def test_das_datum_wird_nicht_erfunden():
    assert dodis._date_bounds("12.6.1967", 1967) == ("1967-06-12", "1967-06-12")
    assert dodis._date_bounds("7.1955", 1955) == ("1955-07-01", "1955-07-31")
    # Der Monat endet, wo der Kalender endet — der Vollbau hat «2.1955» als
    # 29. Februar geschrieben, und rdflib hat es abgewiesen.
    assert dodis._date_bounds("2.1955", 1955) == ("1955-02-01", "1955-02-28")
    assert dodis._date_bounds("2.1956", 1956) == ("1956-02-01", "1956-02-29")
    assert dodis._date_bounds("1955", None) == ("1955-01-01", "1955-12-31")
    assert dodis._date_bounds("ca. 1950er", None) == (None, None)
    # Ein unmöglicher Tag wird nicht zu einem unmöglichen xsd:date — das Jahr bleibt.
    assert dodis._date_bounds("31.2.1955", 1955) == ("1955-01-01", "1955-12-31")
