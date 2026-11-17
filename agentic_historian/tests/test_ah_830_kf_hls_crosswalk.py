"""knowledge_hub/crosswalks/kf_hls.py — Königsfelden persons → HLS, rebuilt (#830).

The October crosswalk (162 persons) was lost with OpenClaw and recorded no
method. The rebuild proposes candidates from Wikidata (P902) and lets the HLS
article decide: its title and the life dates of its lead. ``high`` needs a
year that agrees; ``medium`` only a first attestation inside the article's
window and no rival; two fitting articles give nothing.

The leads below are HLS's own, verbatim (17.11.2026).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_830_kf_hls_crosswalk.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub.crosswalks import kf_hls as m


def _page(title, lead):
    return (f"<html><head><title>{title}</title>\n"
            f'<meta name="description" content="{lead}"/></head></html>')


def _art(hid, title, lead):
    return m.parse_hls(hid, _page(title, lead))


MANESSE = _art("018090", "Manesse, Rüdiger VII.",
               "Erstmals erwähnt 1336 Zürich, ✝︎ 9.11.1383 Zürich, von Zürich. Sohn des Ulrich "
               "Manesse Manegg, Ritters, und der Adelheid von Breitenlandenberg.")
ALBRECHT_I = _art("029202", "Albrecht I.",
                  "∗︎ um 1255, erstmals erwähnt 3.7.1265, ✝︎ 1.5.1308 bei Königsfelden, ❒︎ Dom zu "
                  "Speyer. Ältester Sohn des Rudolf I. von Habsburg")
SCHMID = _art("012260", "Schmid, Rudolf",
              "Erstmals erw. 1560, ✝︎ vor dem 6.2.1587, ref., vermutlich von Brugg oder aus dessen "
              "Umgebung. ∞︎ 1) Sara Maier, 1580 2) …")
SAX = _art("011930", "Sax, Heinrich von",
           "1235 erstmals, 1258 letztmals erwähnt. Vertreter der Freiherren von Sax")


def _kf(pid="per1", fn="", sn="", gen="", main="", birth="", death="", first=None, occ=""):
    return m.KFPerson(pid, fn, sn, gen, main or f"{fn} {sn}".strip(), occ, birth, death, first)


# ── reading HLS's lead ───────────────────────────────────────────────────────

@pytest.mark.parametrize("lead,want", [
    ("Erstmals erwähnt 1336 Zürich, ✝︎ 9.11.1383 Zürich, von Zürich.", (None, 1383, 1336, None)),
    ("∗︎ um 1255, erstmals erwähnt 3.7.1265, ✝︎ 1.5.1308 bei Königsfelden, ❒︎ Dom", (1255, 1308, 1265, None)),
    ("1235 erstmals, 1258 letztmals erwähnt. Vertreter der Freiherren", (None, None, 1235, 1258)),
    ("Urkundlich zwischen 1158 und 1192 erwähnt, ✝︎ vor dem 30.8.1196 . Sohn", (None, 1196, 1158, 1192)),
    ("∗︎ 1355, ✝︎ 9./10.10.1423 Horst (Niederlande), ❒︎ Kathedrale Utrecht.", (1355, 1423, None, None)),
    ("1258 erstmals erwähnt, ✝︎ 3., 5. oder 6.9.1296, ❒︎ im Basler Münster.", (None, 1296, 1258, None)),
    ("Erstmals 1448, letztmals am 17.10.1497 erwähnt, von Baar. 1448-1449", (None, None, 1448, 1497)),
    ("1467 -1517 bezeugt, von Schaffhausen. Sohn des Stadtschreibers", (None, None, 1467, 1517)),
    ("Ab 1344 erwähnt, ✝︎ vor dem 15.6.1349. Der Prämonstratenser", (None, 1349, 1344, None)),
    ("✝︎ wahrscheinlich am 25.10.912, ❒︎ Abtei Saint-Maurice.", (None, 912, None, None)),
    ("❒︎ 4.10.1747 Neuenburg, ref., von Neuenburg.", (None, None, None, None)),
])
def test_the_lead_gives_the_life_dates(lead, want):
    a = _art("1", "X, Y", lead)
    assert (a.birth, a.death, a.first, a.last) == want


def test_a_marriage_line_is_not_a_mention():
    assert (SCHMID.first, SCHMID.last, SCHMID.death) == (1560, None, 1587)


def test_uncertain_dates_are_marked_approximate():
    assert ALBRECHT_I.birth_approx and not ALBRECHT_I.death_approx
    assert SCHMID.death_approx                                  # «vor dem»


def test_a_page_without_title_or_lead_is_unreadable():
    assert m.parse_hls("1", "<html></html>") is None


# ── names ────────────────────────────────────────────────────────────────────

def test_forename_variants_are_one_name():
    assert m.fkeys("Hans Jakob") == m.fkeys("Johann Jakob") == ("johann", "jakob")
    assert m.fkeys("Henmann") == ("johann",)


def test_a_second_forename_is_not_ignored():
    hans = _kf(fn="Hans", sn="Nägeli", first=1423)
    hr = _art("2", "Nägeli, Hans Rudolf", "∗︎ um 1499, ✝︎ 1579 Bern.")
    assert m.names_agree(hans, hr) is None


def test_a_forename_is_never_a_surname():
    assert m.surname_tokens("Hans Ulrich") == set()


def test_a_ruler_is_found_by_regnal_number_and_family():
    p = _kf(fn="Albrecht", sn="Habsburg-Österreich", gen="I.", birth="um 1255", death="01.05.1308")
    assert m.names_agree(p, ALBRECHT_I) == "regnal number"


# ── the verdict ──────────────────────────────────────────────────────────────

def test_an_agreeing_death_year_is_high():
    p = _kf(fn="Rüdiger", sn="Manesse", gen="VII.", death="09.11.1383", first=1336)
    v, basis = m.verdict(p, MANESSE)
    assert v == "high" and basis == ["forename", "surname", "death year"]


def test_death_years_apart_reject():
    p = _kf(fn="Rüdiger", sn="Manesse", death="1390")
    assert m.verdict(p, MANESSE)[0] == "reject"


def test_attested_before_birth_rejects():
    p = _kf(fn="Albrecht", sn="Habsburg-Österreich", gen="I.", first=1240)
    assert m.verdict(p, ALBRECHT_I) == ("reject", ["forename", "regnal number",
                                                   "attested 1240 before HLS birth 1255"])


def test_an_attestation_in_the_window_is_only_window():
    assert m.verdict(_kf(fn="Heinrich", sn="Sax", first=1250), SAX)[0] == "window"
    assert m.verdict(_kf(fn="Heinrich", sn="Sax", first=1300), SAX)[0] == "reject"


# ── the whole build ──────────────────────────────────────────────────────────

def _wd(q, hls, *names, b=(), d=()):
    return m.WDPerson(q, {hls}, set(names), set(b), set(d), set())


def test_build_picks_one_names_ambiguity_and_marks_shared_articles():
    kf = [
        _kf("per000325", fn="Rüdiger", sn="Manesse", gen="VII.", death="09.11.1383", first=1336),
        _kf("per000326", fn="Rüdiger", sn="Manesse", death="1383", first=1340),
        _kf("per000900", fn="Heinrich", sn="Sax", first=1250),
        _kf("per000902", fn="Rudolf", sn="Schmid", first=1570),
    ]
    wd = [_wd("Q1", "018090", "Rüdiger Manesse", d=[1383]),
          _wd("Q2", "011930", "Heinrich von Sax"),
          _wd("Q3", "012260", "Rudolf Schmid", d=[1587]),
          _wd("Q4", "099999", "Rudolf Schmid")]
    arts = {"018090": MANESSE, "011930": SAX, "012260": SCHMID,
            "099999": _art("099999", "Schmid, Rudolf", "Erwähnt zwischen 1565 und 1590, von Bern.")}
    res = m.build(kf, wd, arts.get, log=lambda *_: None)
    p = res["persons"]
    assert p["per000325"]["hls_id"] == "018090" and p["per000325"]["confidence"] == "high"
    assert p["per000326"]["confidence"] == "high"
    assert p["per000325"]["same_hls_as"] == ["per000326"]        # the register's duplicates, both high
    assert p["per000900"]["confidence"] == "medium" and "same_hls_as" not in p["per000900"]
    assert "per000902" not in p and res["ambiguous"]["per000902"] == ["012260", "099999"]


def test_the_committed_map_says_how_it_was_made():
    doc = json.loads((PKG / "knowledge_hub" / "crosswalks" / "kf_hls_id_map.json").read_text())
    meta = doc["_meta"]
    assert meta["people_xml"]["repo"] == "Staatsarchiv-Aargau/koenigsfelden-data"
    assert meta["counts"]["high"] == sum(r["confidence"] == "high" for r in doc["persons"].values())
    for pid, r in doc["persons"].items():
        assert pid.startswith("per") and len(r["hls_id"]) == 6 and r["confidence"] in ("high", "medium")
        assert r["basis"][:1] == ["forename"]


# ── kf.py reads the map ──────────────────────────────────────────────────────

def _kg8():
    import importlib.util
    spec = importlib.util.spec_from_file_location("kg8_830", Path(__file__).parent / "test_kg_8_kf_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export(tmp_path, hls_map):
    from rdflib import Graph

    from knowledge_hub.sources import kf

    db = _kg8()._db(tmp_path)
    out = tmp_path / "kf.nt"
    rep = kf.export(db, out, validate=True, shacl=True, hls_map=hls_map)
    return rep, Graph().parse(str(out), format="nt")


def _map(tmp_path, persons):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"_meta": {}, "persons": persons}), encoding="utf-8")
    return p


def test_high_is_same_as_and_medium_only_a_typed_identifier(tmp_path):
    from rdflib import URIRef
    from rdflib.namespace import OWL

    from knowledge_hub.bestandsgraph import CRM

    hls_map = _map(tmp_path, {
        "per017157": {"hls_id": "014119", "confidence": "high", "basis": ["forename", "surname", "death year"],
                      "hls_title": "Feer, Petermann"},
        "per005779": {"hls_id": "123456", "confidence": "medium", "hls_title": "Hohenlandenberg, Hugo von",
                      "basis": ["forename", "surname", "KF attested 1480 in HLS 1470–1532"]},
        "per016072": {"hls_id": "654321", "confidence": "low"},
    })
    rep, g = _export(tmp_path, hls_map)
    assert (rep.counts["persons_hls_same_as"], rep.counts["persons_hls_candidate"]) == (1, 1)
    hls = URIRef("https://hls-dhs-dss.ch/de/articles/014119/")
    assert len(list(g.subjects(OWL.sameAs, hls))) == 1
    assert not list(g.subjects(OWL.sameAs, URIRef("https://hls-dhs-dss.ch/de/articles/123456/")))
    cand = [e for e in g.subjects(CRM["P190_has_symbolic_content"], None)
            if str(g.value(e, CRM["P190_has_symbolic_content"])) == "123456"]
    assert len(cand) == 1
    typ = g.value(cand[0], CRM["P2_has_type"])
    assert "hls-kandidat" in str(typ)
    assert rep.validation.get("ok")


def test_human_lineage_reads_the_same_as_and_not_the_candidate(tmp_path):
    from knowledge_hub.sources import humanlineage as hl

    hls_map = _map(tmp_path, {
        "per017157": {"hls_id": "014119", "confidence": "high", "basis": [], "hls_title": ""},
        "per005779": {"hls_id": "123456", "confidence": "medium", "basis": [], "hls_title": ""},
    })
    _, g = _export(tmp_path, hls_map)
    ids = hl.person_identifiers(g)
    assert sorted(v["hls"] for v in ids.values() if v.get("hls")) == ["014119"]


def test_no_map_builds_as_before(tmp_path):
    rep, _ = _export(tmp_path, None)
    assert (rep.counts["persons_hls_same_as"], rep.counts["persons_hls_candidate"]) == (0, 0)


SIGISMUND = _art("029208", "Sigismund",
                 "∗︎ 15.2.1368 Nürnberg, ✝︎ 9.12.1437 Znaim (Mähren, heute Znojmo, Tschechische "
                 "Republik). Sohn von König Karl IV. und der Elisabeth von Pommern.")


def test_a_ruler_whose_lead_names_no_family_needs_both_years_exactly():
    p = _kf(fn="Sigismund", sn="Luxemburg Luxemburgicus", birth="15.02.1368", death="09.12.1437")
    assert m.verdict(p, SIGISMUND) == ("high", ["forename", "birth and death year"])
    only_death = _kf(fn="Sigismund", sn="Luxemburg", death="09.12.1437")
    assert m.verdict(only_death, SIGISMUND)[0] == "reject"
    approx = _kf(fn="Sigismund", sn="Luxemburg", birth="um 1368", death="09.12.1437")
    assert m.verdict(approx, SIGISMUND)[0] == "reject"


def test_names_nested_by_language_are_read(tmp_path):
    x = tmp_path / "people.xml"
    x.write_text('''<TEI xmlns="http://www.tei-c.org/ns/1.0"><listPerson>
      <person xml:id="per000712"><persName type="full">
        <persName xml:lang="de"><forename>Sigismund</forename><surname>Luxemburg</surname></persName>
        <persName xml:lang="la"><forename>Sigismundus</forename><surname>Luxemburgicus</surname></persName>
      </persName><persName type="main">Sigismund von Luxemburg</persName>
      <birth>15.02.1368</birth><death>09.12.1437</death></person></listPerson></TEI>''', encoding="utf-8")
    (p,) = m.load_kf(x)
    assert (p.forename, p.fk, p.stoks) == ("Sigismund", ("sigismund",), {"luxemburg", "luxemburgicus"})


def test_one_name_kept_apart_by_the_editors_is_never_a_medium_match():
    kf = [_kf(f"per00090{i}", fn="Heinrich", sn="Sax", first=1250 + i) for i in range(2)]
    res = m.build(kf, [_wd("Q2", "011930", "Heinrich von Sax")], {"011930": SAX}.get,
                  log=lambda *_: None)
    assert res["persons"] == {} and set(res["ambiguous"]) == {"per000900", "per000901"}
