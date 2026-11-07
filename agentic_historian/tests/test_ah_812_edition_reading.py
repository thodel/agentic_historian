"""Agent F and the Königsfelden edition's two layers (#812, after the second tei run).

07.11.2026, ``ask … --bestand kf`` on tei: 0 of 5 claims, "quote not in the
cited passage" ×8. The edition's text keeps each abbreviation beside its
expansion (``de eliā elemosina ꝓ pro infirmis``), so no quote of the words
could stand in it. The decision (Tobias, 07.11.): a quote is checked exactly
against the edition's **reading layer** — abbreviations resolved by a fixed
rule — and the answer shows the edition's own characters beside it.

The same run and the first one showed three more things, tested here too: the
passage must be read in its document, not a 200-character snippet; a holding
the question names must scope the search; "nothing in this holding" is an
empty search, not a failed one.

The fixture is the real entry StAAG U.17/0274 (1357), ``get_entry`` verbatim.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_edition_reading.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import edition_text as et
import evidence as ev
from agents import argument_agent as af

FIX = json.loads((PKG / "tests" / "fixtures" / "evidence" / "kf_U-17_0274.json")
                 .read_text(encoding="utf-8"))
FULL = FIX["get_entry"]["text_raw"]
SNIPPET = FIX["search_snippet"]


# ── the reading layer ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def layer():
    return et.reading_layer(FULL)


@pytest.mark.parametrize("abbreviated,expanded", [
    ("eliā elemosina", "elemosina"),
    ("ꝓ pro", "pro"),
    ("bˀg berg", "berg"),
    ("schulth̄ schultheiss", "schultheiss"),
    ("ḡl gel", "gel"),
    ("Königsf. Königsfelden", "Königsfelden"),
])
def test_an_abbreviation_beside_its_expansion_is_read_as_the_expansion(
        layer, abbreviated, expanded):
    assert abbreviated in FULL
    assert abbreviated not in layer.text
    assert expanded in layer.text


def test_ordinary_characters_and_unmarked_words_stay(layer):
    assert "guͦt" in layer.text               # a combining ͦ, no expansion after it
    assert "mt mütt" in layer.text             # no mark: never dropped
    assert "dem dem dem richter" in layer.text  # the edition's own repetition
    assert "✳" not in layer.text and "¬" not in layer.text


@pytest.mark.parametrize("quote,source_span", [
    ("elemosina pro infirmis", "elemosina ꝓ pro infirmis"),
    ("schultheiss ze Bremgarten", "schultheiss ze Bremgar tē ten"),
    ("ir ingesigel gehenkt an disen brief", "ir ingesi ḡl gel gehenkt an disen brief"),
    ("umb hundert und zwenzig pfunt pfennig", "umb hundert und zwenzig pfunt pfennig"),
])
def test_a_quote_is_found_and_mapped_back_to_the_editions_characters(layer, quote,
                                                                      source_span):
    spans = et.locate(quote, layer, FULL)
    assert [FULL[s:e] for s, e in spans] == [source_span]


def test_the_reading_is_exact_apart_from_whitespace(layer):
    assert et.locate("elemosina pro infirmi s", layer, FULL)        # spacing only
    assert not et.locate("Elemosina pro infirmis", layer, FULL)     # case is kept
    assert not et.locate("elemosina für die Kranken", layer, FULL)  # translation
    assert not et.locate("elemosyna pro infirmis", layer, FULL)     # a near miss


# ── binding against the reading layer ────────────────────────────────────────

@pytest.fixture
def kf_item():
    return ev.edition_item("kf", "U-17_0274", FULL, datierung="1357",
                           title="StAAG U.17/0274")


def test_a_quote_of_the_reading_binds_and_records_both_layers(kf_item):
    pool = {kf_item.evidence_id: kf_item}
    ok, bad = af.check_bindings([{"claim": "Die Rente kam den Kranken zu.", "evidence": [
        {"id": kf_item.evidence_id, "quote": "elemosina pro infirmis"}]}], pool)
    assert ok and not bad
    b = ok[0].bindings[0]
    assert (b.layer, b.source_span) == ("reading", "elemosina ꝓ pro infirmis")


def test_a_literal_quote_is_still_literal(kf_item):
    pool = {kf_item.evidence_id: kf_item}
    ok, _ = af.check_bindings([{"claim": "x", "evidence": [
        {"id": kf_item.evidence_id, "quote": "umb hundert und zwenzig pfunt pfennig"}]}], pool)
    assert ok[0].bindings[0].layer == "literal"
    assert ok[0].bindings[0].source_span == ""


def test_a_translated_or_invented_quote_is_still_refused(kf_item):
    pool = {kf_item.evidence_id: kf_item}
    _, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": kf_item.evidence_id, "quote": "Almosen für die Kranken"}]}], pool)
    assert bad[0].bindings[0].reason == "quote not in the cited passage"


def test_our_own_readings_get_no_reading_layer():
    """Only edition text has two layers; an HTR reading is quoted as it is."""
    rs = ev.ReadingSet("d", "", [ev.Reading("vlm", "m", "der brief ꝓ pro")])
    item = ev.item_for_span(rs, "vlm/m", 0, 15)
    assert item.reading_text is None
    _, bad = af.check_bindings([{"claim": "x", "evidence": [
        {"id": item.evidence_id, "quote": "der brief pro"}]}], {item.evidence_id: item})
    assert bad


def test_the_model_is_shown_the_reading_layer_and_the_rules(kf_item):
    block = af._passages_block([kf_item])
    assert "elemosina pro infirmis" in block and "ꝓ" not in block
    assert "Misericordia" in af._BIND_PROMPT
    assert "keinen Zweck und kein Motiv" in af._BIND_PROMPT


def test_the_answer_shows_the_editions_characters_beside_the_quote(kf_item):
    lines = af._cite(kf_item, "elemosina pro infirmis", "elemosina ꝓ pro infirmis")
    text = "\n".join(lines)
    assert "„elemosina pro infirmis“" in text
    assert "Edition, beide Schichten: „elemosina ꝓ pro infirmis“" in text


# ── the passage in its document ──────────────────────────────────────────────

@pytest.fixture
def snippet_item():
    it = ev.edition_item("kf", "U-17_0274", SNIPPET.strip("…"), datierung="1357",
                         title="StAAG U.17/0274")
    it.retrieval = {"subquestion": "q1", "score": 0.9, "retriever": "fulltext"}
    return it


def test_a_sampled_snippet_becomes_a_window_of_its_document(snippet_item):
    pool = {snippet_item.evidence_id: snippet_item}
    calls = []

    def fetch(source, doc_id):
        calls.append((source, doc_id))
        return FULL

    audit = af.Audit()
    ids = af.expand_context([snippet_item.evidence_id], pool, audit=audit,
                            fetch=fetch, chars=600)
    assert calls == [("kf", "U-17_0274")]
    new = pool[ids[0]]
    assert ids[0] != snippet_item.evidence_id and snippet_item.evidence_id not in pool
    assert "elemosina ꝓ pro infirmis" in new.quote
    # the window now carries the charter around the endorsement
    assert "an sant Agten abende" in new.quote
    assert new.quote.startswith("…")
    assert new.source["datierung"] == "1357" and new.source["title"] == "StAAG U.17/0274"
    assert new.retrieval["context"] == "document"
    assert new.retrieval["snippet_id"] == snippet_item.evidence_id
    assert new.retrieval["subquestion"] == "q1"


def test_one_document_is_fetched_once(snippet_item):
    other = ev.edition_item("kf", "U-17_0274", "umb hundert und zwenzig pfunt pfennig")
    pool = {snippet_item.evidence_id: snippet_item, other.evidence_id: other}
    calls = []
    af.expand_context(list(pool), pool, audit=af.Audit(),
                      fetch=lambda s, d: calls.append(d) or FULL)
    assert calls == ["U-17_0274"]


def test_a_document_that_cannot_be_read_leaves_the_snippet_and_says_so(snippet_item):
    pool = {snippet_item.evidence_id: snippet_item}
    audit = af.Audit()

    def boom(source, doc_id):
        raise ConnectionError("kf: 302")

    ids = af.expand_context([snippet_item.evidence_id], pool, audit=audit, fetch=boom)
    assert ids == [snippet_item.evidence_id]
    assert any("not fetched" in e for e in audit.events)


def test_a_snippet_not_in_its_document_stands():
    it = ev.edition_item("kf", "U-17_0274", "ein Satz, der nicht dort steht")
    pool = {it.evidence_id: it}
    audit = af.Audit()
    assert af.expand_context([it.evidence_id], pool, audit=audit,
                             fetch=lambda s, d: FULL) == [it.evidence_id]
    assert any("not found" in e for e in audit.events)


def test_sources_without_a_document_tool_are_left_alone():
    it = ev.edition_item("hbls", "x", "ein Artikel")
    pool = {it.evidence_id: it}
    called = []
    af.expand_context([it.evidence_id], pool, audit=af.Audit(),
                      fetch=lambda s, d: called.append(d))
    assert called == []


# ── scope, duplicates, empty vs failed ───────────────────────────────────────

class _LLM:
    def __init__(self, bestand):
        self.bestand, self.prompts = bestand, []

    def __call__(self, prompt, system, max_tokens):
        self.prompts.append(prompt)
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [
                {"text": "Wie wird Almosen begründet?", "terms": ["elemosina", "elemosina"]},
                {"text": "Für wen?", "terms": ["Elemosina", "infirmis"]}],
                "bestand": self.bestand}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": []}), {}
        return json.dumps({"gaps": []}), {}


def _spy():
    seen = []

    def fulltext(query, *, bestand=None, limit=10):
        seen.append((query, bestand))
        return []

    return seen, {"fulltext": fulltext}


def test_a_holding_the_question_names_scopes_the_search():
    seen, retrievers = _spy()
    llm = _LLM("kf")
    res = af.ask("Wie wird Almosen in den Königsfelder Urkunden begründet?",
                 llm=llm, retrievers=retrievers, max_rounds=0, fetch=lambda s, d: None)
    assert seen and {b for _, b in seen} == {"kf"}
    assert res.plan.bestand == "kf"
    assert "kf: KF — Königsfelden register" in llm.prompts[0]
    assert "Bestand aus der Frage gelesen: kf" in af.render_markdown(res)


def test_the_callers_holding_wins_and_an_unknown_one_is_ignored():
    seen, retrievers = _spy()
    af.ask("q", bestand="eos", llm=_LLM("kf"), retrievers=retrievers, max_rounds=0,
           fetch=lambda s, d: None)
    assert {b for _, b in seen} == {"eos"}
    seen, retrievers = _spy()
    res = af.ask("q", llm=_LLM("nowhere"), retrievers=retrievers, max_rounds=0,
                 fetch=lambda s, d: None)
    assert {b for _, b in seen} == {None}
    assert any("not one of the federation's" in e for e in res.audit.events)


def test_the_same_search_runs_once():
    seen, retrievers = _spy()
    af.ask("q", llm=_LLM(None), retrievers=retrievers, max_rounds=0,
           fetch=lambda s, d: None)
    assert [q for q, _ in seen] == ["elemosina", "infirmis"]


def test_no_passages_in_the_holding_is_empty_not_failed(monkeypatch):
    import passage_find

    monkeypatch.setattr(passage_find, "find", lambda *a, **k: passage_find.Found(
        query="x", bestand="kf", indexed=1, candidates=0))
    assert af.own_passages("x", bestand="kf") == []
    monkeypatch.setattr(passage_find, "find", lambda *a, **k: passage_find.Found(
        query="x", indexed=0))
    with pytest.raises(LookupError, match="leer"):
        af.own_passages("x")
