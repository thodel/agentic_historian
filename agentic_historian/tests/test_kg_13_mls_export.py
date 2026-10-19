"""KG-13 (ch-h-bot#543, M-6): das Musiklexikon der Schweiz im Bestandsgraphen — gegen eine mls.db aus der Fixture.

Die Fixture (``fixtures/bestandsgraph/mls.json``) ist am 19.10.2026 aus der echten
``mls.db`` gezogen: alle 27 Lexika, 29 Lemmata, die je einen Fall tragen (HLS-Code
mit vier, fünf Ziffern und «F», GND+VIAF, Pseudonyme, «um/vor/nach/getauft»,
«nachgewiesen», Datum ohne Info, «-», Institution, Ort, Sache, Lemmata ohne Typ,
ein Verweis-Lemma, ein Spoerri-Artikel ohne Text), mit allen ihren Artikeln,
Orten und Tätigkeiten.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_13_mls_export.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef, RDF, RDFS

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from knowledge_hub import bestandsgraph as bg             # noqa: E402
from knowledge_hub import queries as q                    # noqa: E402
from knowledge_hub.ontology import registry               # noqa: E402
from knowledge_hub.sources import mls                     # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph" / "mls.json"
CRM, PROV, OWL = registry.CRM, registry.PROV, registry.OWL
#: Die Tabellen aus ``mls_mcp/build_db.py`` (SCHEMA), ohne FTS und Ingest-Buchhaltung.
SCHEMA = """
CREATE TABLE lexica (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, shortname TEXT, citation_form TEXT,
    year TEXT, weblink TEXT, links_digi TEXT, comment TEXT, last_modified TEXT);
CREATE TABLE lemmata (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, lemma_text TEXT, family_name TEXT,
    given_name TEXT, pseudonym TEXT, variants TEXT, lemma_type TEXT, sex TEXT, deceased TEXT, relevance TEXT,
    start_date TEXT, start_date_info TEXT, end_date TEXT, end_date_info TEXT, century TEXT, gnd TEXT, viaf TEXT,
    comment TEXT, sub_lemma_iri TEXT, last_modified TEXT);
CREATE TABLE articles (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, lemma_iri TEXT, lexicon_iri TEXT,
    article_type TEXT, text TEXT, comment TEXT, short_info TEXT, pages TEXT, hls_code TEXT, oeml_code TEXT,
    ticino_code TEXT, theater_code TEXT, fonoteca_code TEXT, web_link TEXT, frontend_link TEXT, state TEXT,
    timestamp TEXT, source TEXT, musical_activity TEXT, internal_lex TEXT, orig_spelling TEXT, date_text TEXT,
    author_iri TEXT, subarticle_iri TEXT, is_reference INTEGER NOT NULL DEFAULT 0, last_modified TEXT);
CREATE TABLE locations (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, placename TEXT, canton TEXT,
    country TEXT, comment TEXT, last_modified TEXT);
CREATE TABLE lemma_locations (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, lemma_iri TEXT, location_iri TEXT,
    relation TEXT, comment TEXT, last_modified TEXT);
CREATE TABLE occupations (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, occupation TEXT, last_modified TEXT);
CREATE TABLE lemma_occupations (iri TEXT PRIMARY KEY, ark TEXT, label TEXT, lemma_iri TEXT, occupation_iri TEXT,
    comment TEXT, last_modified TEXT);
CREATE TABLE list_nodes (iri TEXT PRIMARY KEY, label TEXT, root TEXT);
"""
TABLES = ("lexica", "list_nodes", "lemmata", "articles", "locations", "lemma_locations",
          "occupations", "lemma_occupations")


def _db(tmp_path: Path, mutate=None) -> Path:
    d = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if mutate:
        mutate(d)
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "mls.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for table in TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        con.executemany(f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [tuple(r.get(c, 0 if c == "is_reference" else None) for c in cols) for r in d[table]])
    con.commit()
    con.close()
    return path


def _sql(path: Path, query: str):
    con = sqlite3.connect(path)
    try:
        return con.execute(query).fetchone()[0]
    finally:
        con.close()


def _typed(db: Path, label: str) -> int:
    return _sql(db, "SELECT count(*) FROM lemmata l JOIN list_nodes n ON n.iri = l.lemma_type "
                    f"WHERE n.label = '{label}'")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("mls")
    db = _db(tmp)
    out = tmp / "mls.nt"
    rep = mls.export(db, out, validate=True, shacl=True)
    return db, out, rep, Graph().parse(str(out), format="nt")


def _lemma(g: Graph, label: str) -> URIRef:
    kinds = ("/mls/person/", "/mls/group/", "/mls/place/", "/mls/topic/")
    hits = [s for s in g.subjects(RDFS.label, Literal(label)) if any(k in str(s) for k in kinds)]
    assert len(hits) == 1, (label, hits)
    return hits[0]


def _span(g: Graph, event: URIRef) -> tuple:
    ts = g.value(event, CRM["P4_has_time-span"])
    if ts is None:
        return None, None, ()
    a = g.value(ts, CRM["P82a_begin_of_the_begin"])
    z = g.value(ts, CRM["P82b_end_of_the_end"])
    return (str(a) if a else None, str(z) if z else None, tuple(str(n) for n in g.objects(ts, CRM["P3_has_note"])))


# ── Zählungen = mls.db ──────────────────────────────────────────────────────

def test_die_zaehlungen_sind_die_sql_zaehlungen(built):
    db, _, rep, g = built
    c = rep.counts
    assert c["lexica"] == _sql(db, "SELECT count(*) FROM lexica") == 27
    assert c["articles"] == _sql(db, "SELECT count(*) FROM articles")
    assert c["mentions"] == _sql(db, "SELECT count(*) FROM articles WHERE lemma_iri IN (SELECT iri FROM lemmata)")
    assert c["articles_reference"] == _sql(db, "SELECT count(*) FROM articles WHERE is_reference = 1")
    assert c["places"] == _sql(db, "SELECT count(*) FROM locations")
    assert c["groups"] == _typed(db, "Institution")
    assert c["places_lemma"] == _typed(db, "Ort")
    assert c["persons"] - c["persons_untyped"] == _typed(db, "Person")
    assert c["topics"] - c["topics_untyped"] == _typed(db, "Sache")
    assert c["persons_untyped"] + c["topics_untyped"] == _sql(db, "SELECT count(*) FROM lemmata WHERE lemma_type IS NULL")
    assert (c["persons"] + c["groups"] + c["places_lemma"] + c["topics"]
            == _sql(db, "SELECT count(*) FROM lemmata"))
    # … und im Graphen, nicht nur im Bericht
    def n(cls, kind):
        return sum(1 for s in g.subjects(RDF.type, CRM[cls]) if f"/mls/{kind}/" in str(s))
    assert n("E31_Document", "document") == c["articles"]
    assert n("E21_Person", "person") == c["persons"]
    assert n("E74_Group", "group") == c["groups"]
    assert n("E53_Place", "place") == c["places"] + c["places_lemma"]
    assert n("E55_Type", "topic") == c["topics"]
    assert len(set(g.subject_objects(CRM["P67_refers_to"]))) == c["mentions"]


def test_taetigkeiten_zaehlen_wie_die_tabelle(built):
    db, _, rep, g = built
    person = ("lemma_iri IN (SELECT l.iri FROM lemmata l JOIN list_nodes n ON n.iri = l.lemma_type "
              "WHERE n.label = 'Person')")
    assert rep.counts["occupation_links"] == _sql(db, f"SELECT count(*) FROM lemma_occupations WHERE {person}")
    assert rep.counts["occupations"] == _sql(
        db, f"SELECT count(DISTINCT lemma_iri || occupation_iri) FROM lemma_occupations WHERE {person}")
    assert rep.counts["occupations"] == len(set(g.subjects(RDF.type, registry.SDH_SLC["C13"])))
    # Koëlla trägt dieselbe Tätigkeit zweimal: eine C13, nicht zwei
    assert rep.counts["occupation_links"] > rep.counts["occupations"]
    assert rep.withheld["occupations_on_non_persons"] == _sql(
        db, f"SELECT count(*) FROM lemma_occupations WHERE NOT {person}")


def test_der_graph_ist_valide_mit_shacl(built):
    _, _, rep, _ = built
    assert rep.validation["ok"], rep.validation
    assert rep.validation["shacl"] is True


def test_zweimal_gebaut_ist_byteidentisch_bis_auf_die_bauzeit(tmp_path):
    db = _db(tmp_path)
    a, b2 = tmp_path / "a.nt", tmp_path / "b.nt"
    mls.export(db, a)
    mls.export(db, b2)
    strip = lambda p: [line for line in p.read_text(encoding="utf-8").splitlines()  # noqa: E731
                       if "AtTime" not in line]
    assert strip(a) == strip(b2)


# ── Lexikon als Datensatz je Artikel ────────────────────────────────────────

def test_jeder_artikel_stammt_aus_genau_einem_lexikon(built):
    db, _, _, g = built
    bestand = bg.AH["dataset/mls"]
    lexica = set(g.subjects(PROV["wasDerivedFrom"], bestand)) & set(g.subjects(RDF.type, PROV["Entity"]))
    assert len(lexica) == 27
    for doc in g.subjects(RDF.type, CRM["E31_Document"]):
        sources = list(g.objects(doc, PROV["wasDerivedFrom"]))
        assert len(sources) == 1 and sources[0] in lexica, (doc, sources)
        creation = g.value(predicate=CRM["P94_has_created"], object=doc)
        if creation is not None:
            assert list(g.objects(creation, PROV["wasDerivedFrom"])) == sources
    labels = {str(g.value(x, RDFS.label)) for x in lexica}
    assert "Musiklexikon der Schweiz — Becker 1874" in labels
    assert all(lbl.startswith("Musiklexikon der Schweiz — ") for lbl in labels)


def test_ein_artikel_traegt_das_jahr_seines_lexikons(built):
    _, _, rep, g = built
    doc = next(s for s in g.subjects(RDFS.label, None)
               if str(g.value(s, RDFS.label)) == "Odier, Louis (Becker 1874)")
    creation = g.value(predicate=CRM["P94_has_created"], object=doc)
    assert _span(g, creation)[:2] == ("1874-01-01", "1874-12-31")
    # Online-Lexika ohne Jahr: der Artikel bleibt undatiert, gezählt
    assert rep.withheld["undated_articles"] > 0
    assert rep.counts["articles_dated"] + rep.withheld["undated_articles"] == rep.counts["articles"]


def test_hls_verweis_und_bearbeitungsstand_sind_typen(built):
    _, _, _, g = built
    hls_ref = [s for s, t in g.subject_objects(CRM["P2_has_type"])
               if str(g.value(t, RDFS.label)) == "HLS-Verweis"]
    assert hls_ref and all("(HLS Online)" in str(g.value(s, RDFS.label)) for s in hls_ref)
    ocr = [s for s, t in g.subject_objects(CRM["P2_has_type"])
           if str(g.value(t, RDFS.label)) == "mit 1. Korrektur"]
    assert any("(Refardt 1928)" in str(g.value(s, RDFS.label)) for s in ocr)


# ── Lemma: Typ, sameAs, Namen ───────────────────────────────────────────────

def test_lemmatyp_bestimmt_die_klasse(built):
    _, _, _, g = built
    assert (_lemma(g, "Jazz"), RDF.type, CRM["E55_Type"]) in g
    assert (_lemma(g, "Groove-T-Gang"), RDF.type, CRM["E74_Group"]) in g
    assert (_lemma(g, "La Chaux-du-Milieu"), RDF.type, CRM["E53_Place"]) in g
    assert (_lemma(g, "Munzinger, Eduard"), RDF.type, CRM["E21_Person"]) in g
    # ohne Typ: mit Namen eine Person, ohne ein Thema
    assert (_lemma(g, "Gerbini, Luigia"), RDF.type, CRM["E21_Person"]) in g
    assert (_lemma(g, "Orgelbauer"), RDF.type, CRM["E55_Type"]) in g


def test_hls_code_wird_sameas_auf_den_hls_artikel(built):
    _, _, rep, g = built
    def same(label):
        return {str(o) for o in g.objects(_lemma(g, label), OWL["sameAs"])}
    assert "https://hls-dhs-dss.ch/de/articles/046111/" in same("Munzinger, Eduard")   # D46111
    assert "https://hls-dhs-dss.ch/de/articles/009967/" in same("Morel, Gall")         # D9967
    assert "https://hls-dhs-dss.ch/de/articles/047154/" in same("Dubuis, Claude")      # F47154
    assert rep.withheld["hls_codes_unparsed"] == 1                                     # «-none-»
    assert not any("hls-dhs-dss" in s for s in same("Groove-T-Gang"))


def test_gnd_viaf_und_ark_sind_sameas(built):
    _, _, _, g = built
    same = {str(o) for o in g.objects(_lemma(g, "Odier, Louis"), OWL["sameAs"])}
    assert any(s.startswith("https://d-nb.info/gnd/") for s in same)
    assert any(s.startswith("https://viaf.org/viaf/") for s in same)
    assert any(s.startswith("https://ark.dasch.swiss/ark:/72163/1/0807/") for s in same)
    # «134849655 | 134849655» ist eine GND, nicht zwei
    gnd = [o for o in g.objects(_lemma(g, "Breitenbach, Franz Joseph"), OWL["sameAs"]) if "d-nb.info" in str(o)]
    assert len(gnd) == 1


def _names(g: Graph, subject: URIRef, kind: str) -> set[str]:
    out = set()
    for n in g.objects(subject, CRM["P1_is_identified_by"]):
        t = g.value(n, CRM["P2_has_type"])
        if t is not None and str(g.value(t, RDFS.label)) == kind:
            out.add(str(g.value(n, CRM["P190_has_symbolic_content"])))
    return out


def test_namen_als_e41_je_art(built):
    _, _, _, g = built
    schori = _lemma(g, "Schori, Fritz")
    assert _names(g, schori, "Familienname") == {"Schori"}
    assert _names(g, schori, "Pseudonym") == {"Julius Meister", "Jean Marquis", "Achille Duroc", "Max H. Forster"}
    assert _names(g, _lemma(g, "Zwyssig, Alberik"), "Variante") == {"Zwyssig, Alberich",
                                                                    "Zwyssig, Johann Joseph Maria"}


# ── Daten: Geburt/Tod oder Zuschreibung ─────────────────────────────────────

def _event(g: Graph, person: URIRef, kind: str):
    return URIRef(f"{person}/{kind}") if (URIRef(f"{person}/{kind}"), None, None) in g else None


def test_geboren_und_gestorben_sind_e67_e69(built):
    _, _, _, g = built
    m = _lemma(g, "Munzinger, Eduard")
    assert (_event(g, m, "birth"), RDF.type, CRM["E67_Birth"]) in g
    assert _span(g, _event(g, m, "birth"))[:2] == ("1831-06-24", "1831-06-24")
    assert _span(g, _event(g, m, "death"))[:2] == ("1899-03-29", "1899-03-29")


def test_um_vor_nach_getauft_so_genau_wie_die_quelle(built):
    _, _, _, g = built
    senfl = _lemma(g, "Senfl, Ludwig")                       # um 1486 – ca. 02.12.1542
    a, z, notes = _span(g, _event(g, senfl, "birth"))
    assert (a, z) == ("1486-01-01", "1486-12-31") and any("um 1486" in n for n in notes)
    ebel = _lemma(g, "Ebel (Uebel), Heinrich")              # nach 1400 – vor 1499
    assert _span(g, _event(g, ebel, "birth"))[:2] == ("1400-01-01", None)
    assert _span(g, _event(g, ebel, "death"))[:2] == (None, "1499-12-31")
    hermann = _lemma(g, "Hermann, Gabriel")                 # getauft 25.04.1556
    a, z, notes = _span(g, _event(g, hermann, "birth"))
    assert (a, z) == (None, "1556-04-25") and any("getauft" in n for n in notes)


def _assigned(g: Graph, subject: URIRef) -> dict[str, tuple]:
    out = {}
    for e in g.subjects(CRM["P140_assigned_attribute_to"], subject):
        kind = str(g.value(g.value(e, CRM["P177_assigned_property_of_type"]), RDFS.label))
        out[kind] = _span(g, e)[:2]
    return out


def test_sonst_eine_zuschreibung_e13(built):
    _, _, _, g = built
    oser = _lemma(g, "Oser, Ida")                           # nachgewiesen 1949 – nachgewiesen 1950
    assert _event(g, oser, "birth") is None and _event(g, oser, "death") is None
    e13 = list(g.subjects(CRM["P140_assigned_attribute_to"], oser))
    assert len(e13) == 2 and all((e, RDF.type, CRM["E13_Attribute_Assignment"]) in g for e in e13)
    assert _assigned(g, oser) == {"nachgewiesen": ("1950-01-01", "1950-12-31")} or \
        {_span(g, e)[:2] for e in e13} == {("1949-01-01", "1949-12-31"), ("1950-01-01", "1950-12-31")}
    assert {_span(g, e)[:2] for e in e13} == {("1949-01-01", "1949-12-31"), ("1950-01-01", "1950-12-31")}
    durigo = _lemma(g, "Durigo, Ilona")                     # Daten ohne Info
    assert "Anfangsdatum ohne Angabe" in _assigned(g, durigo)
    assert _event(g, durigo, "birth") is None
    lyss = _lemma(g, "Musikgesellschaft Lyss")              # Gründungsdatum, eine Institution
    assert "Gründungsdatum" in _assigned(g, lyss)
    assert _event(g, lyss, "birth") is None


def test_strich_und_unbekannt_werden_gezaehlt_nicht_gebaut(built):
    _, _, rep, g = built
    assert rep.withheld["dash_dates"] >= 1                  # Baer, Rolf: Enddatum «-»
    baer = _lemma(g, "Baer, Rolf")
    assert _event(g, baer, "death") is None


# ── Orte mit Bezug ──────────────────────────────────────────────────────────

def test_geburts_und_todesort_an_der_e67_e69_taetigkeitsort_als_p74(built):
    _, _, _, g = built
    aebi = _lemma(g, "Aebi, Christoph")
    assert g.value(_event(g, aebi, "birth"), CRM["P7_took_place_at"]) is not None
    assert g.value(_event(g, aebi, "death"), CRM["P7_took_place_at"]) is not None
    assert list(g.objects(aebi, CRM["P74_has_current_or_former_residence"]))
    # Institution: «Gründung» ist der Sitz
    assert list(g.objects(_lemma(g, "Groove-T-Gang"), CRM["P74_has_current_or_former_residence"]))
    # nie eine C3 zu einem Ort
    assert not list(g.subjects(RDF.type, registry.SDH_SLC["C3"]))


def test_bezuege_ohne_art_und_an_orten_werden_gezaehlt(built):
    db, _, rep, _ = built
    assert rep.withheld["relations_without_kind"] == _sql(db, "SELECT count(*) FROM lemma_locations WHERE relation IS NULL")
    assert rep.withheld["relations_on_non_actors"] >= 1     # Engelberg, Muri, Reichenau (Ort) — Tätigkeitsort
    built_rels = rep.counts["residences"] + rep.counts["birth_places"] + rep.counts["death_places"]
    distinct = _sql(db, "SELECT count(DISTINCT lemma_iri || location_iri || relation) FROM lemma_locations "
                        "WHERE relation IS NOT NULL")
    assert built_rels + rep.withheld["relations_on_non_actors"] == distinct


# ── die Frage über die Bestände ─────────────────────────────────────────────

def test_persons_of_century_liefert_mls_zeilen_fuer_das_19_jahrhundert(built):
    _, _, _, g = built
    rows = q.run("persons-of-century-across-sources", g, century="19").rows
    assert rows
    assert all(r["source"].startswith("Musiklexikon der Schweiz — ") for r in rows)
    assert {r["source"] for r in rows} == {"Musiklexikon der Schweiz — Becker 1874"}
    assert any(r["personLabel"] == "Odier, Louis" and r["authority"]
               and r["authority"].startswith("https://d-nb.info/gnd/") for r in rows)


def test_persons_of_century_neben_den_anderen_bestaenden(built):
    """Über die Vereinigung mit dem KG-6-Fixture: MLS kommt hinzu, nichts fällt weg."""
    import bestandsgraph_fixture as fx
    _, _, _, g = built
    others = fx.all_graphs()
    union = Graph()
    for part in (others, g):
        for t in part:
            union.add(t)
    for century in ("14", "15", "16", "19", "20"):
        before = q.run("persons-of-century-across-sources", others, century=century).rows
        after = q.run("persons-of-century-across-sources", union, century=century).rows
        mls_rows = [r for r in after if r["source"].startswith("Musiklexikon der Schweiz")]
        assert len(after) == len(before) + len(mls_rows), century
    after19 = q.run("persons-of-century-across-sources", union, century="19").rows
    assert any(r["source"].startswith("Musiklexikon der Schweiz") for r in after19)
