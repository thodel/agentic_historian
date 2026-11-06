"""#773 — consistency of the hand, all pages against all.

Synthetic pages in two "hands": upright round letters with thin strokes, and
narrow letters leaning 20° with thick strokes. The tests pin what the spike
promises before it meets a real volume: lines are found, the slant is read,
the profile tolerates a small rotation, and a change of hand shows as a
changepoint, an outlier and a split — while one hand alone shows none of them.
"""

import math
import random
import sys
from pathlib import Path

import numpy as np
import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hand_consistency as hc  # noqa: E402

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

HANDS = {
    # xh, letter width/xh, slant deg, stroke px
    "A": (28, 0.9, 0, 3),
    "B": (28, 0.45, 20, 7),
}


def _page(hand: str, seed: int, lines: int = 7, size=(900, 560)) -> np.ndarray:
    """A page image (uint8) of ``lines`` lines of letter-like shapes in ``hand``."""
    xh, wr, sl, stroke = HANDS[hand]
    rnd = random.Random(seed)
    im = Image.new("L", size, 235)
    dr = ImageDraw.Draw(im)
    pitch = size[1] // (lines + 1)
    for li in range(lines):
        base = pitch * (li + 1)
        x = 40
        while x < size[0] - 60:
            w = int(xh * wr * rnd.uniform(0.9, 1.1))
            top = base - xh
            shear = math.tan(math.radians(sl)) * xh
            kind = rnd.random()
            if kind < 0.5:                       # a bowl
                dr.ellipse([x, top, x + w, base], outline=20, width=stroke)
            else:                                # a stem, leaning by the hand's slant
                dr.line([x + shear, top - (xh // 2 if kind > 0.8 else 0), x, base], fill=20, width=stroke)
            x += w + rnd.randint(int(xh * 0.2), int(xh * 0.5))
            if rnd.random() < 0.15:
                x += xh                          # a word gap
    return np.asarray(im, dtype=np.uint8)


def _ink(g: np.ndarray) -> np.ndarray:
    return g <= hc.otsu(g)


def _feats(hand: str, seed: int, name: str):
    f = hc.page_features(_ink(_page(hand, seed)), name=name)
    assert f is not None
    return f


# ── the parts ────────────────────────────────────────────────────────────────

def test_otsu_splits_a_bimodal_image():
    g = np.concatenate([np.full(500, 30), np.full(500, 220)]).astype(np.uint8)
    t = hc.otsu(g)
    assert 30 <= t < 220


def test_lines_are_found_from_the_row_profile():
    ink = _ink(_page("A", 1, lines=7))
    found = hc.find_lines(ink)
    assert len(found) == 7, found


def test_slant_reads_the_lean_of_the_hand():
    a = hc.page_features(_ink(_page("A", 2)))
    b = hc.page_features(_ink(_page("B", 2)))
    assert abs(a.scalars[0]) <= 4
    assert abs(b.scalars[0]) >= 12, "the leaning hand reads as leaning"
    assert b.scalars[1] > a.scalars[1], "thicker strokes, in x-heights"


def test_a_round_shape_has_a_flat_profile_and_tolerates_rotation():
    yy, xx = np.mgrid[-20:21, -20:21]
    disk = np.argwhere(xx ** 2 + yy ** 2 <= 400)
    p = hc.radial_profile(disk)
    assert p is not None and p.min() > 0.85
    bar = np.argwhere((abs(xx) <= 3) & (abs(yy) <= 20))
    q = hc.radial_profile(bar)
    rot = np.roll(q, 2)                        # ~3° turned
    assert hc.profile_distance(q, rot) < 1e-9 < hc.profile_distance(q, np.roll(q, 40))


def test_components_are_eight_connected():
    m = np.zeros((5, 5), bool)
    m[0, 0] = m[1, 1] = m[2, 2] = True          # a diagonal: one component
    m[4, 4] = True                              # alone
    sizes = sorted(len(c) for c in hc.components(m))
    assert sizes == [1, 3]


# ── the volume ───────────────────────────────────────────────────────────────

def test_a_change_of_hand_is_a_changepoint():
    pages = [_feats("A", s, f"p{s:02d}") for s in range(7)] + \
            [_feats("B", s, f"p{s + 7:02d}") for s in range(3)]
    rep = hc.analyse(pages)
    assert rep.changepoint["index"] == 7 and rep.changepoint["page"] == "p07"
    one = hc.analyse([_feats("A", s, f"p{s:02d}") for s in range(10)])
    assert rep.changepoint["ratio"] > 1.5 * one.changepoint["ratio"]
    assert rep.silhouette2 > one.silhouette2


def test_a_single_foreign_page_is_an_outlier_and_never_the_typical_one():
    pages = [_feats("A", s, f"p{s:02d}") for s in range(9)]
    pages.insert(4, _feats("B", 99, "odd"))
    rep = hc.analyse(pages)
    assert rep.outliers == ["odd"]
    assert rep.typical != "odd"


def test_block_means_rise_where_the_hand_changes():
    pages = [_feats("A", s, f"p{s:02d}") for s in range(6)] + \
            [_feats("A", 50, "p06"), _feats("B", 51, "p07"), _feats("B", 52, "p08")]
    rep = hc.analyse(pages, blocks=3)
    assert len(rep.blocks) == 3
    assert rep.blocks[-1]["centrality"] > rep.blocks[0]["centrality"]


def test_the_typical_page_is_deterministic():
    pages = [_feats("A", s, f"p{s:02d}") for s in range(5)]
    assert hc.analyse(pages).typical == hc.analyse(list(pages)).typical


def test_a_page_without_lines_gives_no_features():
    blank = np.zeros((300, 400), bool)
    assert hc.page_features(blank) is None


def test_validation_table_names_the_decision():
    rows = [{"id": "a", "hands": 1, "pages": 10, "skipped": 0, "silhouette2": 0.1,
             "changepoint_ratio": 1.1, "changepoint_page": "p03", "outliers": 0, "typical": "p01"},
            {"id": "b", "hands": 2, "pages": 10, "skipped": 0, "silhouette2": 0.5,
             "changepoint_ratio": 1.9, "changepoint_page": "p07", "outliers": 1, "typical": "p02"}]
    table = hc.format_validation(rows)
    assert "| a | 1 | 10 |" in table and "0.5 (n=1)" in table and "#777/#778 fall away" in table
