"""
test_ah_631_care_flag.py — "nein" und "nicht geprüft" sind zwei Antworten.

Agent B's care classification returned `is_care_related: False` both when the
model said no and when the call failed — its exception path returned the same
default. A stored `False` therefore claimed a judgement about a welfare record
that nobody had made (#631).

What these tests pin:

  1. Three values. A failed classification is `None` with a reason, not `False`.
  2. **No migration.** A pre-#631 record is read by the asymmetry that was
     always true of it: `True` survives, `False` reads as unknown.
  3. The graph publishes all three (`careClassified` + `careRelated`), so a
     corpus-health query can select on "never checked".
  4. The narrower publication scope withholds the unclassified too — an
     unclassified document *may* be care-related, which is what that setting
     exists for.
  5. The description markdown says "ungeprüft", not "Nein".

Run: pytest agentic_historian/tests/test_ah_631_care_flag.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph, Literal                              # noqa: E402

import care_flag as cf                                         # noqa: E402
from knowledge_hub import rdf_export as rx                     # noqa: E402


def _doc(doc_id="doc-1", flag=None, *, with_flag=True):
    description = {"source_description": "d",
                   "source_json": {"Datierung": {"wert": "15. Jahrhundert"}}}
    if with_flag:
        description["care_flag"] = (flag if flag is not None
                                   else cf.classified(True, types=["Almosen"]))
    return {
        "doc_id": doc_id,
        "transcription": "lesung",
        "description": description,
        "entities": {"entities": [{"text": "A", "type": "PERSON",
                                   "normalised": "A"}]},
        "recognitions": [{"engine": "kraken", "model_id": "m1", "text": "lesung",
                          "confidence": 0.9}],
    }


# ── 1. three values ─────────────────────────────────────────────────────────

def test_a_failed_classification_is_unknown_and_says_why():
    flag = cf.read(cf.unclassified("ReadTimeout: gateway"))
    assert flag.state == cf.UNKNOWN
    assert flag.is_care_related is None
    assert flag.measured is False
    assert "ReadTimeout" in flag.reason


def test_a_real_negative_is_a_negative():
    flag = cf.read(cf.classified(False))
    assert flag.state == cf.NO
    assert flag.is_care_related is False
    assert flag.measured is True


def test_a_positive_carries_its_context_and_types():
    flag = cf.read(cf.classified(True, context="Spital", types=["Almosen", "Pflege"],
                                 participants=["X"]))
    assert flag.state == cf.YES
    assert flag.is_care_related is True
    assert flag.context == "Spital"
    assert flag.types == ("Almosen", "Pflege")
    assert flag.participants == ("X",)


def test_the_three_states_are_three_different_sentences():
    lines = {cf.read(cf.classified(True)).line(),
             cf.read(cf.classified(False)).line(),
             cf.read(cf.unclassified("boom")).line()}
    assert len(lines) == 3
    assert "ungeprüft" in cf.read(cf.unclassified("boom")).line()
    assert "boom" in cf.read(cf.unclassified("boom")).line()


def test_the_stored_record_of_a_failure_does_not_say_false():
    """The whole bug in one assertion."""
    stored = cf.unclassified("timeout")
    assert stored["is_care_related"] is None
    assert stored["classified"] is False
    assert stored["error"]


# ── 2. no migration: a vintage record is read by its own asymmetry ─────────

def test_a_vintage_true_survives_as_true():
    """`True` was only ever written by a classification that succeeded."""
    flag = cf.read({"is_care_related": True, "care_context": "Spital"})
    assert flag.state == cf.YES
    assert flag.vintage is True


def test_a_vintage_false_reads_as_unknown_not_as_no():
    """It could be either, so it is neither — and the reason names the vintage."""
    flag = cf.read({"is_care_related": False, "care_context": ""})
    assert flag.state == cf.UNKNOWN
    assert flag.vintage is True
    assert "#631" in flag.reason


def test_the_marker_is_what_tells_the_vintages_apart():
    """`classified` is the version marker — no cutoff date has to be guessed."""
    assert cf.read({"is_care_related": False}).vintage is True
    assert cf.read(cf.classified(False)).vintage is False


def test_a_record_that_claims_to_be_classified_but_has_no_answer_is_unknown():
    """A contradictory record — ``classified: true`` with no value — must fall
    to unknown, not to "no". Every fallback in this module goes the honest way,
    including the ones that should never be reached."""
    flag = cf.read({"is_care_related": None, "classified": True})
    assert flag.state == cf.UNKNOWN
    assert flag.is_care_related is None


def test_a_record_without_any_care_flag_is_unknown():
    assert cf.read({"description": {}}).state == cf.UNKNOWN
    assert cf.read({}).state == cf.UNKNOWN
    assert cf.read(None).state == cf.UNKNOWN


@pytest.mark.parametrize("shape", [
    "pipeline",          # the whole record, as the exporter has it
    "description",       # Agent B's own result
    "flag",              # the bare dict, as a test has it
])
def test_the_reader_takes_any_of_the_three_shapes_a_caller_holds(shape):
    flag = cf.classified(True, types=["Almosen"])
    source = {"pipeline": {"description": {"care_flag": flag}},
              "description": {"care_flag": flag},
              "flag": flag}[shape]
    assert cf.read(source).state == cf.YES


def test_a_shape_the_reader_does_not_recognise_is_unknown_never_negative():
    """Guessing wrong must fall to "nobody knows", not to "not care-related"."""
    for odd in ("a string", 42, [], {"something": "else"}):
        assert cf.read(odd).state == cf.UNKNOWN


def test_counting_states_separates_the_unchecked_from_the_negatives():
    """"3 of 20 care-related" and "3 of 20, 6 never classified" differ."""
    records = ([cf.classified(True)] * 3 + [cf.classified(False)] * 11
               + [cf.unclassified("x")] * 6)
    assert cf.count_states(records) == {"yes": 3, "no": 11, "unknown": 6}


def test_a_vintage_corpus_counts_as_unchecked_rather_than_clean():
    records = [{"is_care_related": False}] * 5 + [{"is_care_related": True}]
    assert cf.count_states(records) == {"yes": 1, "no": 0, "unknown": 5}


# ── 3. the graph publishes all three ───────────────────────────────────────

def test_a_care_related_document_is_published_as_such():
    g = Graph()
    rx.document_to_rdf(g, _doc("yes", cf.classified(True, types=["Almosen"])))
    d = rx.document_uri("yes")
    assert (d, rx.SHB["careClassified"], Literal(True)) in g
    assert (d, rx.SHB["careRelated"], Literal(True)) in g
    assert {str(o) for o in g.objects(d, rx.SHB["careType"])} == {"Almosen"}


def test_a_measured_negative_is_now_published_as_a_negative():
    """It could not be before: the record could not support the claim (#631)."""
    g = Graph()
    rx.document_to_rdf(g, _doc("no", cf.classified(False)))
    d = rx.document_uri("no")
    assert (d, rx.SHB["careClassified"], Literal(True)) in g
    assert (d, rx.SHB["careRelated"], Literal(False)) in g


def test_an_unclassified_document_says_so_instead_of_claiming_a_negative():
    g = Graph()
    rx.document_to_rdf(g, _doc("unknown", cf.unclassified("timeout")))
    d = rx.document_uri("unknown")
    assert (d, rx.SHB["careClassified"], Literal(False)) in g
    assert not list(g.objects(d, rx.SHB["careRelated"]))


def test_the_unclassified_marker_is_explicit_and_not_merely_absent():
    """Absence in a graph is ambiguous — it could mean an older build. "We never
    checked this welfare document" is a fact a query has to select on."""
    g = Graph()
    rx.document_to_rdf(g, _doc("unknown", cf.unclassified("timeout")))
    assert list(g.objects(rx.document_uri("unknown"), rx.SHB["careClassified"]))


def test_a_vintage_negative_is_published_as_unclassified():
    g = Graph()
    rx.document_to_rdf(g, _doc("old", {"is_care_related": False}))
    d = rx.document_uri("old")
    assert (d, rx.SHB["careClassified"], Literal(False)) in g
    assert not list(g.objects(d, rx.SHB["careRelated"]))


def test_the_reason_a_classification_failed_is_not_published():
    """It can carry a model name or a URL. It belongs in the record and the log."""
    g = Graph()
    rx.document_to_rdf(g, _doc(
        "unknown", cf.unclassified("ConnectError: https://gpustack.internal/v1")))
    turtle = g.serialize(format="turtle")
    assert "gpustack.internal" not in turtle
    assert "ConnectError" not in turtle


def test_the_care_context_is_still_not_published():
    g = Graph()
    rx.document_to_rdf(g, _doc("yes", cf.classified(
        True, context="Spitalpfrund für eine Witwe", participants=["Anna"])))
    turtle = g.serialize(format="turtle")
    assert "Spitalpfrund" not in turtle
    assert "Anna" not in turtle


def test_the_care_types_of_a_negative_are_not_emitted():
    """A model that answers "no" and still fills `care_types` has contradicted
    itself; the flag is the answer, so the types do not travel."""
    g = Graph()
    rx.document_to_rdf(g, _doc("no", {**cf.classified(False),
                                      "care_types": ["Almosen"]}))
    assert not list(g.objects(rx.document_uri("no"), rx.SHB["careType"]))


def test_the_care_queries_still_only_find_the_positives():
    """KG-5's question is "which documents are care-related" — a published
    `careRelated false` must not widen it."""
    from knowledge_hub import queries as q

    g = Graph()
    rx.document_to_rdf(g, _doc("yes", cf.classified(True)))
    rx.document_to_rdf(g, _doc("no", cf.classified(False)))
    rx.document_to_rdf(g, _doc("unknown", cf.unclassified("x")))
    rows = q.run("persons-in-care-documents", g, century="15").rows
    assert {r["document"].rsplit("/", 1)[-1] for r in rows} == {"yes"}


# ── 4. the publication scope ──────────────────────────────────────────────

def test_the_narrow_scope_withholds_care_and_unclassified_separately(tmp_path):
    import json

    from knowledge_hub import corpus_build as cb

    for name, flag in (("yes", cf.classified(True)),
                       ("no", cf.classified(False)),
                       ("unknown", cf.unclassified("timeout")),
                       ("vintage", {"is_care_related": False})):
        (tmp_path / f"{name}_pipeline.json").write_text(
            json.dumps(_doc(name, flag)), encoding="utf-8")

    report = cb.build(tmp_path, tmp_path / "corpus.ttl",
                      cb.PublicationScope(include_care_flagged=False))
    assert report.documents == 1                                  # only "no"
    assert report.withheld["care_flagged_documents"] == 1
    assert report.withheld["unclassified_documents"] == 2         # unknown + vintage


def test_the_wide_scope_publishes_everything_and_still_counts_the_unchecked(tmp_path):
    import json

    from knowledge_hub import corpus_build as cb

    for name, flag in (("yes", cf.classified(True)),
                       ("no", cf.classified(False)),
                       ("unknown", cf.unclassified("timeout"))):
        (tmp_path / f"{name}_pipeline.json").write_text(
            json.dumps(_doc(name, flag)), encoding="utf-8")

    report = cb.build(tmp_path, tmp_path / "corpus.ttl")
    assert report.documents == 3
    assert not report.withheld
    # The number that was impossible before: how many were actually checked.
    assert report.care == {"yes": 1, "no": 1, "unknown": 1}
    assert "care" in report.render()
    assert "unknown=1" in report.render()


def test_the_counts_come_off_the_graph_not_from_bookkeeping(tmp_path):
    """Like every other count in the report — nothing to drift."""
    import inspect

    from knowledge_hub import corpus_build as cb

    source = inspect.getsource(cb._count)
    assert "careClassified" in source and "careRelated" in source


# ── 5. Agent B ────────────────────────────────────────────────────────────

def test_the_classifier_stores_an_unknown_when_the_model_call_fails(monkeypatch):
    from agents import source_description as sd

    def _boom(*a, **k):
        raise RuntimeError("gateway said 503")

    monkeypatch.setattr(sd.gs, "chat_text", _boom)
    stored = sd._care_flag("unser fruntlich gruos")
    assert stored["is_care_related"] is None
    assert stored["classified"] is False
    assert "503" in stored["error"]
    assert cf.read(stored).state == cf.UNKNOWN


def test_the_classifier_stores_an_unknown_when_the_answer_has_no_json(monkeypatch):
    """The reported failure mode: a reasoning model that never reaches the JSON."""
    from agents import source_description as sd

    monkeypatch.setattr(sd.gs, "chat_text",
                        lambda *a, **k: "Lass mich nachdenken ...")
    stored = sd._care_flag("text")
    assert stored["is_care_related"] is None
    assert cf.read(stored).state == cf.UNKNOWN


def test_the_classifier_stores_a_real_answer_as_measured(monkeypatch):
    from agents import source_description as sd

    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: (
        '{"is_care_related": true, "care_context": "Spital", '
        '"care_types": ["Almosen"], "beteiligte": ["Anna"]}'))
    stored = sd._care_flag("text")
    assert stored["classified"] is True
    assert stored["is_care_related"] is True
    assert cf.read(stored).state == cf.YES
    assert cf.read(stored).types == ("Almosen",)


def test_a_negative_from_the_model_is_stored_as_measured(monkeypatch):
    from agents import source_description as sd

    monkeypatch.setattr(sd.gs, "chat_text",
                        lambda *a, **k: '{"is_care_related": false}')
    stored = sd._care_flag("text")
    assert stored["classified"] is True
    assert stored["is_care_related"] is False
    assert cf.read(stored).state == cf.NO


def test_the_description_markdown_says_ungeprueft_rather_than_nein(tmp_path, monkeypatch):
    import config
    from agents import source_description as sd

    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path)
    sd._save("doc-x", {"doc_id": "doc-x", "source_description": "d",
                       "source_json": {},
                       "care_flag": cf.unclassified("timeout")})
    md = (tmp_path / "doc-x.md").read_text(encoding="utf-8")
    assert "ungeprüft" in md
    assert "Care-relevant:** Nein" not in md


def test_the_description_markdown_still_says_nein_for_a_real_negative(tmp_path,
                                                                     monkeypatch):
    import config
    from agents import source_description as sd

    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path)
    sd._save("doc-y", {"doc_id": "doc-y", "source_description": "d",
                       "source_json": {}, "care_flag": cf.classified(False)})
    md = (tmp_path / "doc-y.md").read_text(encoding="utf-8")
    assert "Care-relevant:** Nein" in md


def test_the_paths_that_never_classify_store_an_unknown():
    """Two of them, and both stored `False`: the degenerate transcription and
    the image-only description. The flag reads the transcription, and in both
    cases there is no usable one — which is "unknown", not "no"."""
    source = (PKG / "agents" / "source_description.py").read_text(encoding="utf-8")
    assert source.count('"care_flag": {"is_care_related": False') == 0
    assert source.count("care_flag.unclassified(") >= 3   # two paths + the failure


def test_every_care_flag_consumer_goes_through_the_reader():
    """The vintage rule exists once, not in three modules that drift apart."""
    for module in ("knowledge_hub/rdf_export.py", "knowledge_hub/corpus_build.py",
                   "agents/source_description.py"):
        source = (PKG / module).read_text(encoding="utf-8")
        assert "care_flag" in source, module
    scope = (PKG / "knowledge_hub" / "corpus_build.py").read_text(encoding="utf-8")
    # No module re-implements the truthiness test the reader replaced.
    assert 'flag.get("is_care_related")' not in scope
