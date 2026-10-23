"""KG-9 (#653): SSRQ in den Bestandsgraphen — Register-RDF plus TEI-Erwähnungen, gegen die Fixture.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_9_ssrq_export.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef, RDF, RDFS

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from knowledge_hub.ontology import registry           # noqa: E402
from knowledge_hub.sources import ssrq                # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "bestandsgraph"
REGISTER = FIX / "ssrq_register.ttl"
TEI = FIX / "ssrq_tei"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("kg9") / "ssrq.nt"
    rep = ssrq.export(REGISTER, TEI, out, validate=True, shacl=True)
    return out, rep, Graph().parse(str(out), format="nt")


def test_der_graph_ist_valide_mit_shacl(built):
    _, rep, _ = built
    assert rep.validation["ok"], rep.validation


def test_die_zaehlungen_stimmen_mit_dem_register(built):
    _, rep, g = built
    reg = Graph().parse(str(REGISTER), format="turtle")
    P = lambda n: URIRef(ssrq.PERS + n)  # noqa: E731
    assert rep.counts["persons"] >= len(list(reg.subjects(RDF.type, P("person"))))   # plus Beziehungsziele? nein — nur Register
    assert rep.counts["groups"] == len(list(reg.subjects(RDF.type, P("family"))))
    assert rep.counts["persons"] == len(set(g.subjects(RDF.type, registry.CRM["E21_Person"]))) - rep.counts.get("tei_only_persons", 0)
    # Namen: jede name-Node mit Text wird E41
    n_names = sum(1 for n in reg.objects(None, P("name"))
                  if (next(reg.objects(n, P("forename")), None) is not None or next(reg.objects(n, P("surname")), None) is not None))
    assert rep.counts["names"] == n_names


def test_zwei_tei_dokumente_mit_ihren_verweisen(built):
    _, rep, g = built
    docs = list(g.subjects(RDF.type, registry.CRM["E31_Document"]))
    assert rep.counts["documents"] == 2 == len(docs)
    assert rep.counts["documents_dated"] == 2
    # Die beiden Dateien tragen 44 Verweise (14+9 und 13+6+2). Orte werden
    # immer geprägt (Register-URI, kein Name); Personen und Gruppen nur, wenn
    # das Register sie kennt — die Fixture kennt fünf Personen, also hängt
    # hier das meiste. Im Vollbau ist das die Zahl, die zählt.
    assert rep.counts["mentions"] + rep.withheld["tei_refs_dangling"] == 44
    assert rep.counts["mentions"] >= 15                     # 9 + 6 Ortsverweise
    assert rep.withheld["tei_refs_unparsed"] == 0
    for d in docs:
        assert list(g.objects(d, registry.CRM["P67_refers_to"]))
        assert (d, registry.CRM["P4_has_time-span"], None) not in g      # Datierung an der Creation


def test_tei_suffixe_werden_auf_den_stamm_gekuerzt():
    assert ssrq.stem("per010051a.01") == "per010051"
    assert ssrq.stem(" org002627\t") == "org002627"
    assert ssrq.stem("per02583") is None
    assert ssrq.stem("sste") is None


def test_gnd_und_hls_werden_same_as_literatur_wird_notiz(built):
    _, rep, g = built
    same = {str(o) for o in g.objects(None, registry.OWL["sameAs"])}
    assert any("d-nb.info/gnd/" in u for u in same) or rep.counts.get("gnd", 0) == 0
    assert any("hls-dhs-dss.ch/de/articles/0" in u for u in same)
    assert not any("permalink.snl.ch" in u or "wikipedia" in u for u in same), "Literatur ist keine Identität"
    assert rep.counts.get("literature_notes", 0) >= 1


def test_orte_haben_register_uri_und_keinen_erfundenen_namen(built):
    _, _, g = built
    places = list(g.subjects(RDF.type, registry.CRM["E53_Place"]))
    assert places
    for p in places:
        assert (p, RDFS.label, None) not in g, p
        assert any("Register/#loc" in str(o) for o in g.objects(p, registry.OWL["sameAs"]))


def test_verwandtschaft_ehe_und_familie(built):
    _, rep, g = built
    assert rep.counts["partnerships"] >= 1 and list(g.subjects(RDF.type, registry.SDH_SLC["C9"]))
    assert rep.counts["parenthood"] >= 1 and list(g.subject_objects(registry.CRM["P152_has_parent"]))
    assert rep.counts["relations"] >= 1 and list(g.subjects(RDF.type, registry.SDH_SLC["C3"]))
    assert rep.counts["memberships"] >= 5
    # Jede C9 hat genau zwei Partner (SHACL hat es schon geprüft; hier als Zahl)
    for c9 in g.subjects(RDF.type, registry.SDH_SLC["C9"]):
        assert len(list(g.objects(c9, registry.SDH_SLC["P20"]))) == 2


def test_der_bearbeitungszeitstempel_wird_nie_ein_datum(built):
    """pers:hist ist, wann eine Bearbeiterin gespeichert hat. 2019 ist kein
    Jahr eines Menschen aus dem 14. Jahrhundert."""
    _, _, g = built
    for ts in g.subjects(RDF.type, registry.CRM["E52_Time-Span"]):
        for p in (registry.CRM["P82a_begin_of_the_begin"], registry.CRM["P82b_end_of_the_end"]):
            for v in g.objects(ts, p):
                assert int(str(v)[:4]) < 1900, (ts, v)


def test_datumsformen():
    assert ssrq._date_literal("1342") == ("1342-01-01", "1342-12-31")
    assert ssrq._date_literal("1505-02-05") == ("1505-02-05", "1505-02-05")
    assert ssrq._date_literal("1493-02") == ("1493-02-01", "1493-02-28")
    assert ssrq._date_literal("1289-1321") == ("1289-01-01", "1321-12-31")
    assert ssrq._date_literal("1505-02-30") is None
    assert ssrq._date_literal("um 1300") is None


def test_eine_unbekannte_beziehung_wird_gezaehlt_nicht_geraten(tmp_path):
    reg = Graph().parse(str(REGISTER), format="turtle")
    P = lambda n: URIRef(ssrq.PERS + n)  # noqa: E731
    s = next(reg.subjects(RDF.type, P("person")))
    t = [x for x in reg.subjects(RDF.type, P("person")) if x != s][0]
    from rdflib import BNode
    n = BNode()
    reg.add((s, P("alchemistOf"), n)); reg.add((n, P("id"), t))
    p = tmp_path / "reg.ttl"; reg.serialize(str(p), format="turtle")
    rep = ssrq.export(p, None, tmp_path / "x.nt")
    assert rep.withheld["unknown_relations"] == 1 and rep.withheld["unknown_relation_kinds"] == {"alchemistOf": 1}


def test_ein_tei_verweis_ohne_registereintrag_haengt(tmp_path):
    tei = tmp_path / "tei"; tei.mkdir()
    (tei / "x.xml").write_text('<TEI><teiHeader><idno>SSRQ-TEST-1</idno><origDate when="1400-01-01"/></teiHeader>'
                               '<text><persName ref="per999999">Niemand</persName></text></TEI>', encoding="utf-8")
    rep = ssrq.export(REGISTER, tei, tmp_path / "x.nt")
    assert rep.counts["documents"] == 1 and rep.withheld["tei_refs_dangling"] == 1 and rep.counts.get("mentions", 0) == 0
