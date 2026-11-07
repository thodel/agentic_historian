"""What the third run on tei showed (07.11.2026, #812).

``ask "Wie wird Almosen in den Königsfelder Urkunden begründet?"`` held its
scope and bound its quotes through the reading layer, and still:

* searched ``almuosen`` and found nothing — the edition writes ``almuͦsen``
  (u + U+0366), and that spelling finds five charters, among them the ones
  that answer the question (U.17/0288: "durch got und durch unserr sel heiles
  willen"). The Königsfelden answers below are verbatim, asked through the
  connector the same day;
* printed a summary with no claim references that said what no accepted
  claim said ("a God-ordered righteous duty"), in English, to a German
  question;
* read "littera abbatisse su per elemosina" as an abbot's letter "per
  elemosina": the edition splits *super*, and Königsfelden had an abbess.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_third_run.py
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

KF_SOURCE = ("Editionsprojekt «Die Urkunden und Akten des Klosters und der Hofmeisterei "
             "Königsfelden (1300–1658)»")

#: search_fulltext("almuͦsen", limit=5), 07.11.2026, verbatim.
KF_ALMUOSEN = {"result": [
    {"id": "U-17_0288", "title": "StAAG U.17/0288", "short_id": "StAAG U.17/0288",
     "year": 1359, "source": KF_SOURCE,
     "snippet": "…mit disem <mark>almuͦsen</mark>, ✳ und ordenen und wellen, dz ein bruͦder, "
                "der hie ze Kùngsvelt der fro wē wen bihter ist in dem closter, ✳ dz "
                "vorgenant <mark>almuͦsen</mark> enphahen ✳ und in…"},
    {"id": "U-17_0145", "title": "StAAG U.17/0145", "short_id": "StAAG U.17/0145",
     "year": 1334, "source": KF_SOURCE,
     "snippet": "…der Jacob ✳ dù vorgnan tē ten ding gebuwen und gekoͧffet hat mit erberr "
                "lùte <mark>almuͦsen</mark>. ✳ man sol oͧch wissen, da vor got si, das "
                "unser hˀ her re ùber…"},
    {"id": "U-17_0075", "title": "StAAG U.17/0075", "short_id": "StAAG U.17/0075",
     "year": 1322, "source": KF_SOURCE,
     "snippet": "…und swaz si des hoves die wile geniezzent, ✳ daz geben wir in ze einem "
                "rehtem <mark>almuͦsen</mark> ✳ und lazzen es in durch got ✳ und durch "
                "unser und aller unser vordern sele willen…"},
    {"id": "U-17_0109", "title": "StAAG U.17/0109", "short_id": "StAAG U.17/0109",
     "year": 1330, "source": KF_SOURCE,
     "snippet": "…und swas si des hofes die wile geniessent, das geben wir in ze einem "
                "rehten <mark>almuͦsen</mark> ✳ und lassen ez in dur got und durch unser "
                "und aller unserr vordern sele willen…"},
    {"id": "U-17_0104", "title": "StAAG U.17/0104", "short_id": "StAAG U.17/0104",
     "year": 1330, "source": KF_SOURCE,
     "snippet": "…drù und zwaintzig stuck giltet, das selbe guͦt wir koͧft haben mit dem "
                "<mark>almuͦsen</mark> unserr hertzlieben swester seli gē gen ✳ Guͦten, "
                "grefinne von ✳ Oͤttingen, geben und gegeben…"},
]}


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


# ── 1. the edition's spelling ────────────────────────────────────────────────

@pytest.mark.parametrize("term,variant", [
    ("almuosen", "almuͦsen"),
    ("guoter", "guͦter"),
    ("hören", "hoͤren"),
    ("bruoder", "bruͦder"),
])
def test_a_normalised_word_is_also_searched_as_the_edition_writes_it(term, variant):
    variants = et.search_variants(term)
    assert variants[0] == term and variant in variants


def test_a_word_with_nothing_to_vary_is_searched_once():
    assert et.search_variants("almosen") == ["almosen"]
    assert et.search_variants("  ") == []


async def _kf(source, tool, args):
    """The Königsfelden server as it answered on 07.11.2026."""
    if source.name != "kf":
        return {"result": []}
    return KF_ALMUOSEN if args["query"] == "almuͦsen" else {"result": []}


def test_the_charters_the_plain_spelling_missed_are_found(monkeypatch):
    def fulltext(query, *, bestand=None, limit=10):
        return af.federation_fulltext(query, bestand=bestand, limit=limit, call_tool=_kf)

    pool, coverage = {}, []
    af.retrieve([{"id": "q1", "text": "Almosen", "terms": ["almuosen"]}],
                retrievers={"fulltext": fulltext}, pool=pool, coverage=coverage,
                bestand="kf")
    assert [(c["query"], c["status"], c["hits"]) for c in coverage] == [
        ("almuosen", "empty", 0), ("almuͦsen", "ok", 5)]
    titles = {it.source["title"] for it in pool.values()}
    assert "StAAG U.17/0288" in titles and "StAAG U.17/0075" in titles
    pro_anima = next(it for it in pool.values() if it.doc_id == "U-17_0075")
    assert "durch unser und aller unser vordern sele willen" in pro_anima.quote
    assert pro_anima.source["datierung"] == "1322"


# ── 2. the summary carries only what the claims carry ────────────────────────

def _claims(*numbers_ok):
    """Accepted claims with the model's numbers, as check_bindings makes them."""
    return [af.Claim(f"claim {n}", [af.Binding("ev-x", "almuͦsen", ok=True)], n)
            for n in numbers_ok]


def test_the_third_runs_summary_is_withheld():
    """Verbatim from the answer of 07.11.2026: no reference, unsupported."""
    summary = ("The Königsfelder documents justify almsgiving as a God‑ordered righteous "
               "duty, specify that the alms are meant for the sick, and connect the "
               "almsgiving to the abbot’s letter, describing it as performed “per "
               "elemosina.”")
    kept, why = af.check_summary(summary, _claims(1, 2))
    assert kept == ""
    assert why.startswith("1 sentence(s) withheld") and "cites no claim" in why


def test_references_are_renumbered_and_a_rejected_claim_takes_its_sentence_along():
    # the model's claims 1 and 3 were accepted, 2 rejected: shown as [1] and [2]
    summary = "Almosen dienen den Kranken [1]. Gott verlangt es [2]. Sie sind Seelgerät [3]."
    kept, why = af.check_summary(summary, _claims(1, 3))
    assert kept == "Almosen dienen den Kranken [1]. Sie sind Seelgerät [2]."
    assert "cites claim [2], which was not accepted" in why


def test_english_quotation_marks_are_checked_too():
    kept, why = af.check_summary("It was given “for God’s sake alone” [1].", _claims(1))
    assert kept == "" and "quotes text no accepted binding carries" in why
    kept, why = af.check_summary("Sie heissen “almuͦsen” [1].", _claims(1))
    assert kept == "Sie heissen “almuͦsen” [1]." and why == ""


def test_check_bindings_numbers_claims_as_the_model_listed_them():
    it = ev.edition_item("kf", "U-17_0075", "daz geben wir in ze einem rehtem almuͦsen")
    pool = {it.evidence_id: it}
    ok, bad = af.check_bindings([
        {"claim": "a", "evidence": [{"id": it.evidence_id, "quote": "rehtem almuͦsen"}]},
        {"claim": "b", "evidence": [{"id": it.evidence_id, "quote": "erfunden"}]},
        {"claim": "c", "evidence": [{"id": it.evidence_id, "quote": "geben wir"}]}], pool)
    assert [c.number for c in ok] == [1, 3] and [c.number for c in bad] == [2]


def test_the_answer_shows_the_renumbered_summary():
    it = ev.edition_item("kf", "U-17_0075", "daz geben wir in ze einem rehtem almuͦsen "
                         "und lazzen es in durch got")

    def llm(prompt, system, max_tokens):
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [{"text": "q", "terms": []}]}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": [
                {"claim": "Erfunden.", "evidence": [{"id": it.evidence_id, "quote": "nein"}]},
                {"claim": "Almosen werden durch got gegeben.",
                 "evidence": [{"id": it.evidence_id, "quote": "durch got"}]}],
                "summary": "Almosen werden um Gottes willen gegeben [2]. Und mehr [1]."}), {}
        return json.dumps({"gaps": []}), {}

    res = af.ask("q", llm=llm, retrievers={"fulltext": lambda q, **k: [it]}, max_rounds=0)
    assert res.summary == "Almosen werden um Gottes willen gegeben [1]."
    assert "1 sentence(s) withheld" in res.summary_rejected


# ── 3. language, 4. split words and the holding ──────────────────────────────

def _bind_prompt(items):
    return af._BIND_PROMPT.format(question="Wie wird Almosen begründet?",
                                  passages=af._passages_block(items),
                                  holdings=af._holding_context(items))


def test_the_answer_is_asked_for_in_the_questions_language():
    assert "in der Sprache der Forschungsfrage" in _bind_prompt([])


def test_the_model_is_told_the_edition_splits_words():
    p = _bind_prompt([])
    assert '"su per" ist "super"' in p and '"Bremgar ten" ist "Bremgarten"' in p


def test_the_holding_is_explained_when_its_text_is_shown():
    kf = ev.edition_item("kf", "U-17_0061-02", "littera abbatisse su ꝑ per elemosina")
    p = _bind_prompt([kf])
    assert "Bestand kf:" in p and "Äbtissin (abbatissa)" in p
    eos = ev.edition_item("eos", "x", "der Almosen Schaf ner")
    assert "Almosenamt" in _bind_prompt([eos])
    assert "Zu den Beständen" not in _bind_prompt([])


def test_a_joined_quote_of_a_split_word_still_binds():
    it = ev.edition_item("kf", "U-17_0061-02",
                         "uber der bruͦ dˀ der almuͦsen lr̄a littera abbatisse su ꝑ per "
                         "elemosina fr̄i fratri bƺ bus")
    ok, _ = af.check_bindings([{"claim": "x", "evidence": [
        {"id": it.evidence_id, "quote": "littera abbatisse super elemosina"}]}],
        {it.evidence_id: it})
    assert ok and ok[0].bindings[0].layer == "reading"
