"""Two sizes one factor relates are one scan twice, so the page comes along (#638).

The geometry check refuses a page whose PAGE XML disagrees with its image,
because the polygons would crop the wrong strip of every line and nothing below
the dataset would say so. Measuring all 43 refusals of the 14 October run showed
that they are not one population:

===========  =======  ============================================  ===========
pages        factor   what the two sizes are                        this export
===========  =======  ============================================  ===========
27           —        two crops of the page (one a fixed frame:     refused
                      1420x1781 for 16 pages of three heights)
16           uniform  the same scan at two resolutions              **exported**
===========  =======  ============================================  ===========

The sixteen agree on their factor to **0.11 % or better** on both axes; the
nearest refused pair is 0.78 % out. That is not the judgement the module refuses
to make — picking which of two *different* scans is authoritative — it is an
arithmetic fact about one scan, so the coordinates are divided by the factor and
the page says so in its own first comment. `imageWidth`/`imageHeight` are
rewritten too, or the document would still contradict its image one step further
down, where nothing runs this check.

The factors are 1.58, 1.62, 1.99, 2.10, 2.26, 2.63 and 3.31 — nine archives
delivering their own derivatives, not one pipeline. An earlier reading of this
page called the most frequent one "300 against 133 dpi"; the full table says
there is no single relationship to name.
"""

import sys
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hf_export as hf                     # noqa: E402

NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'

#: Every size pair the 14 October run refused, with the factor the measurement
#: found — `None` where the two images are different crops. The real table, so a
#: change to either threshold has to answer to this corpus and not to an example.
MEASURED = [
    # pages, claimed, actual, factor
    (8, (3333, 4362), (1420, 1781), None),
    (4, (3333, 4391), (1420, 1781), None),
    (4, (3333, 4304), (1420, 1781), None),
    (3, (1698, 2568), (1655, 2549), None),
    (2, (2923, 3690), (1267, 1612), None),
    (2, (1770, 2598), (1296, 1636), None),
    (1, (1182, 1506), (1296, 1636), None),
    (1, (791, 1158), (1296, 1636), None),
    (1, (2479, 3508), (2968, 2553), None),      # landscape: a two-page opening
    (1, (1566, 2639), (1566, 1325), None),      # same width, half the height
    (4, (1770, 2598), (785, 1152), 2.2550),
    (2, (2923, 3690), (1296, 1636), 2.2554),
    (2, (1818, 2457), (913, 1233), 1.9920),
    (1, (1818, 2457), (1148, 1551), 1.5838),
    (1, (2171, 2803), (1371, 1770), 1.5836),
    (1, (1541, 2309), (773, 1159), 1.9928),
    (1, (1258, 2015), (775, 1240), 1.6241),
    (1, (1258, 2015), (600, 960), 2.0979),
    (1, (1258, 1707), (600, 814), 2.0969),
    (1, (3137, 4016), (1192, 1527), 2.6309),
    (1, (3137, 4016), (948, 1213), 3.3100),
]


def _xml(width: int, height: int, *, coords="100,200 300,400",
         baseline="100,390 300,395") -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?><PcGts {NS}>'
            f'<Page imageFilename="x.tif" imageWidth="{width}" '
            f'imageHeight="{height}"><TextRegion id="tr">'
            f'<Coords points="0,0 {width},{height}"/>'
            f'<TextLine id="tl_0"><Coords points="{coords}"/>'
            f'<Baseline points="{baseline}"/>'
            f'<TextEquiv><Unicode>Basel 30 Augst 33.</Unicode></TextEquiv>'
            f'</TextLine><TextEquiv><Unicode>Basel 30 Augst 33.</Unicode>'
            f'</TextEquiv></TextRegion></Page></PcGts>')


def _image(path: Path, size):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(path, "JPEG", quality=60)
    return path


class _GT:
    def __init__(self, source, text):
        self.source, self.text = source, text


class _Scored:
    def __init__(self, source, text, located):
        self.gt, self.located = _GT(source, text), located


def _page(tmp_path, name, claimed, actual):
    """One scored page: an XML claiming ``claimed``, an image of ``actual``."""
    gt = tmp_path / f"{name}.xml"
    gt.write_text(_xml(*claimed), encoding="utf-8")
    img = _image(tmp_path / "src" / "Basel" / f"{name}.jpg", actual)
    return _Scored(gt, "Basel 30 Augst 33.", f"Basel__{name}"), img


# ── the measurement decides, and one function holds it ───────────────────────

@pytest.mark.parametrize("pages,claimed,actual,factor", MEASURED)
def test_every_measured_pair_is_sorted_as_the_run_sorted_it(pages, claimed,
                                                            actual, factor):
    got = hf.uniform_scale(claimed, actual)

    if factor is None:
        assert got is None, f"{claimed}→{actual} would now be rescaled"
    else:
        assert got == pytest.approx(factor, abs=0.001)


@pytest.mark.parametrize("pages,claimed,actual,factor", MEASURED)
def test_the_description_and_the_decision_cannot_disagree(pages, claimed,
                                                          actual, factor):
    """One function answers "is this a rescale". Two would be a page exported
    with coordinates nothing checked, described as refused.

    Compared on the token before the colon, which is what `rejected_table`
    writes into its `shape` column — not on the prose, where "not one rescaled"
    contains the word "scaled" and made the first version of this test pass a
    refusal off as a rescale.
    """
    shape = hf.describe_signature(claimed, actual).split(":", 1)[0]

    assert (shape == "scaled") is (hf.uniform_scale(claimed, actual) is not None)


def test_the_sixteen_are_sixteen():
    """The split this change rests on, as a number: 16 of 43 come along."""
    rescalable = sum(n for n, c, a, f in MEASURED
                     if hf.uniform_scale(c, a) is not None)

    assert rescalable == 16
    assert sum(n for n, *_ in MEASURED) == 43


def test_a_size_of_zero_is_not_a_factor():
    assert hf.uniform_scale((100, 100), (0, 100)) is None


# ── the coordinates ──────────────────────────────────────────────────────────

def test_points_are_divided_and_rounded_to_pixels():
    assert hf.rescale_points("100,200 300,400", 2.255) == "44,89 133,177"


def test_a_pair_that_cannot_be_parsed_is_left_alone():
    """A coordinate this cannot read is one it must not invent."""
    assert hf.rescale_points("10,20 x,y 30,40", 2.0) == "5,10 x,y 15,20"


def test_the_baseline_is_scaled_as_well_as_the_outline():
    """Moving one and not the other would crop correctly and order the lines
    wrongly, which no column downstream would show."""
    out = hf.rescale_xml(_xml(1770, 2598), 2.255, (1770, 2598), (785, 1152))

    assert 'points="44,89 133,177"' in out          # Coords
    assert 'points="44,173 133,175"' in out         # Baseline


def test_the_declared_geometry_is_rewritten_to_the_image():
    out = hf.rescale_xml(_xml(1770, 2598), 2.255, (1770, 2598), (785, 1152))

    assert hf.xml_size(out) == (785, 1152)


def test_the_page_says_what_was_done_to_it():
    out = hf.rescale_xml(_xml(1770, 2598), 2.255, (1770, 2598), (785, 1152))

    assert "agentic_historian" in out
    assert "divided by 2.255" in out
    assert "1770x2598" in out and "785x1152" in out


def test_the_note_comes_after_the_declaration():
    """An XML declaration has to be the first thing in the document."""
    out = hf.rescale_xml(_xml(1770, 2598), 2.255, (1770, 2598), (785, 1152))

    assert out.startswith("<?xml")
    assert out.index("<!--") < out.index("<PcGts")


def test_a_document_without_a_declaration_still_gets_the_note():
    out = hf.rescale_xml("<PcGts><Page/></PcGts>", 2.0, (10, 10), (5, 5))

    assert out.startswith("<!--")


# ── the plan: exported, refused, and not leaking between pages ───────────────

def test_a_uniformly_scaled_page_is_exported_with_its_factor(tmp_path):
    scored, img = _page(tmp_path, "p1", (1770, 2598), (785, 1152))

    p = hf.plan([scored], {"Basel__p1": img})

    assert [e.key for e in p.entries] == ["Basel__p1"]
    assert p.rejected == []
    entry, = p.entries
    assert entry.scale == pytest.approx(2.2550, abs=0.001)
    assert entry.claimed == (1770, 2598) and entry.actual == (785, 1152)
    assert p.rescaled == [entry]


def test_a_different_crop_is_still_refused(tmp_path):
    """The 27 that stay out. 1420x1781 is the fixed frame 16 pages of three
    different heights all landed in — no factor relates those."""
    scored, img = _page(tmp_path, "p1", (3333, 4362), (1420, 1781))

    p = hf.plan([scored], {"Basel__p1": img})

    assert p.entries == []
    assert "geometry" in p.rejected[0].reason


def test_a_matching_page_carries_no_factor(tmp_path):
    scored, img = _page(tmp_path, "p1", (785, 1152), (785, 1152))

    entry, = hf.plan([scored], {"Basel__p1": img}).entries

    assert entry.scale is None
    assert hf.Plan(entries=[entry]).rescaled == []


def test_a_factor_does_not_leak_into_the_next_page(tmp_path):
    """Both pages in one plan: the scaled one first. A `scale` that only exists
    on some paths through the loop is how the second page inherits the first
    page's factor and is written with coordinates for another image."""
    a, img_a = _page(tmp_path, "scaled", (1770, 2598), (785, 1152))
    b, img_b = _page(tmp_path, "exact", (785, 1152), (785, 1152))

    p = hf.plan([a, b], {"Basel__scaled": img_a, "Basel__exact": img_b})

    by_key = {e.key: e for e in p.entries}
    assert by_key["Basel__scaled"].scale is not None
    assert by_key["Basel__exact"].scale is None


def test_with_the_check_off_nothing_is_rescaled(tmp_path):
    """`--no-geometry-check` exports the page as it is. It is not a quiet
    rescale, and it never was."""
    scored, img = _page(tmp_path, "p1", (1770, 2598), (785, 1152))

    p = hf.plan([scored], {"Basel__p1": img}, require_geometry=False)

    assert [e.scale for e in p.entries] == [None]


# ── the tree, and the check it would face downstream ─────────────────────────

def test_the_written_xml_describes_the_image_beside_it(tmp_path):
    """The point of the whole change, end to end: after the tree is written,
    the geometry check that refused this page passes on what is in it."""
    scored, img = _page(tmp_path, "p1", (1770, 2598), (785, 1152))
    p = hf.plan([scored], {"Basel__p1": img})

    hf.build(p, tmp_path / "tree")

    written = next((tmp_path / "tree").rglob("page/*.xml"))
    image = next(q for q in (tmp_path / "tree").rglob("*.jpg"))
    assert hf.xml_size(written.read_text(encoding="utf-8")) == hf.image_size(image)


def test_an_unscaled_page_is_still_a_byte_copy(tmp_path):
    """241 of the 254 exported pages are untouched, and 'untouched' has to mean
    it. (The sixteen rescalable files describe thirteen pages — measured on
    2026-10-14, after this docstring first said 257.)"""
    scored, img = _page(tmp_path, "p1", (785, 1152), (785, 1152))
    p = hf.plan([scored], {"Basel__p1": img})

    hf.build(p, tmp_path / "tree")

    written = next((tmp_path / "tree").rglob("page/*.xml"))
    assert written.read_bytes() == (tmp_path / "p1.xml").read_bytes()


def test_the_build_reports_how_many_it_rewrote(tmp_path):
    a, img_a = _page(tmp_path, "scaled", (1770, 2598), (785, 1152))
    b, img_b = _page(tmp_path, "exact", (785, 1152), (785, 1152))
    p = hf.plan([a, b], {"Basel__scaled": img_a, "Basel__exact": img_b})

    out = hf.build(p, tmp_path / "tree")

    assert out["pages"] == 2 and out["rescaled"] == 1


# ── the two reports ──────────────────────────────────────────────────────────

def test_the_plan_names_the_rescaled_pages(tmp_path):
    scored, img = _page(tmp_path, "p1", (1770, 2598), (785, 1152))

    said = hf.format_plan(hf.plan([scored], {"Basel__p1": img}))

    assert "1 of them carry rescaled coordinates" in said
    assert "1770x2598 → 785x1152" in said
    assert "Basel__p1" in said


def test_the_writers_table_carries_the_factor(tmp_path):
    scored, img = _page(tmp_path, "p1", (1770, 2598), (785, 1152))

    table = hf.writers_table(hf.plan([scored], {"Basel__p1": img}))

    header, row = table.strip().splitlines()
    assert "scale" in header.split("\t")
    cell = row.split("\t")[header.split("\t").index("scale")]
    assert float(cell) == pytest.approx(2.2550, abs=0.001)


def test_a_plan_with_nothing_rescaled_says_nothing_about_it(tmp_path):
    scored, img = _page(tmp_path, "p1", (785, 1152), (785, 1152))

    said = hf.format_plan(hf.plan([scored], {"Basel__p1": img}))

    assert "rescaled coordinates" not in said
