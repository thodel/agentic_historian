"""Tests for KG-2 (#328): document-centric CIDOC-CRM + PROV corpus RDF export.

Offline — no MCP, no network. Run from the repo root:
    pytest agentic_historian/tests/test_kg_2_corpus_rdf.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph, Literal, RDF, URIRef

from knowledge_hub import rdf_export as rx

CRM = rx.CRM
OWL = rx.OWL


def _pipeline(doc_id="doc-1", recognitions=None, entities=None, **extra):
    doc = {
        "doc_id": doc_id,
        "transcription": "unser fruntlich gruos vor liebe getruwe",
        "entities": {"entities": entities if entities is not None else []},
        "recognitions": recognitions or [],
        "a_meta": {"pages": 1, "qa_score": 0.8, "source": "grouped"},
    }
    doc.update(extra)
    return doc


def _write(tmp_path, doc):
    (tmp_path / f"{doc['doc_id']}_pipeline.json").write_text(
        json.dumps(doc), encoding="utf-8")
    return tmp_path


TWO_ENGINES = [
    {"engine": "trocr", "model_id": "trocr-kurrent-xvi", "text": "reading one",
     "confidence": 0.7, "error": None},
    {"engine": "kraken", "model_id": "kraken-de", "text": "reading two",
     "confidence": 0.6, "error": None},
]


# ── many readings, none of them "the text" ───────────────────────────────────

def test_two_readings_produce_two_linguistic_objects_both_attached():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(recognitions=TWO_ENGINES))

    d_uri = rx.document_uri("doc-1")
    readings = set(g.objects(d_uri, CRM["P128_carries"]))
    # two engines + the working (fused) reading
    assert len(readings) == 3
    for r in readings:
        assert (r, RDF.type, CRM["E33_Linguistic_Object"]) in g

    texts = {str(t) for r in readings
             for t in g.objects(r, CRM["P190_has_symbolic_content"])}
    assert {"reading one", "reading two"} <= texts


def test_no_symbolic_content_is_ever_attached_to_the_document():
    """The document must never carry a single text — that is the whole point."""
    g = Graph()
    rx.document_to_rdf(g, _pipeline(recognitions=TWO_ENGINES))

    d_uri = rx.document_uri("doc-1")
    assert list(g.objects(d_uri, CRM["P190_has_symbolic_content"])) == []
    # and nothing marks one reading as the document's authoritative text
    assert list(g.objects(d_uri, rx.SDHSS["hasClosestReading"])) == []


def test_engine_readings_are_distinguishable_by_their_provenance_stub():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(recognitions=TWO_ENGINES))
    engines = {str(o) for o in g.objects(None, rx.SDHSS["engine"])}
    assert {"trocr", "kraken"} <= engines


# ── mentions belong to the reading, not the document ─────────────────────────

PERSON = {"text": "Heinrich von Wiler", "type": "PERSON",
          "normalised": "Heinrich von Wiler", "gnd": "12175376X"}


def test_mention_is_attached_to_the_reading_not_the_document():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(recognitions=TWO_ENGINES, entities=[PERSON]))

    d_uri = rx.document_uri("doc-1")
    subjects = set(g.subjects(CRM["P67_refers_to"], None))
    assert subjects, "no mention triple emitted"
    # the subject is a reading, never the document
    assert d_uri not in subjects
    for s in subjects:
        assert (s, RDF.type, CRM["E33_Linguistic_Object"]) in g


def test_only_the_reading_agent_c_read_carries_the_mentions():
    """Claiming every engine reading mentions the person would be a fabrication."""
    g = Graph()
    rx.document_to_rdf(g, _pipeline(recognitions=TWO_ENGINES, entities=[PERSON]))

    subjects = set(g.subjects(CRM["P67_refers_to"], None))
    assert len(subjects) == 1
    working = next(iter(subjects))
    assert (working, rx.SDHSS["readingRole"], Literal("working")) in g


# ── link out, never invent ───────────────────────────────────────────────────

def test_person_with_gnd_gets_owl_sameas_to_the_public_uri():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(entities=[PERSON]))

    same = {str(o) for o in g.objects(None, OWL["sameAs"])}
    assert "https://d-nb.info/gnd/12175376X" in same


def test_authority_uris_match_the_table_kg1_persists():
    """KG-1 and KG-2 must mint identical authority URIs or the graph won't join."""
    from entity_index import authority_uri
    g = Graph()
    rx.document_to_rdf(g, _pipeline(entities=[
        {"text": "X", "type": "PERSON", "normalised": "X",
         "gnd": "118540238", "hls": "12345", "wikidata": "Q42"},
    ]))
    same = {str(o) for o in g.objects(None, OWL["sameAs"])}
    for source, ident in (("gnd", "118540238"), ("hls", "12345"),
                          ("wikidata", "Q42")):
        assert authority_uri(source, ident) in same


def test_source_without_public_uri_gets_local_identifier_and_no_invented_uri():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(entities=[
        {"text": "Henin Rost", "type": "PERSON", "normalised": "Henin Rost",
         "hub_id": "hbls:1:51"},
    ]))

    # no owl:sameAs at all — nothing public to point at
    assert list(g.objects(None, OWL["sameAs"])) == []

    # …but the id survives as a literal on an E42 Identifier
    idents = [s for s in g.subjects(RDF.type, CRM["E42_Identifier"])]
    labels = {str(o) for i in idents for o in g.objects(i, __import__("rdflib").RDFS.label)}
    assert "hbls:1:51" in labels

    # the person node is in OUR namespace, not an authority's
    people = list(g.subjects(RDF.type, CRM["E21_Person"]))
    assert people and all(str(p).startswith(rx.CORPUS_BASE) for p in people)


def test_entity_nodes_are_always_minted_in_our_namespace():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(entities=[PERSON]))
    people = list(g.subjects(RDF.type, CRM["E21_Person"]))
    assert people and all(str(p).startswith(rx.CORPUS_BASE) for p in people)


def test_entity_uri_is_stable_across_documents_via_authority_id():
    a = rx.entity_uri("PERSON", "Heinrich von Wiler", {"gnd": "12175376X"})
    b = rx.entity_uri("PERSON", "Hainricus de Villa", {"gnd": "12175376X"})
    assert a == b          # same GND → same node, despite different spellings


# ── document identity and the source image (#208) ────────────────────────────

def test_document_carries_its_archival_id_and_source_image():
    g = Graph()
    rx.document_to_rdf(g, _pipeline(source_url="https://example.org/img/001r.jpg"))
    d_uri = rx.document_uri("doc-1")

    assert (d_uri, RDF.type, CRM["E22_Human-Made_Object"]) in g
    assert (d_uri, rx.SCHEMA["image"],
            URIRef("https://example.org/img/001r.jpg")) in g
    idents = list(g.objects(d_uri, CRM["P1_is_identified_by"]))
    assert idents


def test_document_without_source_url_gets_no_image_triple():
    g = Graph()
    rx.document_to_rdf(g, _pipeline())
    assert list(g.objects(rx.document_uri("doc-1"), rx.SCHEMA["image"])) == []


# ── corpus build, validity, determinism ──────────────────────────────────────

def test_corpus_graph_is_built_from_pipeline_files(tmp_path):
    _write(tmp_path, _pipeline("doc-1", recognitions=TWO_ENGINES, entities=[PERSON]))
    _write(tmp_path, _pipeline("doc-2", entities=[PERSON]))

    g = rx.corpus_to_graph(tmp_path)
    docs = set(g.subjects(RDF.type, CRM["E31_Document"]))
    assert docs == {rx.document_uri("doc-1"), rx.document_uri("doc-2")}


def test_entities_only_file_is_skipped_without_a_reading_to_anchor_it(tmp_path):
    """A mention with no reading cannot be modelled honestly, so it is skipped."""
    (tmp_path / "orphan_entities.json").write_text(
        json.dumps({"entities": [PERSON]}), encoding="utf-8")
    g = rx.corpus_to_graph(tmp_path)
    assert len(g) == 0


def test_graph_is_valid_turtle_and_round_trips(tmp_path):
    _write(tmp_path, _pipeline("doc-1", recognitions=TWO_ENGINES, entities=[PERSON]))
    out = rx.corpus_to_turtle(tmp_path, tmp_path / "corpus.ttl")

    reparsed = Graph().parse(out, format="turtle")
    original = rx.corpus_to_graph(tmp_path)
    assert len(reparsed) == len(original)
    assert set(reparsed) == set(original)


def test_export_is_byte_identical_on_unchanged_input(tmp_path):
    """Determinism — the graph must be diffable and citable across releases."""
    _write(tmp_path, _pipeline("doc-1", recognitions=TWO_ENGINES, entities=[PERSON]))
    _write(tmp_path, _pipeline("doc-2", entities=[PERSON]))

    a = Path(rx.corpus_to_turtle(tmp_path, tmp_path / "a.ttl")).read_text(encoding="utf-8")
    b = Path(rx.corpus_to_turtle(tmp_path, tmp_path / "b.ttl")).read_text(encoding="utf-8")
    assert a == b


def test_empty_outputs_dir_yields_an_empty_graph(tmp_path):
    assert len(rx.corpus_to_graph(tmp_path)) == 0


def test_malformed_pipeline_file_is_skipped(tmp_path):
    (tmp_path / "broken_pipeline.json").write_text("{not json", encoding="utf-8")
    _write(tmp_path, _pipeline("doc-1"))
    g = rx.corpus_to_graph(tmp_path)
    assert set(g.subjects(RDF.type, CRM["E31_Document"])) == {rx.document_uri("doc-1")}


# ── rdflib must be a runtime dependency (the export runs in production) ──────

def test_rdflib_is_declared_as_a_runtime_dependency():
    pyproject = (PKG / "pyproject.toml").read_text(encoding="utf-8")
    deps = pyproject.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert "rdflib" in deps, "rdflib must be a runtime dep, not dev-only"
