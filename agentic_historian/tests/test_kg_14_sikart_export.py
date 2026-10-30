"""KG-14 (ch-h-bot#582, S-7): SIKART Künstlernamen → Bestandsgraph.

Die Fixture (``fixtures/bestandsgraph/sikart.json``) sind die 50 Zeilen der
sikart_mcp-Testfixture, am 30.10.2026 mit build_db.py und link_authorities.py
gebaut (Wikidata aus dem SPARQL-Lauf desselben Tags); Ackers Wikidata-HLS-Id ist
von Hand auf 049618 gesetzt, damit ein Konflikt im Bild ist, und eine
HBLS-Trefferzeile (0,99) ist ergänzt, weil die 50 Zeilen keine ergeben.

Was hier gesichert wird: ein Erstdatum wird keine Geburt; Taufe ist keine Geburt;
Orte sind Namen am Ereignis, keine E53; jede Kennung hat ihre Quelle; ein Konflikt
steht beidseitig; ein Abgleich unter 1 wird kein owl:sameAs.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef, RDF, RDFS, XSD

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_hub import bestandsgraph as bg             # noqa: E402
from knowledge_hub.ontology import registry               # noqa: E402
from knowledge_hub.sources import sikart                  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph" / "sikart.json"
CRM, PROV, OWL, SHB = registry.CRM, registry.PROV, registry.OWL, registry.SHB
ACKER = "12361740"
SCHEMA = """
CREATE TABLE persons (id TEXT PRIMARY KEY, permalink TEXT, name_sort TEXT, name TEXT, kind TEXT, vita TEXT,
    life_dates_text TEXT, website TEXT, dok INTEGER, redacted INTEGER, row_hash TEXT, first_seen_run TEXT,
    last_changed_run TEXT);
CREATE TABLE dates (person_id TEXT, role TEXT, type TEXT, raw TEXT, year INTEGER, iso TEXT);
CREATE TABLE places (person_id TEXT, role TEXT, type TEXT, raw TEXT, name TEXT, code TEXT);
CREATE TABLE identifiers (person_id TEXT, scheme TEXT, value TEXT, raw TEXT, asserted_by TEXT, redirected_from TEXT);
CREATE TABLE ingest_runs (run_id TEXT, started_at TEXT, finished_at TEXT, source TEXT, etag TEXT,
    last_modified TEXT, rows INTEGER, inserted INTEGER, updated INTEGER, unchanged INTEGER, removed INTEGER,
    redacted INTEGER, living_cutoff_year INTEGER, commit_sha TEXT, note TEXT);
CREATE TABLE links (link_id INTEGER PRIMARY KEY, person_id TEXT, scheme TEXT, value TEXT, asserted_by TEXT,
    confidence REAL, method TEXT, retrieved_at TEXT, conflicts_with INTEGER);
CREATE TABLE link_checks (person_id TEXT, source TEXT, checked_at TEXT, status TEXT);
"""
TABLES = ("persons", "dates", "places", "identifiers", "ingest_runs", "links", "link_checks")


def _db(tmp: Path) -> Path:
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    path = tmp / "sikart.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for t in TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        con.executemany(f"INSERT INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r.get(c) for c in cols) for r in d[t]])
    con.commit()
    con.close()
    return path


def _sql(path: Path, query: str):
    con = sqlite3.connect(path)
    try:
        return con.execute(query).fetchone()[0]
    finally:
        con.close()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("sikart")
    db = _db(tmp)
    out = tmp / "sikart.nt"
    rep = sikart.export(db, out, validate=True, shacl=True)
    return db, out, rep, Graph().parse(str(out), format="nt")


def _person(g: Graph, pid: str) -> URIRef:
    for kind in ("person", "group"):
        u = URIRef(f"https://tei.dh.unibe.ch/id/sikart/{kind}/{pid}")
        if (u, RDF.type, None) in g:
            return u
    raise AssertionError(pid)


def _span(g: Graph, node: URIRef):
    ts = g.value(node, CRM["P4_has_time-span"])
    if ts is None:
        return None, None
    a, z = g.value(ts, CRM["P82a_begin_of_the_begin"]), g.value(ts, CRM["P82b_end_of_the_end"])
    return (str(a) if a else None, str(z) if z else None)


def test_der_graph_ist_valide_mit_shacl(built):
    _, _, rep, _ = built
    assert rep.validation["ok"], rep.validation


def test_die_zaehlungen_sind_die_sql_zaehlungen(built):
    db, _, rep, g = built
    c = rep.counts
    assert c["persons"] == _sql(db, "SELECT count(*) FROM persons WHERE kind = 'person'")
    assert c["groups"] == _sql(db, "SELECT count(*) FROM persons WHERE kind != 'person'")
    assert c["births"] == _sql(db, "SELECT count(*) FROM dates d JOIN persons p ON p.id = d.person_id "
                                   "WHERE d.type = 'Geburtsdatum' AND p.kind = 'person'")
    assert c["deaths"] == _sql(db, "SELECT count(*) FROM dates d JOIN persons p ON p.id = d.person_id "
                                   "WHERE d.type = 'Todesdatum' AND p.kind = 'person'")
    assert c["activities"] == _sql(db, "SELECT count(DISTINCT person_id) FROM dates WHERE type IN "
                                       "('Erstdatum','Letztdatum','Epochenerstdatum','Epochenletztdatum')")
    assert c["date_assignments"] + c["births"] + c["deaths"] + _sql(
        db, "SELECT count(*) FROM dates WHERE type IN ('Erstdatum','Letztdatum','Epochenerstdatum','Epochenletztdatum')"
    ) >= _sql(db, "SELECT count(*) FROM dates")
    assert c["place_names"] == _sql(db, "SELECT count(*) FROM places")
    assert c["redacted"] == _sql(db, "SELECT count(*) FROM persons WHERE redacted = 1")
    assert sum(c["assertions"].values()) == _sql(db, "SELECT count(*) FROM links")
    assert len(list(g.subjects(RDF.type, CRM["E53_Place"]))) == 0, "Orte sind Namen am Ereignis, keine E53"


def test_ein_erstdatum_ist_eine_taetigkeit_keine_geburt(built):
    _, _, _, g = built
    u = _person(g, ACKER)
    assert (None, CRM["P98_brought_into_life"], u) not in g and (None, CRM["P100_was_death_of"], u) not in g
    acts = list(g.subjects(CRM["P14_carried_out_by"], u))
    assert len(acts) == 1 and (acts[0], RDF.type, CRM["E7_Activity"]) in g
    assert _span(g, acts[0]) == ("1413-01-01", "1461-12-31")
    # Erst-/Letzt-Ort hängen als E41 an der Tätigkeit — «Ulm (DEU)», wie SIKART schreibt
    names = {str(g.value(n, CRM["P190_has_symbolic_content"])) for n in g.objects(acts[0], CRM["P1_is_identified_by"])}
    assert names == {"Ulm (DEU)"}


def test_geburt_und_tod_nur_aus_geburts_und_todesdatum(built):
    db, _, _, g = built
    con = sqlite3.connect(db)
    acht = con.execute("SELECT id FROM persons WHERE name_sort = 'Acht, René'").fetchone()[0]
    taufe = con.execute("SELECT person_id FROM dates WHERE type = 'Taufdatum' LIMIT 1").fetchone()
    con.close()
    u = _person(g, acht)
    birth = g.value(predicate=CRM["P98_brought_into_life"], object=u)
    death = g.value(predicate=CRM["P100_was_death_of"], object=u)
    assert birth is not None and death is not None
    assert _span(g, birth)[0].startswith("19") and (birth, PROV["wasDerivedFrom"], None) in g
    place = {str(g.value(n, CRM["P190_has_symbolic_content"])) for n in g.objects(birth, CRM["P1_is_identified_by"])}
    assert place == {"Basel (BS)"}
    if taufe:
        t = _person(g, taufe[0])
        assert (None, CRM["P98_brought_into_life"], t) not in g, "Taufe ist keine Geburt"
        e13 = [a for a in g.subjects(CRM["P140_assigned_attribute_to"], t)
               if str(g.value(g.value(a, CRM["P177_assigned_property_of_type"]), RDFS.label)) == "Taufdatum"]
        assert len(e13) == 1


def test_eine_lebende_person_traegt_die_kuerzung_als_notiz(built):
    db, _, _, g = built
    pid = _sql(db, "SELECT id FROM persons WHERE redacted = 1 LIMIT 1")
    notes = {str(n) for n in g.objects(_person(g, pid), CRM["P3_has_note"])}
    assert any("zurückgehalten" in n for n in notes)


def test_jede_kennung_hat_ihre_quelle_und_sameas_nur_bei_konfidenz_eins(built):
    _, _, _, g = built
    u = _person(g, ACKER)
    same = {str(o) for o in g.objects(u, OWL["sameAs"])}
    assert "https://recherche.sik-isea.ch/sik:person-12361740" in same
    assert "https://www.wikidata.org/entity/Q71761" in same
    assert "https://d-nb.info/gnd/121397009" in same
    assert "https://hls-dhs-dss.ch/de/articles/031952/" in same          # SIKART sagt es
    assert "https://hls-dhs-dss.ch/de/articles/049618/" in same          # Wikidata (in der Fixture) sagt das
    ids = list(g.objects(u, CRM["P1_is_identified_by"]))
    e42 = [i for i in ids if (i, RDF.type, CRM["E42_Identifier"]) in g]
    sources = {str(g.value(i, PROV["wasDerivedFrom"])) for i in e42}
    assert any("/dataset/sikart/csv" in s for s in sources)
    assert any("/dataset/sikart/wikidata-sparql" in s for s in sources)
    hbls = [i for i in e42 if "/identifier/hbls/" in str(i)]
    assert len(hbls) == 1
    assert g.value(hbls[0], SHB["matchConfidence"]) == Literal(0.99, datatype=XSD.double)
    assert not any("hbls" in s for s in same), "ein Abgleich unter 1 wird kein owl:sameAs"


def test_ein_konflikt_steht_beidseitig(built):
    _, _, rep, g = built
    pairs = list(g.subject_objects(SHB["conflictsWith"]))
    assert rep.counts["conflict_edges"] == 2 and len(pairs) == 2
    (a, z), (z2, a2) = pairs if pairs[0][0] == pairs[1][1] else (pairs[0], (pairs[1][1], pairs[1][0]))
    assert {a, z} == {z2, a2}
    values = {str(g.value(x, CRM["P190_has_symbolic_content"])) for x in (a, z)}
    assert values == {"031952", "049618"}


def test_zweimal_gebaut_ist_byteidentisch_bis_auf_die_bauzeit(tmp_path):
    db = _db(tmp_path)
    a, b_ = tmp_path / "a.nt", tmp_path / "b.nt"
    sikart.export(db, a, commit="x")
    sikart.export(db, b_, commit="x")
    strip = lambda p: [ln for ln in p.read_text(encoding="utf-8").splitlines() if "AtTime" not in ln]  # noqa: E731
    assert strip(a) == strip(b_)
