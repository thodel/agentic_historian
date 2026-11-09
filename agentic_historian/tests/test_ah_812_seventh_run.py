"""What the seventh run on tei showed (09.11.2026, #812).

``ask "Wie wird Almosen in den Königsfelder Urkunden begründet?"`` held 2 of
2 claims, no gloss against the glossary — and still answered wrongly:

* both claims rest on one quote of StAAG U.17/0109, though twelve passages
  were shown;
* the gloss was right — "dass sie Gott ewig für uns bitten sollen" — and the
  claim turned it round: "dass Gott ewig für die Seelen der Vorfahren betet".
  The sisters pray, for the donors; no mechanical check sees that;
* ten of thirteen empty searches asked our own transcriptions for a holding
  that is not in them.

So: a claim that rests only on a quote an earlier claim used is marked; one
more model call asks, claim by claim, whether it follows from its own gloss
— who acts, for whom — and a claim that does not is marked; both leave the
summary. The binding prompt asks for breadth. And "not in this holding" is
said once, not ten times.

The claims, quote and gloss are the answer's own, verbatim.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_seventh_run.py
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import evidence as ev
from agents import argument_agent as af

#: StAAG U.17/0109 (1330), the edition's characters.
U0109 = ("und swas si des hofes die wile geniessent, das geben wir in ze einem rehten "
         "almuͦsen ✳ und lassen ez in dur got und durch unser und aller unserr vordern "
         "sele willen, ✳ das si got eweclich fùr uns bitten sùllent.")
QUOTE = ("in ze einem rehten almuͦsen und lassen ez in dur got und durch unser und aller "
         "unserr vordern sele willen, das si got eweclich fùr uns bitten sùllent.")
GLOSS = ("in zu einem rechten Almosen und lassen es in durch Gott und durch unser und "
         "aller unser Vorfahren Seelen Willen, dass sie Gott ewig für uns bitten sollen.")
CLAIM_1 = "In den Königsfelder Urkunden wird Almosen als „rehen almuͦsen“ (pious almsgiving) bezeichnet."
CLAIM_2 = ("Der Almosen wird damit begründet, dass er dazu dient, dass Gott ewig für die "
           "Seelen der Vorfahren der Stifter betet.")
SUMMARY = ("Die Analyse zeigt, dass Almosen in den Königsfelder Urkunden als rehes Almosen "
           "bezeichnet wird [1] und dass diese Gabe mit dem Ziel gerechtfertigt wird, dass "
           "Gott ewig für die Seelen der Vorfahren der Stifter betet [2].")
WHY = "Laut Übersetzung bitten „sie“ (die Schwestern) Gott für „uns“; nicht Gott betet."


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


@pytest.fixture
def u0109():
    return ev.edition_item("kf", "U-17_0109", U0109, datierung="1330")


class _LLM:
    """The seventh run's bind answer; the verify answer is the test's."""

    def __init__(self, item, checks=None, fail_verify=False):
        self.item, self.checks, self.fail_verify = item, checks, fail_verify
        self.prompts = []

    def __call__(self, prompt, system, max_tokens):
        self.prompts.append(prompt)
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [{"text": "q", "terms": []}]}), {}
        if "Passagen (Kennung" in prompt:
            ev_ = [{"id": self.item.evidence_id, "quote": QUOTE, "gloss": GLOSS}]
            return json.dumps({"claims": [{"evidence": ev_, "claim": CLAIM_1},
                                          {"evidence": ev_, "claim": CLAIM_2}],
                               "summary": SUMMARY}), {}
        if "Prüfe jede Behauptung" in prompt:
            if self.fail_verify:
                raise ConnectionError("GPUStack 502")
            return json.dumps({"checks": self.checks or []}), {}
        return json.dumps({"gaps": []}), {}


def _ask(item, llm, retrievers=None):
    return af.ask("Wie wird Almosen begründet?", llm=llm,
                  retrievers=retrievers or {"fulltext": lambda q, **k: [item]},
                  max_rounds=0)


# ── one quote, two claims ────────────────────────────────────────────────────

def test_the_second_claim_on_the_same_quote_is_marked(u0109):
    res = _ask(u0109, _LLM(u0109))
    assert res.claims[0].checks == []
    assert res.claims[1].checks == ["stützt sich nur auf dasselbe Zitat wie Behauptung [1]"]


def test_a_claim_with_one_new_quote_is_not_reuse():
    a = af.Claim("a", [af.Binding("ev-1", "x y", ok=True)], 1)
    b = af.Claim("b", [af.Binding("ev-1", "x y", ok=True),
                       af.Binding("ev-2", "z w", ok=True)], 2)
    c = af.Claim("c", [af.Binding("ev-1", "x y z", ok=True)], 3)
    af.check_reuse([a, b, c])
    assert a.checks == b.checks == c.checks == []


# ── does the claim follow from its own gloss? ────────────────────────────────

def test_the_claim_that_turns_the_gloss_round_is_marked(u0109):
    llm = _LLM(u0109, checks=[{"n": 1, "follows": True, "why": ""},
                              {"n": 2, "follows": False, "why": WHY}])
    res = _ask(u0109, llm)
    assert f"Folgt nicht aus der eigenen Übersetzung: {WHY}" in res.claims[1].checks
    assert not any(c.startswith("Folgt") for c in res.claims[0].checks)
    # the model is shown each claim with its quote and its gloss
    verify_prompt = next(p for p in llm.prompts if "Prüfe jede Behauptung" in p)
    assert f"[2] Behauptung: {CLAIM_2}" in verify_prompt
    assert f"Übersetzung: „{GLOSS}“" in verify_prompt
    assert "nicht Gott" in verify_prompt


def test_the_summary_resting_on_a_marked_claim_is_withheld(u0109):
    res = _ask(u0109, _LLM(u0109, checks=[{"n": 2, "follows": False, "why": WHY}]))
    assert res.summary == ""
    assert "cites claim [2], which did not pass the claim checks" in res.summary_rejected


def test_the_answer_shows_the_mark_and_why(u0109):
    page = af.render_markdown(_ask(u0109, _LLM(u0109, checks=[
        {"n": 2, "follows": False, "why": WHY}])))
    assert (f"2. {CLAIM_2} ⚠️ *Prüfung nicht bestanden — nicht in der "
            "Zusammenfassung*") in page
    assert "   ⚠️ stützt sich nur auf dasselbe Zitat wie Behauptung [1]" in page
    assert f"   ⚠️ Folgt nicht aus der eigenen Übersetzung: {WHY}" in page
    assert f"1. {CLAIM_1}\n" in page                 # the first claim is not marked


@pytest.mark.parametrize("checks", [
    [{"n": 2}],                                      # no verdict is no failure
    [{"n": 2, "follows": "nein"}],                   # only a literal false counts
    [{"n": 9, "follows": False, "why": "x"}],        # no such claim
    [{"n": "zwei", "follows": False}],
    ["kaputt"],
])
def test_an_unclear_verdict_marks_nothing(u0109, checks):
    res = _ask(u0109, _LLM(u0109, checks=checks))
    assert not any(c.startswith("Folgt") for cl in res.claims for c in cl.checks)


def test_a_check_that_could_not_run_marks_nothing_and_says_so(u0109):
    res = _ask(u0109, _LLM(u0109, fail_verify=True))
    assert not any(c.startswith("Folgt") for cl in res.claims for c in cl.checks)
    assert any(e.startswith("verify: not run") for e in res.audit.events)


def test_no_claim_no_verify_call(u0109):
    llm = _LLM(u0109)
    af.verify([], llm=llm, audit=af.Audit())
    assert llm.prompts == []


def test_the_command_line_counts_claims_that_failed_the_checks(u0109, monkeypatch,
                                                                 capsys, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli_812_7", PKG / "__main__.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    res = _ask(u0109, _LLM(u0109, checks=[{"n": 2, "follows": False, "why": WHY}]))
    monkeypatch.setattr(af, "answer", lambda *a, **k: af.Answered(res, tmp_path))
    assert cli.ask_cmd(argparse.Namespace(question="q", bestand=None, year_from=None,
                                          year_to=None, publish=False, out=None)) == 0
    assert "flagged   : 1 claim(s) that failed the claim checks" in capsys.readouterr().out


# ── breadth ──────────────────────────────────────────────────────────────────

def test_the_model_is_asked_for_breadth_and_one_quote_per_claim():
    p = af._BIND_PROMPT
    assert "verschiedene Befunde aus verschiedenen\nUrkunden" in p
    assert "Zwei Behauptungen stützen sich nie auf\ndasselbe Zitat" in p
    assert "auch nicht, wer handelt und für wen" in p


# ── "not in this holding", said once ────────────────────────────────────────

def test_a_holding_not_in_our_transcriptions_is_said_once(u0109):
    asked = []

    def passages(query, *, bestand=None, limit=10):
        asked.append(query)
        raise af.NotInHolding(f"none of our own transcriptions is in holding {bestand}")

    pool, coverage = {}, []
    queries = [{"id": f"q{i}", "text": f"Teilfrage {i}", "terms": []} for i in range(4)]
    af.retrieve(queries, retrievers={"passages": passages,
                                     "fulltext": lambda q, **k: [u0109]},
                pool=pool, coverage=coverage, bestand="kf")
    af.retrieve([{"id": "r1", "text": "Nachfrage", "terms": []}],
                retrievers={"passages": passages, "fulltext": lambda q, **k: []},
                pool=pool, coverage=coverage, bestand="kf")
    assert asked == ["Teilfrage 0"]
    rows = [c for c in coverage if c["retriever"] == "passages"]
    assert [(c["status"], c["error"]) for c in rows] == [
        ("not in holding", "none of our own transcriptions is in holding kf")]


def test_not_in_holding_leaves_the_answer_complete(u0109):
    def passages(query, *, bestand=None, limit=10):
        raise af.NotInHolding("none of our own transcriptions is in holding kf")

    res = _ask(u0109, _LLM(u0109), retrievers={"passages": passages,
                                               "fulltext": lambda q, **k: [u0109]})
    assert res.complete
    page = af.render_markdown(res)
    assert page.count("not in holding") == 1
