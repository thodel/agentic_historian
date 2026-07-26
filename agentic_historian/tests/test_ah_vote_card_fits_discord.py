"""render_vote_card must fit Discord's 2000-char limit (found via #313 live prep).

With the ensemble's engine set the vote card ran to 8392 chars live — 4× the
limit — so /votes would have failed with "message too long" the moment it
appeared. The card now scales each snippet and keeps header + max-CER + tally
whole, trimming only the candidate blocks.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_vote_card_fits_discord.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import path_compare  # noqa: E402
from runstate import RunState  # noqa: E402

LONG = ("unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint "
        "zwùschent Henin Rost und Cuͦnratē nefen darumb nū der selbē vast im digk " * 3)


def _paths(n):
    return {f"BAT_664_r_00027.jpg:eng{i}/model-{i}-with-a-longish-name": LONG
            for i in range(n)}


def test_card_fits_discord_at_the_realistic_seven_candidates():
    card = path_compare.render_vote_card(RunState(doc_id="d"), _paths(7))
    assert len(card) <= 2000                        # the regression (was 8392)


def test_card_fits_at_many_candidates():
    for n in (2, 5, 10, 20):
        card = path_compare.render_vote_card(RunState(doc_id="d"), _paths(n))
        assert len(card) <= 2000, f"{n} candidates → {len(card)} chars"


def test_the_selection_footer_is_never_trimmed_away():
    """The selection footer is the interactive state — trimming must hit the
    readings, not it (#313 multi-select)."""
    card = path_compare.render_vote_card(RunState(doc_id="d"), _paths(12))
    assert "ausgewählt" in card.lower()             # the footer survives


def test_the_readings_are_still_shown():
    card = path_compare.render_vote_card(RunState(doc_id="d"), _paths(7))
    assert "gruͦs" in card                           # candidates still judgeable
    assert "max. paarweise CER" in card             # the one CER line, not the matrix


def test_a_small_card_is_not_padded_or_broken():
    card = path_compare.render_vote_card(
        RunState(doc_id="d"),
        {"p:trocr/a": "kurze lesart eins", "p:trocr/b": "kurze lesart zwei"})
    assert "kurze lesart eins" in card and "kurze lesart zwei" in card
    assert len(card) <= 2000
