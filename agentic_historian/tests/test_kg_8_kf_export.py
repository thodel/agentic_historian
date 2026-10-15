"""KG-8 (#652): Königsfelden in den Bestandsgraphen — gegen eine kf.db aus der Fixture.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_8_kf_export.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef, RDF, RDFS

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub.ontology import registry           # noqa: E402
from knowledge_hub.sources import kf                  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph" / "kf.json"
SCHEMA = """
CREATE TABLE entries(id TEXT PRIMARY KEY,title TEXT,short_id TEXT,year INTEGER,source TEXT,pages TEXT,text_raw TEXT);
CREATE TABLE spans(id INTEGER PRIMARY KEY AUTOINCREMENT,entry_id TEXT,span_id TEXT,class TEXT,ref TEXT,text TEXT,norm TEXT);
CREATE TABLE persons(id TEXT PRIMARY KEY,forename TEXT,surname TEXT,full_name TEXT,main_name TEXT,occupation TEXT,birth TEXT,death TEXT,org_ref TEXT,hls_id TEXT,note TEXT);
CREATE TABLE places(id TEXT PRIMARY KEY,name_de TEXT,name_fr TEXT,country TEXT,region TEXT,geo TEXT,hls_id TEXT,gnd_id TEXT,place_type TEXT);
CREATE TABLE orgs(id TEXT PRIMARY KEY,name TEXT,desc_de TEXT,desc_fr TEXT);
"""


def _db(tmp_path: Path, mutate=None) -> Path:
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(d)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "kf.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?)",
                    [(e["id"], e["title"], e["short_id"], e["year"], e["source"], e["pages"], None) for e in d["entries"]])
    con.executemany("INSERT INTO spans(entry_id,span_id,class,ref,text,norm) VALUES (?,?,?,?,?,?)",
                    [(s["entry_id"], s["span_id"], s["class"], s["ref"], s["text"], s["norm"]) for s in d["spans"]])
    con.executemany("INSERT INTO persons VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [tuple(p[k] for k in ("id", "forename", "surname", "full_name", "main_name", "occupation",
                                          "birth", "death", "org_ref", "hls_id", "note")) for p in d["persons"]])
    con.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?)",
                    [tuple(p[k] for k in ("id", "name_de", "name_fr", "country", "region", "geo", "hls_id",
                                          "gnd_id", "place_type")) for p in d["places"]])
    con.executemany("INSERT INTO orgs VALUES (?,?,?,?)",
                    [(o["id"], o["name"], o["desc_de"], o["desc_fr"]) for o in d["orgs"]])
    con.commit(); con.close()
    return path


def _sql(path: Path, q: str):
    con = sqlite3.connect(path)
    try:
        return con.execute(q).fetchone()[0]
    finally:
        con.close()


RESOLVED = ("ref<>'' AND ref GLOB '[plo][eor][rcg][0-9][0-9][0-9][0-9][0-9][0-9]' AND "
            "(ref IN (SELECT id FROM persons) OR ref IN (SELECT id FROM places) OR ref IN (SELECT id FROM orgs))")


@pytest.fixture()
def built(tmp_path):
    db = _db(tmp_path)
    out = tmp_path / "kf.nt"
    rep = kf.export(db, out, validate=True, shacl=True)
    return db, out, rep, Graph().parse(str(out), format="nt")


def test_die_zaehlungen_sind_die_sql_zaehlungen(built):
    db, _, rep, g = built
    assert rep.counts["documents"] == _sql(db, "SELECT count(*) FROM entries")
    assert rep.counts["persons"] == _sql(db, "SELECT count(*) FROM persons")
    assert rep.counts["places"] == _sql(db, "SELECT count(*) FROM places")
    assert rep.counts["groups"] == _sql(db, "SELECT count(*) FROM orgs")
    mentions = rep.counts["mentions_person"] + rep.counts["mentions_place"] + rep.counts["mentions_group"]
    assert mentions == _sql(db, f"SELECT count(*) FROM spans WHERE {RESOLVED}")
    assert len(set(g.subjects(RDF.type, registry.CRM["E31_Document"]))) == rep.counts["documents"]


def test_der_graph_ist_valide_mit_shacl(built):
    _, _, rep, _ = built
    assert rep.validation["ok"], rep.validation


def test_zweimal_gebaut_ist_byteidentisch_bis_auf_die_bauzeit(tmp_path):
    db = _db(tmp_path)
    a, b2 = tmp_path / "a.nt", tmp_path / "b.nt"
    kf.export(db, a); kf.export(db, b2)
    strip = lambda p: [l for l in p.read_text(encoding="utf-8").splitlines()  # noqa: E731
                       if "AtTime" not in l]
    assert strip(a) == strip(b2)


# ── Verweise: Register-Id oder nichts ────────────────────────────────────────

def test_bearbeiterkuerzel_werden_keine_personen(tmp_path):
    """5 856 persName-Spans in kf.db tragen als ref das Kürzel der Bearbeiterin
    (sste, ch, mn, KF, automatisch). Das sind Verantwortlichkeiten aus dem
    TEI-Kopf, keine Erwähnungen — und Sabine Stettler ist keine Person des
    14. Jahrhunderts."""
    def mutate(d):
        e = d["entries"][0]["id"]
        d["spans"] += [{"entry_id": e, "span_id": "", "class": "persName", "ref": "sste", "text": "Sabine Stettler", "norm": ""},
                       {"entry_id": e, "span_id": "", "class": "persName", "ref": "KF", "text": "KF", "norm": ""},
                       {"entry_id": e, "span_id": "", "class": "persName", "ref": "automatisch", "text": "Maschinell", "norm": ""}]
    db = _db(tmp_path, mutate)
    rep = kf.export(db, tmp_path / "x.nt")
    # Die Fixture trägt solche Kürzel schon (gezählt: 25 in 94 Spans — der
    # Anteil ist im Vollbestand derselbe), dazu die drei von hier.
    vorher = kf.export(_db(tmp_path / "ohne"), tmp_path / "ohne.nt").withheld["editorial_refs"]
    assert rep.withheld["editorial_refs"] == vorher + 3
    assert rep.withheld["editorial_by_prefix"]["sste"] >= 1 and rep.withheld["editorial_by_prefix"]["automatisch"] >= 1
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    labels = {str(o) for o in g.objects(None, RDFS.label)}
    assert "Sabine Stettler" not in labels and "Maschinell" not in labels


def test_eine_falsch_gebaute_id_haengt_und_wird_nicht_erfunden(tmp_path):
    def mutate(d):
        e = d["entries"][0]["id"]
        d["spans"] += [{"entry_id": e, "span_id": "", "class": "persName", "ref": "per02583", "text": "Johansen", "norm": ""},
                       {"entry_id": e, "span_id": "", "class": "placeName", "ref": "loc00019", "text": "Brugg", "norm": ""},
                       {"entry_id": e, "span_id": "", "class": "persName", "ref": "per999999", "text": "Niemand", "norm": ""}]
    db = _db(tmp_path, mutate)
    rep = kf.export(db, tmp_path / "x.nt")
    # Fünfstellig ist keine Register-Id (unparsed), sechsstellig ohne Datensatz hängt.
    assert rep.withheld["unparsed_spans"] == 2 and rep.withheld["dangling_refs"] == 1
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    assert not [s for s in g.subjects() if str(s).endswith("/per999999") or str(s).endswith("/per02583")]


def test_ein_tabulator_am_verweis_wird_gestrippt_und_trifft(tmp_path):
    def mutate(d):
        e = d["entries"][0]["id"]; o = d["orgs"][0]["id"]
        d["spans"].append({"entry_id": e, "span_id": "", "class": "orgName", "ref": o + "\t", "text": "capitulo", "norm": ""})
    db = _db(tmp_path, mutate)
    rep = kf.export(db, tmp_path / "x.nt")
    assert rep.withheld["unparsed_spans"] == 0 and rep.withheld["dangling_refs"] == 0
    # SQLites trim() streift Leerzeichen, keinen Tabulator — darum char(9) dazu.
    assert rep.counts["mentions_group"] == _sql(
        db, "SELECT count(*) FROM spans WHERE class='orgName' AND trim(ref, char(9)||' ') IN (SELECT id FROM orgs)")


# ── Das Mapping im Einzelnen ────────────────────────────────────────────────

def test_lebensdaten_so_genau_wie_die_quelle():
    assert kf.life_date("01.05.1218") == ("1218-05-01", "1218-05-01", None)
    assert kf.life_date("vor 07.04.1363") == (None, "1363-04-07", "Quelle: vor 07.04.1363")
    assert kf.life_date("nach 07.04.1363") == ("1363-04-07", None, "Quelle: nach 07.04.1363")
    assert kf.life_date("1349/1350") == ("1349-01-01", "1350-12-31", None)
    assert kf.life_date("1351?") == ("1351-01-01", "1351-12-31", "Quelle: 1351?")
    assert kf.life_date("um Ostern") == (None, None, "Quelle: um Ostern")
    assert kf.life_date("31.02.1300")[2].startswith("unmöglicher Tag")


def test_eine_obergrenze_ist_nur_eine_obergrenze(tmp_path):
    def mutate(d):
        d["persons"][0]["death"] = "vor 07.04.1363"; d["persons"][0]["birth"] = None
    db = _db(tmp_path, mutate)
    kf.export(db, tmp_path / "x.nt")
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    deaths = list(g.subjects(RDF.type, registry.CRM["E69_Death"]))
    assert deaths
    ts = next(g.objects(deaths[0], registry.CRM["P4_has_time-span"]))
    assert (ts, registry.CRM["P82a_begin_of_the_begin"], None) not in g
    assert str(next(g.objects(ts, registry.CRM["P82b_end_of_the_end"]))) == "1363-04-07"


def test_orte_tragen_geo_hls_und_gnd(built):
    _, _, _, g = built
    same = {str(o) for o in g.objects(None, registry.OWL["sameAs"])}
    assert any("hls-dhs-dss.ch/de/articles/00" in u for u in same), "HLS-Id mit sechs Stellen"
    assert any("d-nb.info/gnd/" in u for u in same)
    assert list(g.subjects(registry.GEO["lat"], None))


def test_die_schreibung_im_dokument_haengt_an_der_entitaet(built):
    _, _, rep, g = built
    orig = [n for n in g.subjects(RDF.type, registry.CRM["E41_Appellation"])
            if any(str(t).endswith("/name-type/orig") for t in g.objects(n, registry.CRM["P2_has_type"]))]
    assert len(orig) == rep.counts["names_orig"] > 0


def test_die_familie_ist_mitgliedschaft_und_gruppe_mit_typ(built):
    _, _, rep, g = built
    assert rep.counts["memberships"] > 0
    assert list(g.subjects(RDF.type, registry.SDH_SLC["C5"]))
    types = {str(o) for o in g.objects(None, registry.SDH["P99"])}
    assert types, "desc_de wird zum Gruppentyp"


def test_der_beruf_ist_eine_rollenverkoerperung(tmp_path):
    def mutate(d):
        d["persons"][0]["occupation"] = "Ritter"
    db = _db(tmp_path, mutate)
    kf.export(db, tmp_path / "x.nt")
    g = Graph().parse(str(tmp_path / "x.nt"), format="nt")
    assert list(g.subjects(RDF.type, registry.SDH_SLC["C13"]))
    assert "Ritter" in {str(o) for o in g.objects(None, RDFS.label)}
