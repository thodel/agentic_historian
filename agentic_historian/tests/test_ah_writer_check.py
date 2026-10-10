"""Is a machine reading enough to tell whose hand a page is in? (`writer_check`)

The question bites its own tail. "300 pages in Laßberg's own hand, none of them
transcribed yet" asks for the one thing an untranscribed page cannot supply: the
hand is inferred from the dateline, the dateline is in the transcription.

Provenance does not rescue it — Basel holds both sides of the correspondence in
one folder (`doc7151991`, Wackernagel, 8.1 % CER, against `doc4726780`,
Laßberg, 34.8 %, same directory). What is left is to run the dateline rule over a
*machine* reading, which most of the corpus already has, and that is a guess
about a guess. So it gets measured first, on the pages that have both.

**The trap this module exists to avoid.** A ground-truth page whose dateline the
rule cannot read is `unbestimmt` — an absence of evidence, not a third hand.
Scoring a machine reading against it would measure the machine on a non-answer:
counted as wrong it punishes the machine for our blindness, counted as right when
both say `unbestimmt` it inflates agreement with pages nobody knows anything
about. Precision and recall therefore count only pages whose ground truth names a
hand, and the rest are reported as the size of what cannot be seen.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import writer_check as wc                     # noqa: E402

LASSBERG = "Eppishausen am 21 Januar 1831.\nHochgeschäzter Herr!"
WACKERNAGEL = "Basel 30 Augst 33.\nHochgeehrter Herr Baron,"
MUTE = "Hochverehrter Herr Baron,\nmit vielem Dank sende ich"


class _GT:
    def __init__(self, text):
        self.text = text
        self.source = Path("gt.xml")


class _Scored:
    def __init__(self, text, located):
        self.gt = _GT(text)
        self.located = located


def _run(pairs):
    """`(confusion, )` for pages given as `(key, gt text, machine text)`."""
    scored = [_Scored(gt, key) for key, gt, _ in pairs]
    readings = {"m": {key: machine for key, _, machine in pairs}}
    return wc.compare(scored, readings)[0]


# ── the comparison ───────────────────────────────────────────────────────────

def test_a_machine_reading_that_keeps_the_dateline_agrees():
    c = _run([("p1", LASSBERG, "Eppishausen am 21 Januar 1831.\nHochgeschäzter")])

    assert c.cells[("lassberg", "lassberg")] == 1
    assert c.agreement == 1.0
    assert c.precision("lassberg") == 1.0


def test_a_machine_reading_that_loses_the_dateline_is_a_miss():
    """What the rule sees is `unbestimmt`, and recall pays for it."""
    c = _run([("p1", LASSBERG, "Eppisbaufen am 21 Jnnuar\nHochgeschäzter")])

    assert c.recall("lassberg") == 0.0
    assert c.precision("lassberg") == 0.0


def test_a_machine_reading_that_invents_the_wrong_place_costs_precision():
    """The expensive error: a page selected *because* the machine called it his."""
    c = _run([("p1", WACKERNAGEL, "Eppishausen 30 Augst 33.\nHochgeehrter")])

    assert c.precision("lassberg") == 0.0
    assert c.cells[("korrespondenten", "lassberg")] == 1


def test_precision_is_the_share_of_picks_the_truth_agrees_with():
    c = _run([
        ("p1", LASSBERG, LASSBERG),            # picked, right
        ("p2", LASSBERG, LASSBERG),            # picked, right
        ("p3", WACKERNAGEL, LASSBERG),         # picked, wrong
        ("p4", WACKERNAGEL, WACKERNAGEL),      # not picked
    ])

    assert c.precision("lassberg") == pytest.approx(2 / 3)
    assert c.recall("lassberg") == 1.0
    assert c.picked("lassberg") == 3


# ── the trap ─────────────────────────────────────────────────────────────────

def test_a_page_with_no_ground_truth_hand_is_not_scored():
    """`unbestimmt` ground truth is an absence of evidence. Counting it as an
    error would measure the machine on a non-answer."""
    c = _run([
        ("p1", LASSBERG, LASSBERG),
        ("p2", MUTE, LASSBERG),        # truth says nothing — not a wrong answer
    ])

    assert c.known == 1
    assert c.undecided == 1
    assert c.precision("lassberg") == 1.0


def test_agreeing_on_unbestimmt_does_not_inflate_agreement():
    """Both saying "I don't know" about the same page is not a hit."""
    c = _run([
        ("p1", LASSBERG, LASSBERG),
        ("p2", MUTE, MUTE),
        ("p3", MUTE, MUTE),
    ])

    assert c.agreement == 1.0          # one scorable page, and it agreed
    assert c.known == 1 and c.pages == 3


def test_the_pool_a_selection_draws_from_counts_undecided_pages_too():
    """Precision is measured on what can be checked; the pool is what exists."""
    c = _run([("p1", LASSBERG, LASSBERG), ("p2", MUTE, LASSBERG)])

    assert c.picked("lassberg") == 2
    assert c.known == 1


# ── what it refuses to compare ───────────────────────────────────────────────

def test_an_unlocated_page_is_skipped():
    """Pairing it with a best guess would compare this letter's ground truth
    against another letter's reading."""
    scored = [_Scored(LASSBERG, "")]
    assert wc.compare(scored, {"m": {"p1": LASSBERG}}) == []


def test_a_page_the_reading_does_not_have_is_skipped():
    scored = [_Scored(LASSBERG, "p1")]
    assert wc.compare(scored, {"m": {"p9": LASSBERG}}) == []


def test_every_reading_gets_its_own_confusion():
    scored = [_Scored(LASSBERG, "p1")]
    readings = {"good": {"p1": LASSBERG}, "bad": {"p1": "nichts lesbares"}}

    out = wc.compare(scored, readings)

    assert [c.reading for c in out] == ["bad", "good"]
    assert out[1].precision("lassberg") == 1.0
    assert out[0].picked("lassberg") == 0


# ── the table a decision is made from ────────────────────────────────────────

def test_the_table_names_precision_and_what_it_could_not_see():
    text = wc.format_comparison(_run_many())

    assert "precision" in text
    assert "pages it calls lassberg" in text
    assert "no ground-truth hand at all" in text
    assert "absence of evidence" in text


def _run_many():
    return [_run([("p1", LASSBERG, LASSBERG), ("p2", MUTE, MUTE)])]


def test_nothing_to_compare_says_so_rather_than_printing_an_empty_table():
    assert "no located page" in wc.format_comparison([])
