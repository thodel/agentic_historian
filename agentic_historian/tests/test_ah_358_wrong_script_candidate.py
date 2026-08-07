"""#358: a candidate written in the wrong script must never be the automatic pick.

Live on tei 2026-08-07, `/run BAT_664_r_00027.jpg` (criteria script=kursive,
century=15, lang=de, max pairwise CER 253.7%) selected:

    kraken/kraken-medieval_15_16   283 chars   引口三へにみへきす国へ是引内すま…

Japanese, and the shortest candidate by half, chosen over two readable German
transcriptions at ranks 2 and 3. Ranking looks only at the model's METADATA match
and never at what the model produced; `kraken-medieval_15_16` won on a century
match against its own filename.

This is not the quality judgement #313 says we cannot make without ground truth.
Ranking two German readings by correctness needs a reference; observing that a CJK
string is not a reading of a Latin-script manuscript does not.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_358_wrong_script_candidate.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agent_a import ensemble  # noqa: E402
from agent_a.model_selector import SourceCriteria  # noqa: E402

# verbatim from the live run
CJK = "引口三へにみへきす国へ是引内すま是つかみ切もなへみもふのかひ也にて「の黒てて"
HEBREW = "וש ימי ולו הים לש יולגי סל וסורן לירחף כ לל א  ילו  רקז ו א"
GOOD = "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint"
POOR = "duser feunilite grus vor liebe gerrmreuon de scosse roepse di fuitsriousthont"
ABBREV = "ðiser Pr mimítt͛. qrí͛g bøn͛ locb.iꝼetrinr.. Evan dec scrsl."


# ── the detector ─────────────────────────────────────────────────────────────

def test_the_japanese_reading_is_implausible_for_german():
    assert ensemble.script_implausible(CJK, "de") is True


def test_the_hebrew_reading_is_implausible_for_german():
    """kraken-catmus_caroline in the same run."""
    assert ensemble.script_implausible(HEBREW, "de") is True


@pytest.mark.parametrize("text", [GOOD, POOR, ABBREV])
def test_latin_readings_are_plausible_however_bad(text):
    """POOR and ABBREV are near-garbage, but they are garbage in the RIGHT script.
    Judging those needs ground truth (#313/#326) — this guard must not touch them."""
    assert ensemble.script_implausible(text, "de") is False


def test_a_hebrew_reading_is_plausible_for_a_hebrew_source():
    assert ensemble.script_implausible(HEBREW, "he") is False


def test_a_greek_reading_is_plausible_for_a_greek_source():
    assert ensemble.script_implausible("μηνος γαρ τουτο εστιν", "el") is False


# ── conservative by construction ─────────────────────────────────────────────

def test_an_unknown_language_never_rejects():
    """Pass 1 runs blind — no criteria — and must not start discarding candidates."""
    assert ensemble.script_implausible(CJK, None) is False
    assert ensemble.script_implausible(CJK, "") is False


def test_empty_text_is_not_rejected():
    assert ensemble.script_implausible("", "de") is False
    assert ensemble.script_implausible("   ", "de") is False


def test_digits_and_punctuation_only_is_not_rejected():
    assert ensemble.script_implausible("1503 — §§ 12, 14.", "de") is False


def test_mostly_latin_with_some_greek_is_kept():
    """A German charter quoting Greek is still a German reading."""
    mixed = GOOD + " (μηνος)"
    assert ensemble.script_implausible(mixed, "de") is False


# ── the ranking consequence ──────────────────────────────────────────────────

def _cand(engine, model_id, text, score):
    rec = SimpleNamespace(engine=engine, model_id=model_id, text=text,
                          error="", confidence=0.5)
    pick = SimpleNamespace(engine=engine, model_id=model_id, score=score)
    return rec, pick


def _rank(cands, criteria):
    recs = [c[0] for c in cands]
    ran = [c[1] for c in cands]
    return ensemble.rank_candidates(recs, ran, criteria)


DE = SourceCriteria(script="kursive", century=15, lang="de")


def test_the_wrong_script_candidate_loses_despite_the_best_match_score():
    """The live shape: the CJK model scored highest on metadata."""
    ranked = _rank([
        _cand("kraken", "kraken-medieval_15_16", CJK, 0.90),      # best score
        _cand("trocr", "trocr-medieval-escriptmask", GOOD, 0.20),
    ], DE)
    assert ranked[0][1].model_id == "trocr-medieval-escriptmask"


def test_select_best_does_not_return_the_wrong_script_candidate():
    recs = [SimpleNamespace(engine="kraken", model_id="kraken-medieval_15_16",
                            text=CJK, error="", confidence=0.9),
            SimpleNamespace(engine="trocr", model_id="trocr-medieval-escriptmask",
                            text=GOOD, error="", confidence=0.1)]
    ran = [SimpleNamespace(engine="kraken", model_id="kraken-medieval_15_16", score=0.9),
           SimpleNamespace(engine="trocr", model_id="trocr-medieval-escriptmask", score=0.2)]
    rec, pick = ensemble.select_best(recs, ran, DE)
    assert pick.model_id == "trocr-medieval-escriptmask"


def test_the_wrong_script_candidate_is_still_listed_as_evidence():
    """A misconfigured model is a finding. The Gate-2 card should show what each
    engine did — the candidate is demoted, never hidden."""
    ranked = _rank([
        _cand("kraken", "kraken-medieval_15_16", CJK, 0.90),
        _cand("trocr", "trocr-medieval-escriptmask", GOOD, 0.20),
    ], DE)
    assert len(ranked) == 2
    assert ranked[-1][1].model_id == "kraken-medieval_15_16"


def test_plausible_candidates_keep_their_score_order():
    """Within the plausible group nothing changes — this guard adds a floor, it
    does not re-rank readings against each other."""
    ranked = _rank([
        _cand("trocr", "low", POOR, 0.20),
        _cand("trocr", "high", GOOD, 0.80),
    ], DE)
    assert [p.model_id for _r, p in ranked] == ["high", "low"]


def test_all_candidates_wrong_script_still_returns_one():
    """Selecting nothing would drop the page. A bad pick the historian can override
    beats no transcription at all."""
    ranked = _rank([
        _cand("kraken", "a", CJK, 0.5),
        _cand("kraken", "b", HEBREW, 0.4),
    ], DE)
    assert len(ranked) == 2


def test_a_blind_pass_ranks_exactly_as_before():
    """criteria=None → no language → the guard is inert, so pass 1 is unchanged."""
    ranked = _rank([
        _cand("kraken", "cjk", CJK, 0.90),
        _cand("trocr", "good", GOOD, 0.20),
    ], None)
    assert ranked[0][1].model_id == "cjk"        # score order, untouched
