"""What the eighth run on tei showed (12.11.2026, #812): the subject unsearched.

``/ask "Welche Pflichten hat der Hofmeister von Königsfelden gegenüber dem
Konvent?" bestand:kf`` — the plan's terms were gülte, jartzit, selmesse,
almuͦsen, zins, "fùr uns bitten", "durch got": glossary words, none of them
the subject. 81 passages, one naming the Hofmeister; of three claims two
quoted text that was not in the passage and the third did not follow from
its gloss; the reflection, shown only the one bound claim, asked for nothing.
The checks held. The search had never looked for what was asked.

So: the question's own nouns are searched whatever the plan says (a
sub-question ``q0`` of the question itself, said in the audit); among passages
of equal score the ones naming the subject are shown first; and the reflection
sees the claims that fell as well as those that stand.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_eighth_run.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import evidence as ev                           # noqa: E402
from agents import argument_agent as af         # noqa: E402

QUESTION = "Welche Pflichten hat der Hofmeister von Königsfelden gegenüber dem Konvent?"
HOLDINGS = {"kf": "KF — Königsfelden register", "eos": "EOS — HGB Basel (documents & spans)"}
#: StAAG U.17/0828 (the one passage of the eighth run that named the Hofmeister).
WITH_SUBJECT = ("das unn szer hofmeister fùr sich selbs har inn nuͦtz anders getàn, dann das "
                "imm unn szer frow die aͤptissin guͦter gedaͧchtnùstz het bevolhen")
#: StAAG U.17/0109, found for "almuͦsen".
WITHOUT = ("und swas si des hofes die wile geniessent, das geben wir in ze einem rehten "
           "almuͦsen und lassen ez in dur got")


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)
    monkeypatch.setattr(af, "_holdings", lambda: HOLDINGS)


class _LLM:
    """The eighth run's plan: glossary terms, no subject. Binds nothing."""

    def __init__(self):
        self.prompts = []

    def __call__(self, prompt, system, max_tokens):
        self.prompts.append(prompt)
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [
                {"text": "Welche finanziellen Abgaben leistet der Hofmeister?",
                 "terms": ["almuͦsen", "zins"]},
                {"text": "Welche liturgischen Pflichten?", "terms": ["jartzit"]}],
                "bestand": "kf"}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": [], "summary": ""}), {}
        return json.dumps({"gaps": []}), {}


# ── the subject of the question ──────────────────────────────────────────────

def test_the_questions_nouns_are_its_subject_not_the_holding_or_the_asking():
    assert af.subject_terms(QUESTION, HOLDINGS) == ["pflichten", "hofmeister", "konvent"]
    assert af.subject_terms("Wie wird Almosen in den Königsfelder Urkunden des 14. "
                            "Jahrhunderts begründet?", HOLDINGS) == ["almosen"]
    assert af.subject_terms("", HOLDINGS) == []


def test_the_subject_is_added_to_a_plan_that_leaves_it_out_and_said():
    audit = af.Audit(max_tokens=10**6, max_calls=10)
    plan_ = af.Plan(QUESTION, [{"id": "q1", "text": "Abgaben?", "terms": ["almuͦsen", "zins"]}])
    out = af.with_subject(plan_, QUESTION, HOLDINGS, audit)
    assert out.subquestions[0] == {"id": "q0", "text": QUESTION,
                                   "terms": ["pflichten", "hofmeister", "konvent"]}
    assert audit.events == ["plan: the question's subject was not among the search "
                            "terms — added: pflichten, hofmeister, konvent"]


def test_a_plan_that_already_searches_the_subject_is_left_alone():
    audit = af.Audit(max_tokens=10**6, max_calls=10)
    plan_ = af.Plan(QUESTION, [
        {"id": "q1", "text": "a", "terms": ["hofmeister", "pflichten"]},
        {"id": "q2", "text": "b", "terms": ["konvent"]}])
    before = [dict(s) for s in plan_.subquestions]
    assert af.with_subject(plan_, QUESTION, HOLDINGS, audit).subquestions == before
    assert audit.events == []


def test_ask_searches_the_subject_whatever_the_plan_says():
    asked = []

    def fulltext(query, *, bestand=None, limit=10):
        asked.append((query, bestand))
        return []

    res = af.ask(QUESTION, bestand="kf", llm=_LLM(), retrievers={"fulltext": fulltext},
                 max_rounds=0)
    assert ("hofmeister", "kf") in asked and ("konvent", "kf") in asked
    q0 = [c for c in res.coverage if c["subquestion"] == "q0"]
    assert {c["query"] for c in q0} >= {"pflichten", "hofmeister", "konvent"}
    assert res.plan.subquestions[0]["id"] == "q0"
    assert any(e.startswith("plan: the question's subject") for e in res.audit.events)
    assert "hofmeister" in af.render_markdown(res)


# ── the sample ───────────────────────────────────────────────────────────────

def test_a_passage_naming_the_subject_is_shown_before_one_that_does_not():
    a = ev.edition_item("kf", "U-17_0109", WITHOUT, datierung="1330")
    b = ev.edition_item("kf", "U-17_0828", WITH_SUBJECT, datierung="1331")
    for it in (a, b):
        it.retrieval = {"score": 0.0, "subquestion": "q1"}
    plan_ = af.Plan(QUESTION, [{"id": "q1", "text": QUESTION, "terms": []}])
    # same holding, century and language: one group, where the id order decided before
    pool = {it.evidence_id: it for it in sorted((a, b), key=lambda i: i.evidence_id)}
    sampled, _ = af.sample(pool, plan_, size=1)
    assert sampled == [b.evidence_id]
    assert b.retrieval["subject_hits"] == 1 and a.retrieval["subject_hits"] == 0


# ── the reflection ───────────────────────────────────────────────────────────

def test_the_reflection_sees_the_claims_that_fell():
    llm = _LLM()
    audit = af.Audit(max_tokens=10**6, max_calls=10)
    plan_ = af.Plan(QUESTION, [{"id": "q1", "text": QUESTION, "terms": []}])
    standing = af.Claim("Der Hofmeister verwaltet den Hof.", [])
    marked = af.Claim("Der Hofmeister stellt den Hof zur Verfügung.", [],
                      checks=["Folgt nicht aus der eigenen Übersetzung"])
    fell = af.Claim("Der Hofmeister bewahrt die Güter.", [])
    af.reflect(QUESTION, plan_, [standing, marked], [], llm=llm, audit=audit, round_no=1,
               rejected=[fell])
    prompt = llm.prompts[-1]
    assert "[1] Der Hofmeister verwaltet den Hof." in prompt
    assert "[2]" not in prompt, "a marked claim is not among the standing ones"
    assert ("- Der Hofmeister stellt den Hof zur Verfügung. — Folgt nicht aus der eigenen "
            "Übersetzung") in prompt
    assert "- Der Hofmeister bewahrt die Güter. — Zitat nicht in der genannten Passage" in prompt
