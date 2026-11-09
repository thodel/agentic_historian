"""What the fifth run on tei showed (08.11.2026, #812).

``ask "Wie wird Almosen in den Königsfelder Urkunden begründet?"`` held 2 of
3 claims, every quote verbatim — and both claims misread the source:

* StAAG AA/0428 (1335): "mit der bescheidenheit, das si … ir jartzit began
  sùllent an sant Margareten tag" became alms given "aus Bescheidenheit … zum
  Gedenken an den Festtag der Heiligen Margareta". It is a *condition*: the
  queen's anniversary is kept *on* St Margaret's day;
* StAAG U.17/0143 (1334): "mit einer gesun gen selmesse" became "einer
  gesunden Selbsterkenntnis". It is a *sung requiem mass*.

The binding check can say a quote is in its passage, never whether a claim
reads it. So: the model writes a word-for-word gloss of every quote before
its claim, and the reader sees it; a glossary names the charter words that a
reader of today's German misreads; the plan searches in the sources'
spelling, not in modern words; and the model is a setting.

The passages are the edition's own characters as the answer showed them.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_fifth_run.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
import evidence as ev
from agents import argument_agent as af

#: StAAG AA/0428, fol. 121v–122r (copy of charter 0151a, 1335).
AA0428 = ("mit dem almuͦsen unser lieben frowen un̄ und swester, chuneginne Elizabethen, "
          "un sˀs sers 111. 122. lieben herren und bruͦders, chùnig Fridrichs, seligen, "
          "husfrowen, ✳ mit der be¬scheidenheit, das si eweclich allù jar ir jartzit "
          "began sùllent an sant Marga¬ reten tag . ✳ also das dù swester, dù der jartzit "
          "pfliget, das gelt in nemen sol ✳ und dem convente und den siechen swestern "
          "geben sol ein pfunt umb guͦt vische")
#: StAAG U.17/0143 (1334).
U0143 = ("mit einer gesun gˀ gen selmesse und mit an dnˀ dern guͦten dingen ✳ un̄ und dù "
         "swes tˀ ter , dù der herschaft jartzit pfliget, dù sol dù eilf stucke in nemen "
         "✳ und sol den bruͦ dnˀ dern da von siben schilling geben ✳ zuͦ dem male, ✳ und "
         "ein stucke an das lieht in der kylchen, ✳ un̄ und zwei stucke zuͦ der spende "
         "gemeinen, armen lùten, ✳ und den swestern in dem chloster ein pfunt zuͦ dem "
         "male, ✳ und ein pfunt sol man teilen under die swes tnˀ tern")


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


@pytest.fixture
def items():
    return (ev.edition_item("kf", "AA_0428_0151a", AA0428, datierung="1335"),
            ev.edition_item("kf", "U-17_0143", U0143, datierung="1334"))


# ── 1. a gloss of every quote, shown beside it ───────────────────────────────

def _answer(items, claims):
    def llm(prompt, system, max_tokens):
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [{"text": "q", "terms": []}]}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": claims}), {}
        return json.dumps({"gaps": []}), {}

    return af.ask("Wie wird Almosen begründet?", llm=llm,
                  retrievers={"fulltext": lambda q, **k: list(items)}, max_rounds=0)


def test_the_gloss_is_kept_and_shown_under_its_quote(items):
    aa, _ = items
    res = _answer(items, [{"evidence": [{
        "id": aa.evidence_id,
        "quote": "mit der bescheidenheit, das si eweclich allù jar ir jartzit began "
                 "sùllent an sant Margareten tag",
        "gloss": "unter der Bedingung, dass sie für immer jedes Jahr ihre Jahrzeit am "
                 "Margaretentag begehen sollen"}],
        "claim": "Die Gabe ist an die jährliche Jahrzeit der Königin gebunden."}])
    b = res.claims[0].bindings[0]
    assert b.ok and b.layer == "reading"
    assert b.gloss.startswith("unter der Bedingung")
    page = af.render_markdown(res)
    assert ("Übersetzung (Modell, ungeprüft): „unter der Bedingung, dass sie für immer "
            "jedes Jahr ihre Jahrzeit am Margaretentag begehen sollen“") in page


def test_a_gloss_is_shown_not_checked(items):
    """The fifth run's misreading, glossed as the model read it: the quote
    binds (it is in the passage), and the reader sees the misreading."""
    _, u = items
    res = _answer(items, [{"evidence": [{
        "id": u.evidence_id, "quote": "mit einer gesungen selmesse",
        "gloss": "mit einer gesunden Selbsterkenntnis"}],
        "claim": "Almosen sind Ausdruck gesunder Selbsterkenntnis."}])
    assert res.claims and res.claims[0].bindings[0].ok
    assert "„mit einer gesunden Selbsterkenntnis“" in af.render_markdown(res)


def test_a_quote_without_a_gloss_still_binds_and_shows_no_empty_line(items):
    _, u = items
    res = _answer(items, [{"claim": "x", "evidence": [
        {"id": u.evidence_id, "quote": "zuͦ der spende gemeinen, armen lùten"}]}])
    assert res.claims[0].bindings[0].gloss == ""
    assert "Übersetzung (Modell" not in af.render_markdown(res)


def test_the_model_is_asked_for_the_gloss_before_the_claim():
    p = af._BIND_PROMPT
    assert "Übersetze jedes Zitat zuerst Wort für Wort" in p
    schema = p[p.index('{{"claims"'):]      # the template, braces still doubled
    assert schema.index('"gloss"') < schema.index('"claim": "..."')


# ── 2. the charter words a reader of today's German misreads ─────────────────

def _bind_prompt(items_):
    return af._BIND_PROMPT.format(question="q", passages=af._passages_block(items_),
                                  holdings=af._holding_context(items_))


@pytest.mark.parametrize("entry", [
    "bescheidenheit: Bestimmung, Bedingung",
    "jartzit, jarzit (lat. anniversarium): Jahrzeit",
    "selmesse: Seelmesse",
    "gesungen: gesungen, nicht gesund",
    "spende: Armenspende",
    "siech: krank",
    "selig (\"chùnig Fridrichs, seligen\"): verstorben",
])
def test_the_glossary_reaches_the_binding_stage(items, entry):
    assert entry in _bind_prompt(list(items))


def test_the_split_word_of_this_run_is_named():
    assert '"gesun gen" ist "gesungen"' in af._BIND_PROMPT


# ── 3. search in the sources' spelling ───────────────────────────────────────

def _plan_prompt():
    return af._PLAN_PROMPT.format(question="Wie wird Almosen begründet?",
                                  holdings="- kf: KF")


def test_the_plan_is_told_to_search_the_sources_words_not_todays():
    p = _plan_prompt()
    assert '"jartzit"' in p and '"spende"' in p and '"selmesse"' in p
    assert "keine heutigen Wörter" in p
    assert '("Königsfelder Urkunden")' in p
    assert "bescheidenheit: Bestimmung, Bedingung" in p


def test_the_plan_prompt_still_asks_for_its_json():
    p = _plan_prompt()
    assert p.rstrip().endswith('"bestand": "kf" oder null}')
    assert "{holdings}" not in p and "<<GLOSSAR>>" not in p


# ── 4. the model is a setting ────────────────────────────────────────────────

def test_the_model_defaults_to_the_text_role(monkeypatch):
    monkeypatch.setattr(config, "ARGUMENT_MODEL", "")
    monkeypatch.setattr(config, "GPUSTACK_MODEL_TEXT", "gpt-oss-120b")
    assert af.argument_model() == "gpt-oss-120b"
    monkeypatch.setattr(config, "ARGUMENT_MODEL", "minimax-m2.7")
    assert af.argument_model() == "minimax-m2.7"


def test_the_default_llm_asks_the_configured_model(monkeypatch):
    from utils import gpustack_client as gs

    seen = {}
    monkeypatch.setattr(config, "ARGUMENT_MODEL", "minimax-m2.7")
    monkeypatch.setattr(gs, "chat", lambda prompt, **kw: seen.update(kw) or "{}")
    monkeypatch.setattr(gs, "last_usage", dict)
    af.gpustack_llm("p", "s", 100)
    assert seen["model"] == "minimax-m2.7" and seen["agent_name"] == "agent_f"
    assert seen["max_tokens"] == 100


def test_the_answer_names_the_model_it_asked(monkeypatch, items):
    monkeypatch.setattr(config, "ARGUMENT_MODEL", "minimax-m2.7")
    monkeypatch.setattr(af, "gpustack_llm", lambda p, s, m: (json.dumps(
        {"subquestions": [{"text": "q", "terms": []}]} if "Zerlege" in p
        else {"claims": []} if "Passagen (Kennung" in p else {"gaps": []}), {}))
    res = af.ask("q", retrievers={"fulltext": lambda q, **k: list(items)}, max_rounds=0)
    assert "model: minimax-m2.7" in res.audit.events
    # an injected model is the caller's to name
    res = af.ask("q", llm=lambda p, s, m: ("{}", {}),
                 retrievers={"fulltext": lambda q, **k: []}, max_rounds=0)
    assert not any(e.startswith("model:") for e in res.audit.events)
