"""Dodis persons → Wikidata, GND, HLS, VIAF, so Human Lineage can reach them (#830).

Dodis persons carried only their Dodis URI, which Human Lineage does not
resolve. Wikidata records the Dodis ID as P701 (17.11.2026: 7 693 humans);
``crosswalks/dodis_wikidata.py`` turns those statements into a crosswalk,
``dodis.py`` into ``owl:sameAs``. A Dodis ID Wikidata gives to two items gets
nothing. The Human Lineage layer then reads Dodis too — only its person lines.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_830_dodis_wikidata.py
"""

import importlib.util
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph, URIRef
from rdflib.namespace import OWL

from knowledge_hub.crosswalks import dodis_wikidata as dw
from knowledge_hub.sources import dodis
from knowledge_hub.sources import humanlineage as hl

MAP = PKG / "knowledge_hub" / "crosswalks" / "dodis_wikidata.json"


def _row(q, dodis_id, gnd="", hls="", viaf=""):
    return {"item": f"http://www.wikidata.org/entity/{q}", "dodis": dodis_id,
            "gnd": gnd, "hls": hls, "viaf": viaf}


# ── the crosswalk ────────────────────────────────────────────────────────────

def test_wikidata_rows_become_one_row_per_dodis_person():
    res = dw.build([_row("Q1394", "P1295", gnd="118640402", hls="28375", viaf="7393146"),
                    _row("Q1394", "P1295", gnd="118640402", hls="28375", viaf="7393146")])
    assert res["persons"] == {"1295": {"wikidata": "Q1394", "gnd": "118640402", "hls": "028375",
                                       "viaf": "7393146"}}


def test_a_dodis_id_on_two_items_gets_nothing():
    res = dw.build([_row("Q160278", "P858"), _row("Q1699735", "P858"), _row("Q1", "P1")])
    assert res["ambiguous"] == {"858": ["Q160278", "Q1699735"]} and "858" not in res["persons"]
    assert res["persons"] == {"1": {"wikidata": "Q1"}}


def test_two_values_of_one_scheme_are_left_out_and_counted():
    res = dw.build([_row("Q5", "P5", gnd="1"), _row("Q5", "P5", gnd="2", viaf="9")])
    assert res["persons"]["5"] == {"wikidata": "Q5", "viaf": "9"}
    assert res["multi_valued_left_out"] == {"gnd": 1}


def test_the_committed_crosswalk_says_where_it_comes_from():
    doc = json.loads(MAP.read_text(encoding="utf-8"))
    meta = doc["_meta"]
    assert "Wikidata" in meta["source"] and meta["queried"].startswith("2026-11-17")
    assert meta["counts"]["persons"] == len(doc["persons"]) > 7000
    assert meta["counts"]["hls"] == sum("hls" in r for r in doc["persons"].values())
    assert all(r["wikidata"].startswith("Q") for r in doc["persons"].values())
    assert not set(doc["ambiguous"]) & set(doc["persons"])
    assert doc["persons"]["1295"]["wikidata"] == "Q1394"                     # Lenin


# ── dodis.py writes sameAs ───────────────────────────────────────────────────

def _kg7():
    spec = importlib.util.spec_from_file_location("kg7_830", Path(__file__).parent / "test_kg_7_dodis_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export(tmp_path, wikidata_map=dodis.WIKIDATA_MAP):
    out = tmp_path / "dodis.nt"
    rep = dodis.export(_kg7()._db(tmp_path), out, validate=True, shacl=True, wikidata_map=wikidata_map)
    return rep, out


def test_the_fixtures_persons_get_wikidata_gnd_hls_and_viaf(tmp_path):
    rep, out = _export(tmp_path)
    g = Graph().parse(str(out), format="nt")
    lenin = next(g.subjects(OWL.sameAs, URIRef("https://dodis.ch/P1295")))
    same = {str(o) for o in g.objects(lenin, OWL.sameAs)}
    assert same == {"https://dodis.ch/P1295", "https://www.wikidata.org/entity/Q1394",
                    "https://d-nb.info/gnd/118640402", "https://hls-dhs-dss.ch/de/articles/028375/",
                    "https://viaf.org/viaf/7393146"}
    assert rep.counts["persons_wikidata"] >= 20 and rep.counts["persons_hls"] >= 15
    assert rep.validation.get("ok"), rep.validation


def test_without_the_crosswalk_dodis_is_built_as_before(tmp_path):
    rep, out = _export(tmp_path, wikidata_map=None)
    assert rep.counts["persons_wikidata"] == 0
    g = Graph().parse(str(out), format="nt")
    persons = {s for s, o in g.subject_objects(OWL.sameAs) if str(o).startswith("https://dodis.ch/P")}
    assert persons and all({str(o) for o in g.objects(p, OWL.sameAs)} == {
        next(str(o) for o in g.objects(p, OWL.sameAs))} for p in persons)   # only the Dodis URI


# ── Human Lineage reaches Dodis, reading only the person lines ───────────────

def test_human_lineage_now_resolves_dodis_persons(tmp_path):
    _, out = _export(tmp_path)
    ids = hl.person_identifiers(hl.load_graphs([out]))
    lenin = next(v for v in ids.values() if v.get("wikidata") == "Q1394")
    assert lenin == {"wikidata": "Q1394", "hls": "028375", "gnd": "118640402"}
    assert len(ids) >= 20


def test_the_person_lines_give_the_same_identifiers_as_the_whole_part(tmp_path):
    _, out = _export(tmp_path)
    full = hl.load_graphs([out])
    slim = hl.load_graphs([out], persons_only=True)
    assert hl.person_identifiers(slim) == hl.person_identifiers(full)
    assert len(slim) < len(full) / 3


def test_the_person_lines_keep_e42_identifiers_with_their_source(tmp_path):
    """KF's medium HLS candidates are E42 typed «hls-kandidat»; an E42 typed
    «hls» counts. Both must survive the slim read exactly as in the full one."""
    from knowledge_hub import bestandsgraph as bg
    b = bg.Build(bg.Dataset(key="t", label="t", version="t"))
    a = b.person("a", "A")
    b.identifier(a, "hls", "012345")
    c = b.person("c", "C")
    b.identifier(c, "hls-kandidat", "054321")
    nt = tmp_path / "t.nt"
    nt.write_text(b.g.serialize(format="nt"), encoding="utf-8")
    slim = hl.person_identifiers(hl.load_graphs([nt], persons_only=True))
    assert slim == hl.person_identifiers(hl.load_graphs([nt])) == {a: {"hls": "012345"}}
