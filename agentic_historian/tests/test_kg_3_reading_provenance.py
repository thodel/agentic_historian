"""Tests for KG-3 (#329): readings as competing claims with provenance.

Offline — no MCP, no network. Run from the repo root:
    pytest agentic_historian/tests/test_kg_3_reading_provenance.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph, Literal, RDF, RDFS

from knowledge_hub import rdf_export as rx

CRM, PROV, SDHSS = rx.CRM, rx.PROV, rx.SDHSS


def _rec(engine, model_id, text, confidence=0.5, **kw):
    d = {"engine": engine, "model_id": model_id, "text": text,
         "confidence": confidence, "error": None}
    d.update(kw)
    return d


# The live BAT_664_r_00027 case: seven readings, 105% pairwise CER, and the
# lowest-scored model produced the best text.
SEVEN = [
    _rec("trocr", "trocr-kurrent-xvi-xvii", "reading a", 0.81),
    _rec("escriptmask", "escriptmask-v2", "reading b", 0.22),
    _rec("kraken", "kraken-de-1", "reading c", 0.55),
    _rec("kraken2", "kraken-de-2", "reading d", 0.51),
    _rec("party", "party-medieval", "reading e", 0.44),
    _rec("vlm", "qwen3-vl-30b", "reading f", 0.63),
    _rec("hfocr", "trocr-base", "reading g", 0.30),
]


def _pipeline(doc_id="BAT_664", recognitions=None, closest=None, a_meta=None):
    doc = {
        "doc_id": doc_id,
        "transcription": "the working text",
        "entities": {"entities": []},
        "recognitions": recognitions if recognitions is not None else list(SEVEN),
        "a_meta": a_meta if a_meta is not None else {
            "fusion_strategy": "vote+no-merge",
            "fusion_agreement_cer": 1.05,
            "fusion_arbitrated": 0,
            "fusion_llm_skipped": True,
        },
    }
    if closest is not None:
        doc["closest_reading"] = closest
    return doc


def _graph(**kw):
    g = Graph()
    rx.document_to_rdf(g, _pipeline(**kw))
    return g


# ── seven readings, seven provenance chains ──────────────────────────────────

def test_seven_readings_yield_seven_recognition_runs():
    g = _graph()
    readings = set(g.objects(rx.document_uri("BAT_664"), CRM["P128_carries"]))
    assert len(readings) == 8          # seven engines + the working reading

    for r in readings:
        runs = list(g.objects(r, PROV["wasGeneratedBy"]))
        assert len(runs) == 1, f"reading {r} has no single provenance chain"
        assert (runs[0], RDF.type, PROV["Activity"]) in g


def test_querying_by_model_id_returns_exactly_that_models_readings():
    """The point of giving each model its own node: one hop to its readings."""
    g = _graph()
    target = rx.model_uri("escriptmask", "escriptmask-v2")

    found = {r for r in g.subjects(PROV["wasGeneratedBy"], None)
             for run in g.objects(r, PROV["wasGeneratedBy"])
             if (run, PROV["used"], target) in g}
    assert len(found) == 1
    reading = next(iter(found))
    assert (reading, CRM["P190_has_symbolic_content"],
            rx._safe_literal("reading b")) in g


def test_model_node_carries_engine_and_model_id():
    g = _graph()
    m = rx.model_uri("trocr", "trocr-kurrent-xvi-xvii")
    assert (m, RDF.type, PROV["SoftwareAgent"]) in g
    assert (m, SDHSS["engine"], Literal("trocr")) in g
    assert (m, SDHSS["modelId"], Literal("trocr-kurrent-xvi-xvii")) in g


def test_confidence_travels_with_the_run():
    g = _graph()
    run = rx.recognition_uri("BAT_664", "escriptmask")
    assert (run, SDHSS["confidence"], Literal(0.22)) in g


def test_timestamp_is_emitted_only_when_known():
    """A run time is never fabricated."""
    g = _graph(recognitions=[_rec("trocr", "m1", "t")])
    assert list(g.objects(rx.recognition_uri("BAT_664", "trocr"),
                          PROV["endedAtTime"])) == []

    g2 = _graph(recognitions=[
        _rec("trocr", "m1", "t", ended_at="2026-07-29T10:00:00+00:00")])
    assert list(g2.objects(rx.recognition_uri("BAT_664", "trocr"),
                           PROV["endedAtTime"]))


# ── the no-merge decision is part of the record (#300) ───────────────────────

def test_no_merge_decision_and_its_measured_cer_are_in_the_graph():
    g = _graph()
    dec = rx.fusion_decision_uri("BAT_664")
    assert (rx.document_uri("BAT_664"), SDHSS["hasFusionDecision"], dec) in g
    assert (dec, SDHSS["merged"], Literal(False)) in g
    assert (dec, SDHSS["agreementCer"], Literal(1.05)) in g
    assert (dec, SDHSS["fusionStrategy"], Literal("vote+no-merge")) in g
    assert list(g.objects(dec, SDHSS["decisionReason"]))


def test_a_merged_document_records_merged_true():
    g = _graph(a_meta={"fusion_strategy": "vote", "fusion_agreement_cer": 0.05})
    dec = rx.fusion_decision_uri("BAT_664")
    assert (dec, SDHSS["merged"], Literal(True)) in g
    assert list(g.objects(dec, SDHSS["decisionReason"])) == []


def test_document_without_fusion_metadata_gets_no_decision_node():
    g = _graph(a_meta={})
    assert list(g.objects(rx.document_uri("BAT_664"),
                          SDHSS["hasFusionDecision"])) == []


# ── the historian's selection: closest, never "the text" ─────────────────────

CLOSEST_SINGLE = {
    "text": "reading b",
    "chosen": ["escriptmask"],
    "combined": False,
    "editor_pseudonym": "editor-9f2a1c",
    "confirmed_at": "2026-07-29T12:00:00+00:00",
    "status": "revisable_editorial_choice",
}

CLOSEST_COMBINED = {
    "text": "a combined text",
    "chosen": ["escriptmask", "trocr"],
    "combined": True,
    "editor_pseudonym": "editor-9f2a1c",
    "confirmed_at": "2026-07-29T12:00:00+00:00",
    "status": "revisable_editorial_choice",
}


def test_selected_reading_is_marked_closest():
    g = _graph(closest=CLOSEST_SINGLE)
    chosen = rx.reading_uri("BAT_664", "escriptmask")
    assert (chosen, SDHSS["closestReading"], Literal(True)) in g
    assert (chosen, SDHSS["readingRole"], Literal("closest")) in g
    assert list(g.objects(chosen, SDHSS["editorialCaveat"]))


def test_no_triple_asserts_the_closest_reading_as_the_documents_text():
    """The absence IS the point of this issue."""
    g = _graph(closest=CLOSEST_SINGLE)
    d = rx.document_uri("BAT_664")

    # nothing hangs text or a "the text" relation off the document
    assert list(g.objects(d, CRM["P190_has_symbolic_content"])) == []
    assert list(g.objects(d, SDHSS["hasClosestReading"])) == []
    for p in set(g.predicates(d, None)):
        assert "text" not in str(p).lower()

    # the marker lives on a reading, and says it is an editorial choice
    marked = list(g.subjects(SDHSS["closestReading"], Literal(True)))
    assert len(marked) == 1
    assert (marked[0], RDF.type, CRM["E33_Linguistic_Object"]) in g
    assert (marked[0], SDHSS["editorialStatus"],
            Literal("revisable_editorial_choice")) in g


def test_selection_is_an_explicit_activity_attributed_to_the_historian():
    g = _graph(closest=CLOSEST_SINGLE)
    sel = rx.selection_uri("BAT_664")
    agent = rx.agent_uri("editor-9f2a1c")
    chosen = rx.reading_uri("BAT_664", "escriptmask")

    assert (sel, RDF.type, SDHSS["EditorialSelection"]) in g
    assert (sel, PROV["wasAssociatedWith"], agent) in g
    assert (sel, PROV["used"], chosen) in g
    assert (chosen, SDHSS["selectedIn"], sel) in g
    assert list(g.objects(sel, PROV["endedAtTime"]))


def test_verbatim_choice_endorses_a_reading_rather_than_generating_it():
    """Picking an engine's text verbatim does not make the historian its author.

    Asserting prov:wasGeneratedBy for a verbatim pick would credit the historian
    with the engine's output AND close a cycle, since the selection already
    prov:used that reading. The reading keeps exactly one generator: its run.
    """
    g = _graph(closest=CLOSEST_SINGLE)
    chosen = rx.reading_uri("BAT_664", "escriptmask")
    sel = rx.selection_uri("BAT_664")
    agent = rx.agent_uri("editor-9f2a1c")

    generators = set(g.objects(chosen, PROV["wasGeneratedBy"]))
    assert generators == {rx.recognition_uri("BAT_664", "escriptmask")}
    assert sel not in generators
    assert (chosen, PROV["wasAttributedTo"], agent) not in g


def test_each_reading_reports_exactly_one_producer():
    """The provenance chain must not fan out into a second, spurious producer."""
    g = _graph(closest=CLOSEST_SINGLE)
    q = """
    PREFIX crm: <http://www.cidoc-crm.org/cidoc-crm/>
    PREFIX prov: <http://www.w3.org/ns/prov#>
    SELECT ?reading ?producer WHERE {
      ?doc crm:P128_carries ?reading .
      ?reading prov:wasGeneratedBy/prov:used ?producer .
    }"""
    rows = list(g.query(q))
    per_reading = {}
    for reading, producer in rows:
        per_reading.setdefault(str(reading), set()).add(str(producer))
    assert per_reading, "no provenance chains found"
    for reading, producers in per_reading.items():
        assert len(producers) == 1, f"{reading} has {len(producers)} producers"


def test_combined_reading_is_generated_by_and_attributed_to_the_historian():
    g = _graph(closest=CLOSEST_COMBINED)
    combined = rx.reading_uri("BAT_664", "closest")
    assert (combined, PROV["wasGeneratedBy"], rx.selection_uri("BAT_664")) in g
    assert (combined, PROV["wasAttributedTo"], rx.agent_uri("editor-9f2a1c")) in g


def test_combined_reading_records_wasderivedfrom_each_source():
    g = _graph(closest=CLOSEST_COMBINED)
    combined = rx.reading_uri("BAT_664", "closest")

    sources = set(g.objects(combined, PROV["wasDerivedFrom"]))
    assert sources == {rx.reading_uri("BAT_664", "escriptmask"),
                       rx.reading_uri("BAT_664", "trocr")}
    assert (combined, SDHSS["closestReading"], Literal(True)) in g
    # the combined text is a new object, attached like any other reading
    assert (rx.document_uri("BAT_664"), CRM["P128_carries"], combined) in g


def test_page_without_a_human_decision_exports_cleanly():
    g = _graph()
    assert list(g.subjects(SDHSS["closestReading"], Literal(True))) == []
    assert list(g.subjects(RDF.type, SDHSS["EditorialSelection"])) == []
    # …and all readings are still there with their provenance
    assert len(set(g.objects(rx.document_uri("BAT_664"), CRM["P128_carries"]))) == 8


# ── pseudonymity ─────────────────────────────────────────────────────────────

def test_historian_is_a_pseudonymous_agent():
    g = _graph(closest=CLOSEST_SINGLE)
    agent = rx.agent_uri("editor-9f2a1c")
    assert (agent, RDF.type, PROV["Agent"]) in g
    assert (agent, SDHSS["pseudonymous"], Literal(True)) in g
    assert (agent, RDFS.label, Literal("editor-9f2a1c")) in g


def test_no_raw_discord_id_reaches_the_graph():
    """Upstream pseudonymises; this asserts nothing here re-introduces the id."""
    leaky = dict(CLOSEST_SINGLE)
    leaky.update({
        "editor": "441742583086645248",          # raw Discord id
        "decided_by": "historian#1234",
        "user_id": "441742583086645248",
    })
    g = _graph(closest=leaky)
    blob = g.serialize(format="turtle")
    assert "441742583086645248" not in blob
    assert "historian#1234" not in blob
    assert "editor-9f2a1c" in blob


# ── the whole corpus still exports, deterministically ────────────────────────

def test_corpus_export_with_provenance_is_deterministic(tmp_path):
    doc = _pipeline(closest=CLOSEST_COMBINED)
    (tmp_path / "BAT_664_pipeline.json").write_text(json.dumps(doc), encoding="utf-8")

    a = Path(rx.corpus_to_turtle(tmp_path, tmp_path / "a.ttl")).read_text(encoding="utf-8")
    b = Path(rx.corpus_to_turtle(tmp_path, tmp_path / "b.ttl")).read_text(encoding="utf-8")
    assert a == b


def test_graph_with_provenance_round_trips_as_turtle(tmp_path):
    doc = _pipeline(closest=CLOSEST_SINGLE)
    (tmp_path / "BAT_664_pipeline.json").write_text(json.dumps(doc), encoding="utf-8")

    out = rx.corpus_to_turtle(tmp_path, tmp_path / "corpus.ttl")
    reparsed = Graph().parse(out, format="turtle")
    assert set(reparsed) == set(rx.corpus_to_graph(tmp_path))
