"""#379: a description declares a ranked set of hands, not one fact.

Three runs of `saa-0428` over identical images called the hand Kursive, then
Fraktur, then Textura — three script families, three unrelated winning model
pools, QA flat at 0.36/0.41/0.40. #380 removed the run-to-run variance (a
deterministic describe call, a cache keyed on the image bytes, Gate-1 pins), so
two runs now produce the same criteria.

What stayed is the part the cache cannot fix: **one reading was taken as a fact
and the rest were actively demoted.** Agent B described the hand as
*"Kursivschrift (Fraktur), schwarze Tinte"* — naming two hands in one breath —
and `normalise_script` returned whichever alias it met first in a dict. Every
Fraktur model then took `SCRIPT_MISMATCH`: not a missing reward but a demotion
below a script-agnostic pick, for a hand the describer had just named.

That is exactly the defect `normalise_langs` was written for in the neighbouring
field (#375): "the longest matching alias wins" collapsed *"Deutsch und Latein"*
to German and scored every Latin model as a mismatch. The type was wrong there
too.

Measured on the issue's own descriptions, with `kraken-fraktur-19` against
`"Kursivschrift (Fraktur)"` at century 16:

    before   +0.15   (0.3 lang + 0.2 century − 0.35 script mismatch)
    after    +0.80   (0.3 lang + 0.2 century + 0.3 script secondary)

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_379_declared_scripts.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest  # noqa: E402

from agent_a.model_selector import (  # noqa: E402
    SCRIPT_EXACT,
    SCRIPT_MISMATCH,
    SCRIPT_SECONDARY,
    normalise_script,
    normalise_scripts,
    score_model,
)
from agent_a.models import KrakenModel  # noqa: E402


#: The three descriptions the issue recorded, verbatim.
RUN1 = "Kursivschrift (Fraktur), schwarze Tinte"
RUN2 = "fraktur"
RUN3 = "Gothische Textura mit Rubrizierung"


def model(script, *, model_id="m", lang="de", centuries=(15, 16)):
    return KrakenModel(model_id=model_id, name=model_id, script=script,
                       lang=lang, centuries=list(centuries))


def scored(script, model_script, *, lang="de", century=16):
    return score_model(model(model_script), script=script, lang=lang,
                       century=century)


# ── what a description declares ─────────────────────────────────────────────

def test_two_hands_in_one_breath_declare_both_in_order():
    """The issue's run 1. `kursive` led and `fraktur` was dropped entirely."""
    assert normalise_scripts(RUN1) == ["kursive", "fraktur"]


def test_the_other_two_runs_declare_one_hand_each():
    assert normalise_scripts(RUN2) == ["fraktur"]
    assert normalise_scripts(RUN3) == ["textura"]


def test_a_mixed_book_hand_declares_both():
    assert normalise_scripts("Textura und Rotunda gemischt") == ["textura",
                                                                 "rotunda"]


def test_a_longer_alias_is_not_also_read_as_the_shorter_one_inside_it():
    """Resolved by position and length, as in `normalise_langs`. Otherwise
    "halbkursive" would declare the cursive family twice over."""
    assert normalise_scripts("Halbkursive") == ["halbkursive"]
    assert normalise_scripts("halbkursive, sehr flüchtig") == ["halbkursive"]


def test_an_exact_alias_is_taken_whole():
    assert normalise_scripts("fraktur") == ["fraktur"]
    assert normalise_scripts("blackletter") == ["fraktur"]


def test_an_unrecognised_script_is_still_declared():
    """A hand nobody has an alias for must still match a model tagged the same
    way, which is what the old function did by returning the raw string."""
    assert normalise_scripts("Zierschrift") == ["zierschrift"]


def test_nothing_declared_is_an_empty_list():
    assert normalise_scripts("") == []
    assert normalise_scripts("   ") == []
    assert normalise_scripts(None) == []


# ── the single-string field still works ────────────────────────────────────

def test_normalise_script_returns_the_leading_reading():
    """The run state, the Gate-1 card and the routing prior each carry one
    script string, and that string is the leading reading. The set lives in
    `normalise_scripts`, as `normalise_langs` sits behind the single `lang`
    field."""
    assert normalise_script(RUN1) == "kursive"
    assert normalise_script(RUN3) == "textura"
    assert normalise_script("blackletter") == "fraktur"
    assert normalise_script("") == ""


# ── the declared alternative is no longer demoted ─────────────────────────

def test_the_named_second_hand_is_in_contention_not_demoted():
    """The whole issue in one assertion. `kraken-fraktur-19` against run 1's
    description used to be pushed below a script-agnostic pick."""
    result = scored(RUN1, "Fraktur")

    assert "script2" in result.matched_on
    assert "script-mismatch" not in result.matched_on
    assert result.score == pytest.approx(0.3 + 0.2 + SCRIPT_SECONDARY)


def test_the_leading_hand_still_wins():
    """"Eligible" and "equal" are not the same thing. An alternative keeps a
    model in contention without letting it outrank the leading reading — the
    distinction #375 drew for a secondary language.

    Scored without a century match, because `score_model` clamps to [0, 1] and
    script-exact + lang + century already saturates: the full gap is only
    visible below the ceiling.
    """
    lead = scored(RUN1, "Kursive", century=None)
    second = scored(RUN1, "Fraktur", century=None)

    assert lead.score > second.score
    assert lead.score - second.score == pytest.approx(SCRIPT_EXACT
                                                      - SCRIPT_SECONDARY)


def test_the_leading_hand_also_wins_at_the_ceiling():
    """With the century matching too, the lead saturates at 1.0 and the
    alternative sits below it. The ordering is what matters; the arithmetic is
    checked above."""
    lead = scored(RUN1, "Kursive")
    second = scored(RUN1, "Fraktur")

    assert lead.score == pytest.approx(1.0)
    assert second.score < lead.score


def test_a_hand_nobody_named_is_still_a_mismatch():
    """The guard: declaring a set must not make everything eligible. Textura is
    a gothic book hand and a cursive model does produce garbage on it."""
    result = scored(RUN1, "Textura")

    assert "script-mismatch" in result.matched_on
    assert result.score == pytest.approx(0.3 + 0.2 + SCRIPT_MISMATCH)


def test_the_demotion_is_what_changed_not_the_ranking():
    """Both models stay ranked as before relative to each other; what changed is
    that the second is no longer pushed below a model with no script at all."""
    second = scored(RUN1, "Fraktur")
    agnostic = score_model(model(None), script=RUN1, lang="de", century=16)

    assert second.score > agnostic.score


# ── the fuzzy path sees every declared hand, not only the leading one ─────

def test_a_family_relation_to_a_secondary_hand_counts():
    """`scripts_related` used to be asked about the leading reading only, so a
    Kurrent model against "Fraktur, teils kursiv" was a mismatch although the
    description named a hand of its own family."""
    result = scored("Fraktur, teils kursiv", "Kurrent")

    assert "script-mismatch" not in result.matched_on
    assert result.score > 0


def test_a_substring_relation_to_a_secondary_hand_counts():
    result = scored("Textura und Rotunda gemischt", "Rotunda")

    assert "script2" in result.matched_on


# ── one hand named: nothing changes ──────────────────────────────────────

@pytest.mark.parametrize("desc,model_script,expect", [
    (RUN3, "Textura", "script"),
    (RUN3, "Kursive", "script-mismatch"),
    (RUN2, "Fraktur", "script"),
    (RUN2, "Textura", "script-mismatch"),
])
def test_a_single_hand_scores_exactly_as_before(desc, model_script, expect):
    """The common case must be untouched: most descriptions name one hand, and
    this change is only about the ones that name more."""
    assert expect in scored(desc, model_script).matched_on


def test_the_cursive_family_still_takes_fuzzy_not_exact():
    """#358's relation is unchanged: kraken-medieval_15_16 tagged "Humanistische
    Kursive" against a Kurrent page is related, not identical."""
    result = scored("Kurrent", "Humanistische Kursive")

    assert "script~" in result.matched_on


def test_no_script_declared_leaves_scoring_alone():
    """A description with no script must not start penalising models."""
    result = score_model(model("Textura"), script="", lang="de", century=16)

    assert not any(m.startswith("script") for m in result.matched_on)
    assert result.score == pytest.approx(0.3 + 0.2)


# ── the three runs, held against each other ─────────────────────────────

def test_the_three_runs_no_longer_choose_three_unrelated_families():
    """The issue's table. The run-to-run variance itself is #380's (the cache
    makes the description a property of the image), but even across three
    different descriptions the Fraktur model is no longer actively demoted by
    the one that named it.
    """
    fraktur = {desc: scored(desc, "Fraktur").matched_on for desc in
               (RUN1, RUN2, RUN3)}

    assert "script2" in fraktur[RUN1]          # named second — in contention
    assert "script" in fraktur[RUN2]           # named alone — exact
    assert "script-mismatch" in fraktur[RUN3]  # genuinely a different hand


def test_the_declared_set_is_recoverable_from_the_stored_string():
    """What #326 needs without a schema change. The criteria keep one `script`
    string, and the set it declares can be derived from it whenever a bucket
    wants to key on more than the leading reading."""
    stored = RUN1                               # what criteria["script"] holds

    assert normalise_scripts(stored) == ["kursive", "fraktur"]
    assert normalise_script(stored) == "kursive"
