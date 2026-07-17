"""The degeneracy detector must not flag text for being LONG.

#274's second heuristic was `distinct_chars / total_chars < 0.10`. An alphabet is
bounded (~40-50 chars incl. diacritics and punctuation) while the denominator grows
with the text — so the ratio measures length, not degeneration, and ANY
natural-language transcription over ~500 chars fell below 0.10.

Measured on tei 2026-07-17, on the first genuinely good reading the pipeline has
produced:

    unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint zwùschent
    654 chars, 47 distinct → ratio 0.072 → "degenerate", quality 0.1

Agent B then took the image-only path (#301) and the model selector lost the
Schrift/Datierung it needed — the exact starvation chain of #298, triggered by GOOD
output. The better the transcription, the more certainly it was rejected.

Offline, pure functions. Run from the repo root:
    pytest agentic_historian/tests/test_ah_degeneracy_length_bug.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents.text_recognition import _is_degenerate, _quality_score  # noqa: E402

# Verbatim from the tei run of 2026-07-17 (trocr-medieval-escriptmask).
REAL_GOOD_READING = (
    "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint zwùschent\n"
    "Henin Rost und Cuͦnratē nefen darumb nū der selbē vast im digk und vil fùrgnomē\n"
    "hat abes nant me usgetrachē möcht werde das im hert angelegē ist nùn han\n"
    "wir darumb vil geschribē die sach uff ze schlachē dz ouch ist geschē und wil si als"
)


def test_the_real_good_reading_is_not_degenerate():
    """The regression, verbatim from production."""
    assert _is_degenerate(REAL_GOOD_READING) is False
    assert _quality_score(REAL_GOOD_READING) > 0.5


def test_degeneracy_does_not_depend_on_length():
    """The defect, stated directly: a full alphabet and a varied vocabulary must
    not become "degenerate" merely by getting longer.

    The vocabulary is distinct pseudo-words rather than one repeated sentence: a
    sentence repeated 100x genuinely does trip the distinct-WORD heuristic, which
    is a different (and defensible) signal. The claim under test is about the
    alphabet rule.
    """
    vocab = [f"{a}{b}{c}" for a in "abcdefgh" for b in "aeiou" for c in "lmnrst"]  # 240
    for reps in (1, 2, 4):                       # ~960 → ~3800 chars
        text = " ".join(vocab * reps)
        assert _is_degenerate(text) is False, f"flagged at {len(text)} chars"


def test_a_long_collapse_is_still_caught():
    """Length must not make a collapse invisible either."""
    assert _is_degenerate("uuuu " * 200) is True


def test_a_short_collapse_is_still_caught():
    assert _is_degenerate("uuuu uuuu uuuu\nuuuuuuuuuuuu\niuuuuuie uuuu") is True


def test_a_tiny_alphabet_is_degenerate_at_any_length():
    """What the heuristic was actually reaching for: 3 distinct characters is
    degenerate whether the page is 40 chars or 4000."""
    assert _is_degenerate("abab " * 300) is True


def test_a_normal_alphabet_is_not_degenerate_even_when_repetitive():
    """Formulaic charter prose repeats a lot — that is not a collapse."""
    text = ("und dz ouch ist geschen und wil si als der selbe Cuonrat begert hant "
            "und dz ouch ist geschen und wil si als der selbe Cuonrat begert hant ")
    assert _is_degenerate(text) is False


def test_empty_and_whitespace_are_not_degenerate():
    assert _is_degenerate("") is False
    assert _is_degenerate("   \n  ") is False
