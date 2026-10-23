"""KG-6 (#650): das Vokabular des Bestandsgraphen, von OntoMe verwaltet, und seine Prüfung.

Offline — alles läuft gegen die eingefrorenen Schnappschüsse unter
``knowledge_hub/ontology/snapshots/`` und die Ausschnitte unter
``tests/fixtures/bestandsgraph/``.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_6_ontology.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from rdflib import Graph, Literal, URIRef, RDF

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from knowledge_hub import bestandsgraph as bg              # noqa: E402
from knowledge_hub.ontology import crm as crm_mod          # noqa: E402
from knowledge_hub.ontology import ontome, registry, validate as v   # noqa: E402
import bestandsgraph_fixture as fx                         # noqa: E402


@pytest.fixture(scope="module")
def voc():
    return ontome.load()


@pytest.fixture(scope="module")
def crm():
    return crm_mod.load()


# ── Die Schnappschüsse ───────────────────────────────────────────────────────

def test_der_schnappschuss_liegt_vor_und_traegt_sein_datum(voc):
    assert voc.fetched_at.startswith("2026-")
    assert set(voc.profile_ids) == set(ontome.PROFILES)
    assert len(voc.classes) >= 100 and len(voc.properties) >= 100


def test_die_hierarchie_reicht_von_sdhss_bis_cidoc(voc, crm):
    """Ohne die nachgeschlagenen Elternklassen endete C9 Relationship bei C18;
    mit ihnen bei E1 — und erst dann ist «Range passt» entscheidbar."""
    h = v.Hierarchy(voc, crm)
    E = str(registry.CRM)
    assert h.is_a(registry.SDH_SLC["C9"], E + "E5_Event")
    assert h.is_a(registry.SDH_SLC["C9"], E + "E2_Temporal_Entity")
    assert h.is_a(registry.SDH_SLC["C5"], E + "E2_Temporal_Entity")
    assert h.is_a(registry.SDH_SLC["C13"], E + "E2_Temporal_Entity")
    assert h.is_a(registry.SDH_SLC["C4"], E + "E55_Type")
    assert h.is_a(E + "E31_Document", E + "E89_Propositional_Object")


def test_ontome_kurzformen_und_rdfs_langformen_sind_dieselbe_klasse(voc, crm):
    h = v.Hierarchy(voc, crm)
    assert h.canon(str(registry.CRM) + "E21") == str(registry.CRM["E21_Person"])
    assert h.is_a(str(registry.CRM) + "E21", str(registry.CRM["E39_Actor"]))


def test_e40_legal_body_ist_in_7_1_3_nicht_mehr_da(crm):
    """Der Korpusexporter bildet ORG darauf ab (KG-12)."""
    assert str(registry.CRM["E40_Legal_Body"]) not in crm.classes
    assert str(registry.CRM["E74_Group"]) in crm.classes


# ── Das Register ─────────────────────────────────────────────────────────────

def test_jeder_ontome_begriff_des_registers_ist_nachgewiesen(voc, crm):
    """Die Zusicherung, um die es geht: kein Begriff im Register, den weder
    OntoMe noch das RDFS kennt. Wer einen erfindet, scheitert hier."""
    h = v.Hierarchy(voc, crm)
    prop_uris = {h.canon(p.uri) for p in voc.properties.values()} | set(crm.properties)
    fehlend = []
    for t in registry.TERMS:
        if t.source != "ontome":
            continue
        if t.kind == "class" and not h.known_class(t.uri):
            fehlend.append(t.uri)
        if t.kind == "property" and h.canon(t.uri) not in prop_uris:
            fehlend.append(t.uri)
    assert not fehlend, fehlend


def test_das_register_ist_frei_von_erfundenem_sdhss():
    """Die 40 Begriffe unter https://sdhss.org/ontology/ (ohne Unter-Namensraum)
    sind erfunden — OntoMe antwortet dort «OntoME URI not found». Keiner davon
    darf im Register stehen."""
    for t in registry.TERMS:
        assert not t.uri.startswith("https://sdhss.org/ontology/") or \
            t.uri.startswith(("https://sdhss.org/ontology/core/",
                              "https://sdhss.org/ontology/social-life-core/",
                              "https://sdhss.org/ontology/crm-supplement/",
                              "https://sdhss.org/ontology/sources-information-metadata/")), t.uri


def test_das_register_hat_keine_doppelten_uris():
    assert len(registry.TERMS) == len(registry.BY_URI)


# ── Der Fixture-Graph aus den drei Beständen ─────────────────────────────────

@pytest.fixture(scope="module")
def fixture_graph():
    return fx.all_graphs()


def test_der_fixture_graph_aus_drei_bestaenden_ist_valide(fixture_graph):
    rep = v.validate(fixture_graph)
    assert rep.ok, rep.render()
    assert rep.triples > 400
    # Jeder Bestand ist drin, mit Dokument und Erwähnungen.
    docs = set(fixture_graph.subjects(RDF.type, registry.CRM["E31_Document"]))
    assert len(docs) == 3
    for d in docs:
        assert list(fixture_graph.objects(d, registry.CRM["P67_refers_to"]))


def test_die_datierung_haengt_an_der_creation_nicht_am_dokument(fixture_graph):
    """P4 verlangt eine E2; ein Dokument ist keine. Die Prüfung hat das beim
    ersten Bau gemeldet, und seither hängt die Zeit an der E65 Creation."""
    for d in fixture_graph.subjects(RDF.type, registry.CRM["E31_Document"]):
        assert (d, registry.CRM["P4_has_time-span"], None) not in fixture_graph


def test_jeder_kernknoten_traegt_seine_herkunft(fixture_graph):
    for cls in (registry.CRM["E31_Document"], registry.CRM["E21_Person"], registry.CRM["E53_Place"],
                registry.CRM["E74_Group"], registry.SDH_SLC["C9"], registry.SDH_SLC["C5"]):
        for n in fixture_graph.subjects(RDF.type, cls):
            assert (n, registry.PROV["wasDerivedFrom"], None) in fixture_graph, n


def test_zweimal_gebaut_ist_byteidentisch():
    a = fx.all_graphs().serialize(format="turtle")
    b = fx.all_graphs().serialize(format="turtle")
    assert a == b


def test_ssrq_verweise_werden_auf_den_registerstamm_gekuerzt():
    assert fx._strip_ref("per010051a.01") == "per010051"
    assert fx._strip_ref("loc000003") == "loc000003"


# ── Gegenproben: die Prüfung bemerkt das Falsche ─────────────────────────────

def _minimal():
    b = bg.Build(bg.Dataset("kf", "KF", "test"))
    d = b.document("U-1", "U 1", identifier=("kf", "U-1"))
    p = b.person("per000001", "Johann", identifier=("kf", "per000001"))
    b.mention(d, p)
    return b, d, p


def test_der_minimalgraph_ist_valide():
    b, _, _ = _minimal()
    assert v.validate(b.g).ok


def test_eine_erfundene_property_wird_gemeldet():
    b, _, p = _minimal()
    b.g.add((p, URIRef("https://sdhss.org/ontology/readingRole"), Literal("working")))
    rep = v.validate(b.g)
    assert not rep.ok
    assert any(f.kind == "unknown-property" and f.predicate.endswith("readingRole") for f in rep.findings)


def test_e40_wird_als_unbekannte_klasse_gemeldet():
    b, _, _ = _minimal()
    b.g.add((URIRef("https://tei.dh.unibe.ch/id/kf/group/x"), RDF.type, registry.CRM["E40_Legal_Body"]))
    rep = v.validate(b.g)
    assert any(f.kind == "unknown-class" and f.subject.endswith("E40_Legal_Body") for f in rep.findings)


def test_ein_literal_an_p2_verletzt_den_range():
    b, _, p = _minimal()
    b.g.add((p, registry.CRM["P2_has_type"], Literal("hub")))
    rep = v.validate(b.g)
    kinds = {f.kind for f in rep.findings if f.predicate.endswith("P2_has_type")}
    assert "range" in kinds and "shacl" in kinds


def test_ein_falscher_range_wird_gemeldet():
    """E21 P67 Literal — P67 verlangt eine E1-Ressource."""
    b, d, p = _minimal()
    b.g.add((d, registry.CRM["P67_refers_to"], Literal("Thun")))
    rep = v.validate(b.g)
    assert any(f.kind == "range" and f.predicate.endswith("P67_refers_to") for f in rep.findings)


def test_eine_falsche_domain_wird_gemeldet():
    """P4 an einem Dokument — die Lage, die der erste Bau hatte."""
    b, d, _ = _minimal()
    b.timespan(d, 1300, 1310)
    rep = v.validate(b.g)
    assert any(f.kind == "domain" and f.predicate.endswith("P4_has_time-span") for f in rep.findings)


def test_eine_partnerschaft_mit_einem_partner_scheitert_an_der_form():
    b, _, p = _minimal()
    u = URIRef("https://tei.dh.unibe.ch/id/kf/partnership/x")
    b.g.add((u, RDF.type, registry.SDH_SLC["C9"])); b.g.add((u, registry.SDH_SLC["P20"], p))
    b.g.add((u, registry.PROV["wasDerivedFrom"], b.dataset_uri))
    rep = v.validate(b.g)
    assert any(f.kind == "shacl" and "P20" in f.detail for f in rep.findings)


def test_ein_knoten_ohne_herkunft_wird_gemeldet():
    b, _, _ = _minimal()
    b.g.add((URIRef("https://tei.dh.unibe.ch/id/kf/person/ohne"), RDF.type, registry.CRM["E21_Person"]))
    rep = v.validate(b.g)
    assert any(f.kind == "provenance" and f.subject.endswith("/ohne") for f in rep.findings)


def test_same_as_in_unbekannten_namensraum_scheitert_an_der_form():
    b, _, p = _minimal()
    b.same_as(p, ["https://example.org/erfunden/1"])
    rep = v.validate(b.g)
    assert any(f.kind == "shacl" and "sameAs" in f.detail for f in rep.findings)


# ── Der Korpusgraph (KG-12, #659) ────────────────────────────────────────────

def _korpus_doc():
    import care_flag
    return {"doc_id": "doc-1", "transcription": "t",
            "description": {"source_description": "d", "care_flag": care_flag.classified(True)},
            "entities": {"entities": [
                {"text": "Heinrich", "type": "PERSON", "normalised": "Heinrich", "gnd": "12175376X"},
                {"text": "Kloster", "type": "ORG", "normalised": "Kloster", "hub_id": "hub_p_1"}]},
            "recognitions": [{"engine": "trocr", "model_id": "m1", "text": "r", "confidence": 0.7, "error": None}],
            "a_meta": {"fusion_strategy": "vote", "fusion_agreement_cer": 0.05},
            "closest_reading": {"chosen": ["trocr"], "combined": False,
                                "editor_pseudonym": "editor-1", "confirmed_at": "2026-10-01T10:00:00Z"}}


def test_der_korpusgraph_ist_valide(tmp_path):
    """Gemessen, nicht behauptet — und umgedreht gegenüber KG-6: bis KG-12
    prägte der Exporter aus KG-2/3 Begriffe unter https://sdhss.org/ontology/
    (14 Properties, 4 Klassen am Fixture-Dokument), schrieb Literale an P2
    und bildete ORG auf E40 ab. Jetzt stehen seine Begriffe unter shb: im
    Register, ORG ist E74 mit sdh:P99, die Id-Quelle ein E55, jeder Knoten
    trägt seine Herkunft — und die Prüfung läuft als Tor in corpus_build."""
    import json
    from knowledge_hub import corpus_build as cb

    (tmp_path / "doc-1_pipeline.json").write_text(json.dumps(_korpus_doc()), encoding="utf-8")
    out = tmp_path / "corpus.ttl"
    report = cb.build(tmp_path, out)
    assert report.validation["ok"] is True

    g = Graph().parse(str(out), format="turtle")
    rep = v.validate(g)
    assert rep.ok, rep.render()
    assert not [f for f in rep.findings if f.kind != "range-untyped"]
    # Kein Begriff mehr unter der SDHSS-Wurzel ohne Unter-Namensraum.
    bad = [t for t in {*g.predicates(), *g.objects(None, RDF.type)}
           if str(t).startswith("https://sdhss.org/ontology/")
           and not str(t).startswith(("https://sdhss.org/ontology/core/",
                                      "https://sdhss.org/ontology/crm-supplement/"))]
    assert bad == [], bad
    # E74 statt E40, E55 an P2, Herkunft an Dokument, Person und Gruppe.
    org = URIRef("https://tei.dh.unibe.ch/id/org/kloster")
    assert (org, RDF.type, registry.CRM["E74_Group"]) in g
    assert (org, registry.SDH["P99"], None) in g
    assert all(not isinstance(o, Literal) for o in g.objects(None, registry.CRM["P2_has_type"]))
    for n in (org, URIRef("https://tei.dh.unibe.ch/id/person/gnd-12175376x"),
              URIRef("https://tei.dh.unibe.ch/id/document/doc-1")):
        assert (n, registry.PROV["wasDerivedFrom"], None) in g, n
    # Die Auswahl ist eine E13, nicht eine erfundene Klasse.
    assert list(g.subjects(RDF.type, registry.CRM["E13_Attribute_Assignment"]))


def test_das_tor_weist_einen_erfundenen_begriff_im_korpusgraphen_ab(tmp_path, monkeypatch):
    """Gegenprobe: ein alter sdhss:-Begriff im Graphen, und der Bau schreibt
    keine Datei. Das ist das Tor, das KG-6 bewusst nicht gesetzt hat."""
    import json

    from knowledge_hub import corpus_build as cb
    from knowledge_hub import rdf_export as rx

    real = rx.document_to_rdf

    def tainted(g, doc, ns=rx.AH):
        d = real(g, doc, ns)
        g.add((d, URIRef("https://sdhss.org/ontology/readingRole"), Literal("working")))
        return d

    monkeypatch.setattr(rx, "document_to_rdf", tainted)
    (tmp_path / "doc-1_pipeline.json").write_text(json.dumps(_korpus_doc()), encoding="utf-8")
    out = tmp_path / "corpus.ttl"
    with pytest.raises(cb.ValidationError) as info:
        cb.build(tmp_path, out)
    assert any(f.kind == "unknown-property" and f.predicate.endswith("readingRole")
               for f in info.value.report.findings)
    assert not out.exists()
    assert not cb.manifest_path(out).exists()
    assert cb.main(["--outputs", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists()
