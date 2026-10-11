"""A run of eighty underscores is a rule, not a loop (#623).

The third page of this corpus to be accused of being filler for reproducing its
own layout. The first was a right-aligned signature indented with 34 spaces
(#606, fixed by excluding whitespace from the run). This one is a Deutsches
Literaturarchiv Marbach insert slip — correctly transcribed by
`lassberg_atr_strict2.md` and correctly marked as a foreign hand — whose printed
separator line came out as eighty underscores:

    [fremde hand: Diesen Zettel bitte nicht entfernen!   deutsches
                                                         literatur
                                                         archiv marbach
    ______________________________________________________________
    Bestandssignatur B: Laßberg, Joseph von …]

Excluding `_` by name would have been the third patch of the same shape, and the
next form will rule its lines with dots or dashes. What tells a rule from filler
is not the character: it is that a rule is a line of its own and a small part of
the page, while filler is extruded into the text or swamps it.

The verdict is not cosmetic. The report says of a flagged page that "every
average over them is wrong", which about a page the model read correctly is a
false accusation that outlives the run.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import page_quality as pq                   # noqa: E402

#: The Marbach slip as `gemini-300-strict2` read it, shortened in the middle.
SLIP = ("[fremde hand: Diesen Zettel bitte nicht entfernen!          deutsches\n"
        "                                                            literatur\n"
        "                                                        archiv marbach\n"
        + "_" * 80 + "\n"
        "Bestandssignatur B: Laßberg, Joseph von\n"
        "Ordnungsgattung Briefe an\n"
        "       Kurztitel Pfeiffer, Franz an Laßberg, Joseph von\n"
        "Bemerk. Haupttitel\n"
        "  Entstehungszeit 1840-1850\n"
        "          Umfang 21 e. Br.  40 Bl. 8°, 4°\n"
        "   Zugangsnummer .11497-11517\n"
        "        Standort                              *HS00848999*\n"
        "        Nummern Mappe:             Kasten:\n"
        "    Bei / Auf / In\n"
        "      Bildobjekt\n"
        "      Bibliothek\n"
        "                      Einlegezettel-HS-v03-main-DA2024-]")

#: The 8 000 zeros of 2026-10-01, the case the run test exists for.
LOOP_IN_LINE = "10 bis 12" + "0" * 3000
LOOP_OWN_LINE = "Euer Hochwohlgeboren, beiliegend die Urkunde.\n" + "0" * 8000
BURST_IN_PROSE = ("Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag "
                  "ertheilt, mich für ein Exemplar umzusehen." + "0" * 20)


def test_the_printed_rule_is_not_a_loop():
    q = pq.classify(SLIP)

    assert q.verdict == "ok"
    assert q.longest_char_run == 80, "the run is seen"
    assert q.run_on_own_line and q.run_share < pq.MAX_RUN_SHARE


@pytest.mark.parametrize("text,why", [
    (LOOP_IN_LINE, "extruded into a line of text"),
    (LOOP_OWN_LINE, "its own line, but 99 % of the page"),
    (BURST_IN_PROSE, "short, and inside a sentence"),
])
def test_what_the_exemption_must_not_spare(text, why):
    assert pq.classify(text).verdict == "repetitive", why


def test_the_two_halves_are_both_needed():
    """Either one alone lets a measured case through, which is why there are
    two rather than a tidier one."""
    own_line_only = pq.classify(LOOP_OWN_LINE)
    assert own_line_only.run_on_own_line, "share is what catches this one"

    small_share_only = pq.classify(BURST_IN_PROSE)
    assert small_share_only.run_share < pq.MAX_RUN_SHARE, \
        "being inside a line is what catches this one"


def test_the_measures_are_stored_so_the_rule_stays_arithmetic():
    q = pq.classify(SLIP)

    assert q.as_dict()["run_share"] == q.run_share
    assert q.as_dict()["run_on_own_line"] is True
    # Raise the share bar past this page and the exemption goes with it, from
    # the stored measures alone — no corpus is read again to change a rule.
    assert pq.looks_repetitive(q, max_run_share=0.0)


@pytest.mark.parametrize("ch", list("-.=·*"))
def test_a_rule_of_any_character_is_spared(ch):
    """The point of measuring position rather than listing characters: the next
    form will not use underscores."""
    page = (SLIP.replace("_" * 80, ch * 60))

    assert pq.classify(page).verdict == "ok", ch


def test_on_a_short_page_a_rule_does_dominate_and_is_flagged():
    """A stated decision, not an oversight. The exemption is a share, so on a
    page that is three lines and a rule the rule *is* most of the page — and a
    page that is 62 % one character is worth a look whatever drew it. The real
    slip is 822 characters with its rule at 10 %."""
    stub = "Bestandssignatur B: Laßberg\n" + "_" * 80 + "\nBriefe an"
    q = pq.classify(stub)

    assert q.run_on_own_line
    assert q.run_share > pq.MAX_RUN_SHARE
    assert q.verdict == "repetitive"
