"""A geometry rejection's *pair of sizes* is the diagnosis (#637).

`export-hf` refused 43 of 552 ground-truth pages on 2026-10-13 because the PAGE
XML's `imageWidth/imageHeight` disagreed with the image beside it, and the plan
printed the count plus five names. From that nothing can be decided, and the
count alone invites the wrong decision — `--no-geometry-check`, which exports
pages whose line polygons crop the wrong strip and says nothing downstream.

The pair of sizes decides it, by arithmetic rather than by opening pages:

====================  ==================================  ====================
signature             what it means                       repairable
====================  ==================================  ====================
width/height swapped  one scan, rotated                   yes, by rotation
a pixel or two apart  one renderer against another        yes, it is the page
one factor, two axes  a scaled derivative                 yes, by multiplying
different aspect      two different crops of the page     **no**
====================  ==================================  ====================

So `Rejected` carries both sizes as numbers beside the sentence rather than
inside it, `Plan.signatures` groups by them, and the pages themselves go to a
file: the cap belongs where a report is rendered and never where rows are
collected (#617, the same lesson in `batch_report`).
"""

import sys
from pathlib import Path

import pytest


PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hf_export as hf                     # noqa: E402


def _rejected(n: int, claimed, actual, *, first: int = 0) -> list:
    """``n`` geometry rejections sharing one signature."""
    return [hf.Rejected(f"doc{first + i}_page1.xml",
                        f"geometry: XML says {claimed[0]}x{claimed[1]}, the image "
                        f"is {actual[0]}x{actual[1]} — the line polygons do not "
                        f"describe this image",
                        claimed=claimed, actual=actual)
            for i in range(n)]


# ── the four signatures, each named by numbers alone ─────────────────────────

def test_swapped_sides_are_a_rotation():
    assert "rotated" in hf.describe_signature((2636, 3212), (3212, 2636))


def test_two_pixels_are_two_renderers_not_two_scans():
    """2479x3508 is what Transkribus holds for the printed edition's pages;
    2480x3508 is A4 at this pipeline's 300 dpi. One pixel of rounding."""
    said = hf.describe_signature((2479, 3508), (2480, 3508))
    assert "rounding" in said
    assert "1x0" in said


def test_three_pixels_are_not_rounding_any_more():
    """The boundary is a constant, so a change to it is arithmetic and not a
    rewrite — and it has to be a boundary, or 'rounding' absorbs real crops."""
    assert hf.ROUNDING_PX == 2
    assert "rounding" not in hf.describe_signature((2479, 3508), (2482, 3508))


def test_one_factor_on_both_axes_is_a_scaled_derivative():
    said = hf.describe_signature((2938, 3638), (1469, 1819))
    assert "scaled" in said
    assert "2.000" in said


def test_a_different_aspect_ratio_is_a_different_crop():
    """The one signature the 13 October run showed: 14.6 megapixels against
    2.5, and the height 90 px short of what the width's factor would give. No
    multiplication repairs it, because the two images are not the same crop."""
    said = hf.describe_signature((3333, 4391), (1420, 1781))
    assert "different crop" in said
    assert "2.347" in said and "2.465" in said


def test_a_near_uniform_scale_is_still_called_scaled():
    """Half a percent of slack, or JPEG dimensions rounded to a macroblock
    would read as a different crop."""
    assert hf.UNIFORM_TOLERANCE == 0.005
    assert "scaled" in hf.describe_signature((3000, 4000), (1500, 1998))


# ── grouping, and where the numbers come from ────────────────────────────────

def test_the_signatures_are_counted_per_pair():
    p = hf.Plan(rejected=_rejected(12, (3333, 4391), (1420, 1781))
                + _rejected(3, (2636, 3212), (3212, 2636), first=100))

    assert p.signatures == {(((3333, 4391), (1420, 1781))): 12,
                            (((2636, 3212), (3212, 2636))): 3}


def test_the_grouping_reads_the_numbers_not_the_sentence():
    """The sizes are fields, so rewording the reason cannot change the
    grouping. A report that parses its own prose counts nothing after the next
    rename (#624)."""
    quiet = hf.Rejected("doc1_page1.xml", "geometry: anders formuliert",
                        claimed=(3333, 4391), actual=(1420, 1781))
    loud = _rejected(1, (3333, 4391), (1420, 1781))[0]

    assert hf.Plan(rejected=[quiet, loud]).signatures == {
        ((3333, 4391), (1420, 1781)): 2}


def test_a_rejection_with_no_sizes_has_no_signature():
    """"not located" and "duplicate" are rejections too, and they are not about
    geometry. Absent is absent, never (0, 0)."""
    p = hf.Plan(rejected=[hf.Rejected("doc1.xml", "duplicate: X already exported")])

    assert p.signatures == {}
    assert p.rejected[0].signature is None


def test_the_category_is_the_reasons_first_word():
    p = hf.Plan(rejected=_rejected(2, (10, 20), (20, 10))
                + [hf.Rejected("d.xml", "not located: no page matched")])

    assert p.reasons == {"geometry": 2, "not located": 1}
    assert p.rejected[0].category == "geometry"


# ── the report names signatures; the file names pages ────────────────────────

def test_the_plan_prints_each_signature_with_its_count():
    p = hf.Plan(rejected=_rejected(12, (3333, 4391), (1420, 1781)))

    said = hf.format_plan(p)

    assert "XML 3333x4391 → image 1420x1781" in said
    assert "12 page(s)" in said
    assert "different crop" in said


def test_many_signatures_are_capped_in_the_report_and_the_rest_named_as_a_count():
    rejected = []
    for i in range(hf.SIGNATURES_SHOWN + 4):
        rejected += _rejected(1, (1000 + i, 2000), (500, 1000), first=i * 10)

    said = hf.format_plan(hf.Plan(rejected=rejected))

    assert said.count("page(s)  XML") == hf.SIGNATURES_SHOWN
    assert "and 4 more pair(s)" in said


def test_the_table_holds_every_rejection_the_report_did_not_name():
    """43 pages, 5 named: the cap is a property of the rendering, and the row
    for every page is still there to be read."""
    p = hf.Plan(rejected=_rejected(43, (3333, 4391), (1420, 1781)))

    said = hf.format_plan(p)
    table = hf.rejected_table(p)

    assert "and 38 more" in said
    assert len(table.strip().splitlines()) == 1 + 43        # header + every page


def test_the_table_carries_both_sizes_and_the_shape():
    p = hf.Plan(rejected=_rejected(1, (3333, 4391), (1420, 1781)))

    header, row = hf.rejected_table(p).strip().splitlines()

    assert header.split("\t") == ["key", "category", "claimed", "actual",
                                  "shape", "reason"]
    key, category, claimed, actual, shape, reason = row.split("\t")
    assert (claimed, actual) == ("3333x4391", "1420x1781")
    assert category == "geometry"
    assert shape == "different crop"
    assert "the line polygons do not describe this image" in reason


def test_a_rejection_without_sizes_still_gets_a_row():
    table = hf.rejected_table(hf.Plan(rejected=[
        hf.Rejected("doc1.xml", "not located: no page matched confidently enough")]))

    row = table.strip().splitlines()[1].split("\t")
    assert row[0] == "doc1.xml" and row[1] == "not located"
    assert row[2] == "" and row[3] == "" and row[4] == ""


# ── the plan still measures the real page, end to end ────────────────────────

def test_a_real_mismatch_arrives_with_its_sizes_as_numbers(tmp_path):
    """Not a unit test of the formatter: `plan` is what fills these fields, and
    an earlier version of this change set them nowhere."""
    from PIL import Image

    NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'
    img = tmp_path / "src" / "Basel" / "p1.jpg"
    img.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1420, 1781), (255, 255, 255)).save(img, "JPEG", quality=60)
    gt = tmp_path / "gt.xml"
    gt.write_text(f'<?xml version="1.0"?><PcGts {NS}><Page imageFilename="x.tif" '
                  f'imageWidth="3333" imageHeight="4391"><TextRegion id="tr">'
                  f'<TextEquiv><Unicode>Basel 30 Augst 33.</Unicode></TextEquiv>'
                  f'</TextRegion></Page></PcGts>', encoding="utf-8")

    class _GT:
        def __init__(self, source, text):
            self.source, self.text = source, text

    class _Scored:
        def __init__(self, source, text, located):
            self.gt, self.located = _GT(source, text), located

    p = hf.plan([_Scored(gt, "Basel 30 Augst 33.", "Basel__p1")],
                {"Basel__p1": img})

    assert p.entries == []
    assert p.signatures == {((3333, 4391), (1420, 1781)): 1}
    assert "different crop" in hf.format_plan(p)


# ── the job asks for the file, so a dry run through MCP has it ───────────────

def test_the_export_job_writes_the_rejection_table_too():
    sys.path.insert(0, str(PKG / "mcp_atr"))
    from mcp_atr import jobs

    argv = jobs.export_hf_argv([Path("/runs/one")],
                               writers_out=Path("/out/w.tsv"),
                               rejected_out=Path("/out/r.tsv"))

    assert "--rejected-out" in argv
    assert argv[argv.index("--rejected-out") + 1] == "/out/r.tsv"


def test_without_a_destination_nothing_is_written():
    """A tool that writes files nobody asked for is a side effect; the job
    passes the path, the module does not invent one."""
    argv = __import__("mcp_atr.jobs", fromlist=["jobs"]).export_hf_argv(
        [Path("/runs/one")])

    assert "--rejected-out" not in argv
