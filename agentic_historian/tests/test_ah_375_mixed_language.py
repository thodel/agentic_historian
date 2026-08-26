"""#375: a bilingual source must keep both languages' models eligible.

Agent B described saa-0428 (a Königsfelden cartulary, 6 pages from e-codices)
accurately as *"Deutsch und Latein, gemischt"* — German front matter, Latin
charters. `normalise_lang` collapsed that to "de" because the longest matching
alias wins, and the Latin models were then scored as a language mismatch on every
page: a Latin charter was read with a German Kurrent model while
`trocr-essoins-middle-latin` sat unused in the pool.

A better tie-break would not have helped — preferring Latin would just invert the
error. The type was wrong: a source language is a set.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_375_mixed_language.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a.model_selector import (  # noqa: E402
    KrakenModel, SourceCriteria, normalise_lang, normalise_langs, score_model,
)

# verbatim from the live run
SAA_SPRACHE = "Deutsch und Latein, gemischt"


# ── the parser keeps both ────────────────────────────────────────────────────

def test_the_live_description_yields_both_languages():
    assert normalise_langs(SAA_SPRACHE) == ["de", "la"]


def test_word_order_is_preserved_not_normalised_away():
    """Which language leads is information — it should not be decided by which
    alias happens to be longer."""
    assert normalise_langs("Latein und Deutsch, gemischt") == ["la", "de"]
    assert normalise_langs("Deutsch und Latein, gemischt") == ["de", "la"]


def test_a_compound_is_not_double_counted():
    """"mittelhochdeutsch" contains "deutsch"; it must yield de once, not twice."""
    assert normalise_langs("Mittelhochdeutsch") == ["de"]


def test_a_prose_description_finds_both():
    assert set(normalise_langs("lateinisch mit deutschen Einschüben")) == {"la", "de"}


def test_a_single_language_still_yields_one():
    assert normalise_langs("Deutsch") == ["de"]


def test_an_unknown_language_yields_nothing():
    """#348's principle holds: no answer beats a plausible wrong one."""
    assert normalise_langs("Klingonisch") == []
    assert normalise_langs("") == []


def test_normalise_lang_still_returns_the_primary():
    """The single-value API is unchanged for every existing caller."""
    assert normalise_lang(SAA_SPRACHE) == "de"
    assert normalise_lang("Mittelhochdeutsch") == "de"


# ── criteria carry the set ───────────────────────────────────────────────────

def _saa_criteria():
    return SourceCriteria.from_source_json({
        "Schrift":   {"wert": "Kursivschrift (Fraktur)"},
        "Sprache":   {"wert": SAA_SPRACHE},
        "Datierung": {"wert": "Anfang 16. Jahrhundert (ca. 1500-1520)"},
    })


def test_source_criteria_expose_every_declared_language():
    c = _saa_criteria()
    assert c.langs == ["de", "la"]
    assert c.lang == "de"                       # primary, for existing callers


def test_criteria_from_prose_also_carry_the_set():
    c = SourceCriteria.from_agent_b("Die Handschrift ist deutsch und lateinisch.")
    assert set(c.langs) == {"de", "la"}


# ── the scoring consequence ──────────────────────────────────────────────────

def _model(lang, script="Kurrent", centuries=(15, 16), model_id="m"):
    return KrakenModel(model_id=model_id, name=model_id, script=script,
                       lang=lang, centuries=list(centuries))


def test_the_second_language_is_no_longer_a_mismatch():
    """The live defect: with lang="de" alone, a Latin model scored no language
    credit on a source that declares Latin."""
    m = score_model(_model("la"), script="kursive", lang=["de", "la"], century=16)
    assert "lang" in m.matched_on


def test_the_primary_language_still_matches():
    m = score_model(_model("de"), script="kursive", lang=["de", "la"], century=16)
    assert "lang" in m.matched_on


def test_a_language_the_source_never_declares_gets_no_credit():
    """The set must not become a wildcard — that would make every model eligible."""
    m = score_model(_model("ar"), script="kursive", lang=["de", "la"], century=16)
    assert "lang" not in m.matched_on and "lang~" not in m.matched_on


def test_a_single_string_is_still_accepted():
    """Back-compat: most callers pass one code, and some pass raw prose."""
    assert "lang" in score_model(_model("de"), lang="de").matched_on
    assert "lang" in score_model(_model("la"), lang="Latein").matched_on


def test_no_declared_language_scores_as_before():
    m = score_model(_model("de"), script="kursive", lang=None, century=16)
    assert "lang" not in m.matched_on


# ── what the fix does NOT protect against ────────────────────────────────────

def test_a_wrong_script_candidate_is_still_caught_for_either_language():
    """Making Latin models eligible also promotes `medieval_15_16` (tagged la +
    Humanistische Kursive) to a full metadata match — and its output for this
    manuscript is Japanese. Metadata cannot see that; #359's guard must, for BOTH
    declared languages."""
    from agent_a.ensemble import script_implausible
    cjk = "し 岡鳥コココ●き七両日四日モ あヨもさのとき一男呂日百コと夫"
    assert script_implausible(cjk, "de") is True
    assert script_implausible(cjk, "la") is True
    # and a real Latin reading is untouched
    assert script_implausible("modios trititi cum prato sito iuxta", "la") is False
