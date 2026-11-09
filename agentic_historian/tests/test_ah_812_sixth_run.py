"""What the sixth run on tei showed (09.11.2026, #812).

``ask "Wie wird Almosen in den Königsfelder Urkunden begründet?"`` held 2 of
2 claims, and the glosses #848 added showed what the model read:

* StAAG U.17/0109 (1330): "die wile geniessent" as "die Wille genießen"
  (*solange* sie nutzen), "unserr vordern sele willen" as "heiligen Willen"
  (*der Seelen* unserer Vorfahren) — the charter's own reason for the alms;
* StAAG AA/0428 (1335): "mit der bescheidenheit" still as "Bescheidenheit",
  "jartzit began" as "Jahrzeit beginnen" — both against the glossary the
  model had been shown.

And reflection, never told how a search term must look, searched
"Seelenheil", "Ablass", "Purgatorium", "Zehnt": all empty.

So: reflection gets the plan's rule for search terms; the glossary names
sele, die wile, began, fùr uns bitten, koͧffen; and a gloss that reads a
glossary word in its modern sense is caught mechanically — the claim is
shown, marked, and carries no sentence of the summary (decided 09.11.).

The quotes and glosses are the answer's own, verbatim.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_sixth_run.py
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

#: StAAG U.17/0109 (1330), the edition's characters as the answer showed them.
U0109 = ("und swas si des hofes die wile geniessent, das geben wir in ze einem rehten "
         "almuͦsen ✳ und lassen ez in dur got und durch unser und aller unserr vordern "
         "sele willen, ✳ das si got eweclich fùr uns bitten sùllent.")
U0109_QUOTE = ("und swas si des hofes die wile geniessent, das geben wir in ze einem rehten "
               "almuͦsen und lassen ez in dur got und durch unser und aller unserr vordern "
               "sele willen, das si got eweclich fùr uns bitten sùllent.")
U0109_GLOSS = ("und was sie des Hofes die Wille genießen, das geben wir in zu einem rechten "
               "Almosen und lassen es in durch Gott und durch unser und aller unser "
               "Vorfahren heiligen Willen, dass sie Gott ewiglich für uns bitten sollen.")

#: StAAG AA/0428, fol. 121v–122r (copy of charter 0151a, 1335).
AA0428 = ("gechoͧffet han den geistlichen frowen, der ✳ ✳ eptischin und dem ✳ ✳ convente "
          "unsers chlosters ze Chùngesvelt, mit dem almuͦsen unser lieben frowen un̄ und "
          "swester, chuneginne Elizabethen, un sˀs sers 111. 122. lieben herren und "
          "bruͦders, chùnig Fridrichs, seligen, husfrowen, ✳ mit der be¬scheidenheit, das "
          "si eweclich allù jar ir jartzit began sùllent an sant Marga¬ reten tag .")
AA0428_QUOTE = ("gechoͧffet han den geistlichen frowen, der eptischin und dem convente unsers "
                "chlosters ze Chùngesvelt, mit dem almuͦsen unser lieben frowen und swester, "
                "chuneginne Elizabethen, un sers 111. 122. lieben herren und bruͦders, chùnig "
                "Fridrichs, seligen, husfrowen, mit der bescheidenheit, das si eweclich allù "
                "jar ir jartzit began sùllent an sant Marga reten tag .")
AA0428_GLOSS = ("gechöffet haben den geistlichen Frauen, der Äbtissin und dem Konvent unseres "
                "Klosters zu Königsfelden, mit dem Almosen unser lieben Frauen und Schwester, "
                "Königin Elisabeth, und ihres 111. 122. lieben Herren und Brüder, König "
                "Friedrich, seligen, Hausfrauen, mit der Bescheidenheit, dass sie ewiglich "
                "alle Jahr ihre Jahrzeit beginnen sollen an Sankt Margarethen Tag.")


@pytest.fixture(autouse=True)
def _no_federation(monkeypatch):
    monkeypatch.setattr(af, "fetch_document", lambda source, doc_id: None)


@pytest.fixture
def items():
    return (ev.edition_item("kf", "U-17_0109", U0109, datierung="1330"),
            ev.edition_item("kf", "AA_0428_0151a", AA0428, datierung="1335"))


# ── the gloss against the glossary ───────────────────────────────────────────

def test_the_sixth_runs_gloss_of_the_pro_anima_formula_is_caught():
    flags = af.check_gloss(U0109_QUOTE, U0109_GLOSS)
    assert flags == [
        "„sele“ als „heiligen“ übersetzt; Glossar: Seele",
        "„die wile“ als „Wille“ übersetzt; Glossar: solange, während",
    ]


def test_the_sixth_runs_gloss_of_the_condition_is_caught():
    flags = af.check_gloss(AA0428_QUOTE, AA0428_GLOSS)
    assert flags == [
        "„bescheidenheit“ als „Bescheidenheit“ übersetzt; Glossar: Bedingung, Bestimmung",
        "„began“ als „beginnen“ übersetzt; Glossar: begehen, feiern",
    ]


def test_the_fifth_runs_misreading_is_caught_too():
    assert af.check_gloss("mit einer gesun gen selmesse",
                          "mit einer gesunden Selbsterkenntnis") == [
        "„gesungen“ als „gesunden“ übersetzt; Glossar: gesungen",
        "„selmesse“ als „Selbsterkenntnis“ übersetzt; Glossar: Seelmesse",
    ]


@pytest.mark.parametrize("quote,gloss", [
    (U0109_QUOTE, ("und was sie vom Hof nutzen, solange sie leben, das geben wir ihnen "
                   "als rechtes Almosen um Gottes willen und um der Seelen unserer und "
                   "aller unserer Vorfahren willen, damit sie ewig für uns zu Gott beten.")),
    ("mit der bescheidenheit, das si … ir jartzit began sùllent",
     "unter der Bedingung, dass sie ihre Jahrzeit begehen sollen"),
    ("mit der bescheidenheit", "mit der Bestimmung (bescheidenheit)"),
    ("mit einer gesun gen selmesse", "mit einer gesungenen Seelmesse"),
    ("chùnig Fridrichs, seligen", "des verstorbenen Königs Friedrich"),
])
def test_a_gloss_that_follows_the_glossary_passes(quote, gloss):
    assert af.check_gloss(quote, gloss) == []


def test_um_gottes_willen_is_not_die_wile():
    """"willen" in a right gloss ("um Gottes willen") is not the misread "Wille"."""
    assert af.check_gloss("die wile si lebent, dur got",
                          "derweil sie leben, um Gottes willen") == []


def test_no_gloss_no_flag():
    assert af.check_gloss(U0109_QUOTE, "") == []


# ── a flagged claim: shown, marked, out of the summary ───────────────────────

def _answer(items, claims, summary):
    def llm(prompt, system, max_tokens):
        if "Zerlege die Frage" in prompt:
            return json.dumps({"subquestions": [{"text": "q", "terms": []}]}), {}
        if "Passagen (Kennung" in prompt:
            return json.dumps({"claims": claims, "summary": summary}), {}
        return json.dumps({"gaps": []}), {}

    return af.ask("Wie wird Almosen begründet?", llm=llm,
                  retrievers={"fulltext": lambda q, **k: list(items)}, max_rounds=0)


def _sixth_run(items, *, good_gloss=False):
    u, aa = items
    gloss = ("solange sie den Hof nutzen … um der Seelen unserer Vorfahren willen, "
             "damit sie ewig für uns zu Gott beten") if good_gloss else U0109_GLOSS
    return _answer(items, [
        {"evidence": [{"id": u.evidence_id, "quote": U0109_QUOTE, "gloss": gloss}],
         "claim": "Die Almosen werden mit dem Seelenheil der Stifter begründet."},
        {"evidence": [{"id": aa.evidence_id, "quote": AA0428_QUOTE, "gloss": AA0428_GLOSS}],
         "claim": "Die Almosen werden aus Bescheidenheit gegeben."}],
        "Die Stifter geben um ihrer Seelen willen [1]. Sie geben aus Bescheidenheit [2].")


def test_a_flagged_claim_stays_shown_but_carries_no_summary_sentence(items):
    res = _sixth_run(items, good_gloss=True)
    assert len(res.claims) == 2 and not res.rejected_claims
    assert res.claims[0].flags == [] and len(res.claims[1].flags) == 2
    assert res.summary == "Die Stifter geben um ihrer Seelen willen [1]."
    assert "cites claim [2], whose gloss contradicts the glossary" in res.summary_rejected


def test_the_answer_marks_the_claim_and_names_each_misreading(items):
    page = af.render_markdown(_sixth_run(items))
    assert ("2. Die Almosen werden aus Bescheidenheit gegeben. ⚠️ *Übersetzung "
            "widerspricht dem Glossar — nicht in der Zusammenfassung*") in page
    assert ("⚠️ Fraglich gelesen: „bescheidenheit“ als „Bescheidenheit“ übersetzt; "
            "Glossar: Bedingung, Bestimmung") in page
    assert "⚠️ Fraglich gelesen: „sele“ als „heiligen“ übersetzt; Glossar: Seele" in page
    # with both claims flagged, no summary sentence stands
    assert "## Zusammenfassung" not in page


def test_the_command_line_counts_the_flagged_claims(items, monkeypatch, capsys, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli_812_6", PKG / "__main__.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    res = _sixth_run(items, good_gloss=True)
    monkeypatch.setattr(af, "answer", lambda *a, **k: af.Answered(res, tmp_path))
    args = argparse.Namespace(question="q", bestand=None, year_from=None, year_to=None,
                              publish=False, out=None)
    assert cli.ask_cmd(args) == 0
    assert ("flagged   : 1 claim(s) whose gloss contradicts the glossary"
            in capsys.readouterr().out)


# ── search terms in reflection; the glossary's new words ─────────────────────

def _reflect_prompt():
    return af._REFLECT_PROMPT.format(question="q", subs="s", claims="(keine)",
                                     gaps="(keine)")


def test_reflection_is_told_how_a_search_term_must_look():
    p = _reflect_prompt()
    assert "keine heutigen Wörter" in p and '"Seelenheil", "Ablass"' in p
    assert "bescheidenheit: Bestimmung, Bedingung" in p
    assert p.rstrip().endswith('"why": "..."}]}')


def test_plan_and_reflection_share_one_rule():
    plan = af._PLAN_PROMPT.format(question="q", holdings="- kf: KF")
    assert af._TERMS_RULE in plan and af._TERMS_RULE in _reflect_prompt()


@pytest.mark.parametrize("entry", [
    "sele, sel, selen: Seele, Seelen — nicht selig, nicht heilig",
    "die wile: solange, während — nicht Wille",
    "began, begangen: (eine Jahrzeit) begehen, feiern — nicht beginnen",
    "fùr uns bitten (lat. orare pro nobis): für uns beten",
    "koͧffen, gekoͧft, gechoͧffet: kaufen, gekauft",
])
def test_the_glossary_names_the_sixth_runs_words(entry):
    assert entry in af._BIND_PROMPT and entry in af._TERMS_RULE
