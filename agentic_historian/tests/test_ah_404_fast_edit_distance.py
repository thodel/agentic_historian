"""#404: the C-backed Levenshtein must be the same metric, only faster.

Measured on tei: one ensemble page spent 31s in pairwise CER and 36s in fusion —
55% of a 122s page on alignment, with no engine involved. The pair count grows
quadratically with candidates (3 pairs at 3 engines, 21 at the 7 the escalating
pages reach), so this is worst exactly where pages are already slowest.

The whole value of the swap is that it changes nothing. These tests assert the two
paths agree, including on the shapes where an off-by-one hides: empty strings,
one-character strings, and the historical orthography this corpus is full of.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_404_fast_edit_distance.py
"""

import random
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import eval.metrics as M  # noqa: E402


@pytest.fixture
def slow(monkeypatch):
    """Force the pure-Python fallback."""
    monkeypatch.setattr(M, "_rf_lev", None)
    return M.edit_distance


def _fast():
    return M.edit_distance


# verbatim candidate texts from the saa-0428 run
REAL = [
    "modios trititi cum prato sito iuxta Ottiuissingen de quibus anno domini item est",
    "Liber modios Terrici cum prato sito iuxta Octwuling de quibus anno domini",
    "130 modios trinn cum pedto sio uxta Ditwüthigen depuis ſtre deber ſoͤu",
    "Hut genennt die brief Daz Closter het zien gelich die daß Closter ze haben sol",
    "unser frùntlich gruͦs vor liebe getrüwe von der stoͤsse wegē so da sint",
    "引口三へにみへきす国へ是引内すま",          # the CJK candidate (#359)
    "וש ימי ולו הים לש יולגי סל וסורן",          # the Hebrew candidate
]


@pytest.mark.parametrize("a", REAL)
@pytest.mark.parametrize("b", REAL)
def test_both_paths_agree_on_real_candidates(a, b, monkeypatch):
    monkeypatch.setattr(M, "_rf_lev", None)
    s = M.edit_distance(a, b)
    monkeypatch.undo()
    assert M.edit_distance(a, b) == s


@pytest.mark.parametrize("a,b", [
    ("", ""), ("", "x"), ("x", ""), ("a", "a"), ("a", "b"),
    ("ab", "ba"), ("kitten", "sitting"), ("ſ", "s"), ("gruͦs", "grus"),
])
def test_both_paths_agree_on_edge_shapes(a, b, monkeypatch):
    """Empty and single-character inputs are where a DP off-by-one hides."""
    monkeypatch.setattr(M, "_rf_lev", None)
    s = M.edit_distance(a, b)
    monkeypatch.undo()
    assert M.edit_distance(a, b) == s


def test_both_paths_agree_on_page_sized_input(monkeypatch):
    """2,400 characters is the size the ensemble actually compares."""
    random.seed(404)
    alpha = "abcdefghijklmnopqrstuvwxyzäöüſ ,."
    a = "".join(random.choice(alpha) for _ in range(2400))
    b = "".join(c if random.random() > 0.3 else random.choice(alpha) for c in a)

    monkeypatch.setattr(M, "_rf_lev", None)
    s = M.edit_distance(a, b)
    monkeypatch.undo()
    assert M.edit_distance(a, b) == s


# ── the metric's own guarantees, whichever path runs ─────────────────────────

def test_distance_is_symmetric():
    assert M.edit_distance(REAL[0], REAL[1]) == M.edit_distance(REAL[1], REAL[0])


def test_identical_strings_have_distance_zero():
    for t in REAL:
        assert M.edit_distance(t, t) == 0


def test_distance_to_empty_is_the_length():
    assert M.edit_distance("abcdef", "") == 6
    assert M.edit_distance("", "abcdef") == 6


# ── the callers keep their published behaviour ──────────────────────────────

def test_cer_is_unchanged_by_the_swap(monkeypatch):
    a, b = REAL[0], REAL[1]
    monkeypatch.setattr(M, "_rf_lev", None)
    s = M.cer(b, a)
    monkeypatch.undo()
    assert M.cer(b, a) == pytest.approx(s)


def test_cer_still_exceeds_one_when_the_hypothesis_over_generates():
    """CER is edits/reference-length and is unbounded above — #300's no-merge band
    and #367's QA clamp both depend on that staying true.

    Note the argument order: `cer(reference, hypothesis)`. Getting it backwards
    makes the assertion silently measure the opposite case, which is how this test
    failed on its first run.
    """
    assert M.cer("kurz", "x" * 200) > 1.0


def test_levenshtein_helper_is_unchanged_by_the_swap(monkeypatch):
    monkeypatch.setattr(M, "_rf_lev", None)
    s = M.levenshtein("Kitten, das!", "sitting das")
    monkeypatch.undo()
    assert M.levenshtein("Kitten, das!", "sitting das") == s


def test_the_fallback_is_used_when_rapidfuzz_is_absent(monkeypatch):
    """An install without the optional dependency must be slower, never wrong."""
    monkeypatch.setattr(M, "_rf_lev", None)
    assert M.edit_distance("kitten", "sitting") == 3
