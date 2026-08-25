"""#358 cause 2: a cursive genus must not demote a cursive species.

Agent B described BAT_664 as "Kursive" where an earlier run said "Kurrent". Same
page, same models, and the kraken ranking inverted — measured on tei:

    kursive:  0.80  zenodo.13942714  ['script','century']          "Humanistische Kursive"
              0.15  zenodo.15030337  ['script-mismatch','lang',…]  "Kurrent"
    kurrent:  1.00  zenodo.15030337  ['script','lang','century']

`zenodo.13942714` is the model whose output for this page is Japanese. It won
because `kursive` matched its tag exactly while every Kurrent model was pushed down
by SCRIPT_MISMATCH (-0.35). The penalty did the damage: not an absent match, an
active demotion of the right models.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_358_script_families.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.model_selector import (  # noqa: E402
    SCRIPT_MISMATCH, KrakenModel, SourceCriteria, score_model, scripts_related,
)


def _model(script, lang="de", centuries=(15, 16), model_id="m"):
    return KrakenModel(model_id=model_id, name=model_id, script=script,
                       lang=lang, centuries=list(centuries))


# ── the family relation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("kursive", "kurrent"),
    ("kurrent", "kursive"),
    ("kursive", "sütterlin"),
    ("kurrent", "sütterlin"),
    ("kursive", "humanistisch"),
    ("kursive", "halbkursive"),
])
def test_cursive_scripts_are_related(a, b):
    """Deutsche Kurrent IS a German cursive; Sütterlin is a Kurrent variant."""
    assert scripts_related(a, b) is True


@pytest.mark.parametrize("a,b", [
    ("textura", "rotunda"),
    ("kurrent", "textura"),
    ("fraktur", "kursive"),
    ("caroline", "kurrent"),
    ("antiqua", "kursive"),
])
def test_unrelated_scripts_stay_unrelated(a, b):
    """The relation must stay narrow. Textura and Rotunda are both gothic book
    hands, but a model trained on one produces garbage on the other — which is
    exactly what SCRIPT_MISMATCH exists to catch (#191)."""
    assert scripts_related(a, b) is False


def test_a_script_is_not_related_to_itself():
    """Identity is an EXACT match and takes the 0.6 branch; reporting it as related
    would quietly downgrade every exact match to fuzzy."""
    assert scripts_related("kurrent", "kurrent") is False


# ── the scoring consequence ──────────────────────────────────────────────────

KURSIVE = SourceCriteria(script="kursive", century=15, lang="de")
KURRENT = SourceCriteria(script="kurrent", century=15, lang="de")


def test_a_kurrent_model_is_no_longer_penalised_for_a_kursive_source():
    """The live defect: 0.15, below models with no language match at all."""
    m = score_model(_model("Kurrent"), script=KURSIVE.script,
                    lang=KURSIVE.lang, century=KURSIVE.century)
    assert "script-mismatch" not in m.matched_on
    assert "script~" in m.matched_on
    assert m.score >= 0.7


def test_the_exact_match_still_scores_higher_than_the_family_match():
    """Relatedness must not flatten the signal — an exact script match is still
    the stronger evidence."""
    exact = score_model(_model("Kurrent"), script="kurrent", lang="de", century=15)
    fuzzy = score_model(_model("Kurrent"), script="kursive", lang="de", century=15)
    assert exact.score > fuzzy.score


def test_a_genuinely_wrong_script_is_still_penalised():
    """#191's Textura-for-a-cursive-hand must keep its penalty."""
    m = score_model(_model("Textura"), script=KURSIVE.script,
                    lang=KURSIVE.lang, century=KURSIVE.century)
    assert "script-mismatch" in m.matched_on


def test_the_kurrent_path_is_unchanged():
    """A source correctly described as Kurrent scored 1.00 before and must still."""
    m = score_model(_model("Kurrent"), script=KURRENT.script,
                    lang=KURRENT.lang, century=KURRENT.century)
    assert "script" in m.matched_on and m.score == pytest.approx(1.0)


def test_the_penalty_constant_is_unchanged():
    """This fixes WHO gets penalised, not how hard — retuning the constant on one
    page would be overfitting to BAT_664."""
    assert SCRIPT_MISMATCH == -0.35


# ── the live ranking ─────────────────────────────────────────────────────────

def test_the_right_model_outranks_the_japanese_one_for_a_kursive_source():
    """The two models from the live run, scored against the criteria as they were
    actually derived. Before the fix: 0.15 vs 0.80."""
    catmus = score_model(_model("Kurrent", lang="de", model_id="zenodo.15030337"),
                         script=KURSIVE.script, lang=KURSIVE.lang,
                         century=KURSIVE.century)
    med_15_16 = score_model(
        _model("Humanistische Kursive", lang=None, model_id="zenodo.13942714"),
        script=KURSIVE.script, lang=KURSIVE.lang, century=KURSIVE.century)

    assert catmus.score >= med_15_16.score, (
        f"catmus {catmus.score} must not rank below medieval_15_16 "
        f"{med_15_16.score} — the latter reads this page as Japanese")
