"""#348: a language description must never become a wrong-but-valid ISO code.

Agent B described a 15th c. Swiss manuscript as "Mittelhochdeutsch"; the old
`s[:2]` fallback turned that into "mi" — Māori. The failure mode is what makes it
worth a test file: a 2-letter code LOOKS valid, so nothing flags it, while it
splits the preference bucket #335 keys on (script, century, lang).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_348_lang_normalisation.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.model_selector import (  # noqa: E402
    LANG_ALIASES, SourceCriteria, normalise_lang,
)


# ── the reported bug ─────────────────────────────────────────────────────────

def test_mittelhochdeutsch_is_german_not_maori():
    """The exact value observed on tei."""
    assert normalise_lang("Mittelhochdeutsch") == "de"
    assert normalise_lang("Mittelhochdeutsch") != "mi"


@pytest.mark.parametrize("raw", [
    "mittelhochdeutsch", "Mittelniederdeutsch", "Althochdeutsch",
    "Frühneuhochdeutsch", "fruehneuhochdeutsch", "Early New High German",
    "Neuhochdeutsch", "Alemannisch", "Schweizerdeutsch", "Oberdeutsch",
    "Niederdeutsch", "Deutsch", "German",
])
def test_every_stage_of_german_normalises_to_de(raw):
    assert normalise_lang(raw) == "de"


@pytest.mark.parametrize("raw", [
    "Mittellatein", "mittellateinisch", "Medieval Latin", "Neulatein",
    "Kirchenlatein", "Latein", "Latin",
])
def test_every_stage_of_latin_normalises_to_la(raw):
    """The removed `"mittel": "de"` catch-all sent Mittellatein to German while its
    own comment claimed Latin."""
    assert normalise_lang(raw) == "la"


def test_a_compound_is_decided_by_its_most_specific_part():
    """Longest alias first: "mittellatein" must resolve via "latein", never via a
    shorter accidental match."""
    assert normalise_lang("mittellateinisch") == "la"
    assert normalise_lang("mittelniederdeutsch") == "de"


# ── the principle: no guess is better than a plausible wrong guess ───────────

def test_an_unrecognised_language_yields_empty_not_a_truncation():
    """`s[:2]` manufactured a well-formed code from anything. An empty value is
    visibly unknown; "kl" would look like a real answer and silently split a
    bucket."""
    assert normalise_lang("Klingonisch") == ""
    assert normalise_lang("Quenya") == ""


def test_empty_and_whitespace_are_empty():
    assert normalise_lang("") == ""
    assert normalise_lang("   ") == ""
    assert normalise_lang(None) == ""


def test_an_already_normalised_code_is_preserved():
    """from_source_json may be handed a code that has been through this once."""
    for code in ("de", "la", "fr", "el"):
        assert normalise_lang(code) == code


def test_no_alias_maps_to_the_maori_code():
    """Guards the shape of the bug rather than the one input that exposed it."""
    assert "mi" not in set(LANG_ALIASES.values())


# ── the path the bug actually travelled ──────────────────────────────────────

def test_source_json_criteria_get_the_right_language():
    """from_source_json → normalise_lang is how "mi" reached the RunState."""
    crit = SourceCriteria.from_source_json(
        {"Schrift": {"wert": "Kurrent"},
         "Sprache": {"wert": "Mittelhochdeutsch"},
         "Datierung": {"wert": "15. Jahrhundert"}})
    assert crit.lang == "de"


def test_an_unknown_source_language_leaves_lang_unset_rather_than_wrong():
    crit = SourceCriteria.from_source_json({"Sprache": {"wert": "Klingonisch"}})
    assert not crit.lang
