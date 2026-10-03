"""The ground-truth pages in the shape `pagexml-hf` reads (`hf_export`).

Not a converter. The Flow Project's `pagexml-hf` already turns Transkribus PAGE
XML into a parquet dataset on the hub, and all fourteen `dh-unibe/image-text_*`
datasets were built with it. A second converter would mean a second column layout
for the trainer to tolerate — `serving-atr-inference`'s `hf_source.py` already
carries aliases (`xml_content`/`xml`, `project_name`/`project`) because that
happened once. So this module produces that converter's *input* and stops.

Two things are worth testing, and the first is the reason the module exists.

**Geometry.** The PAGE XML's line polygons are in the pixel coordinates of
Transkribus's copy of the scan; the JPEG beside it is this pipeline's working copy
of the share's scan. If they differ in size, every polygon crops the wrong strip,
and nothing downstream says so: the crops are plausible images and the text is real
text, so the only symptom is a model that trains badly for no stated reason.

**The project split.** `project` becomes a dataset directory and is the axis any
later stratification uses, so it has to be the hand rather than the archive:
measured here, one engine reads Laßberg's correspondents at 6–20 % CER and Laßberg
himself at 27–45 %, and Basel holds both sides of the correspondence in one folder.
The hand is recorded nowhere, so it is inferred from the dateline — a guess about
two lines, and labelled as one.
"""

import sys
from pathlib import Path


PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import hf_export as hf                     # noqa: E402

NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'


def _xml(lines, *, width=2938, height=3638, geometry=True) -> str:
    body = "".join(f"<TextLine id='tl_{i}'><TextEquiv><Unicode>{t}</Unicode>"
                   f"</TextEquiv></TextLine>" for i, t in enumerate(lines))
    page = (f"<Page imageFilename='x.tif' imageWidth='{width}' "
            f"imageHeight='{height}'>" if geometry else "<Page imageFilename='x.tif'>")
    return (f"<?xml version='1.0' encoding='UTF-8'?><PcGts {NS}>"
            f"{page}<TextRegion id='tr'>{body}<TextEquiv><Unicode>"
            f"{chr(10).join(lines)}</Unicode></TextEquiv></TextRegion>"
            f"</Page></PcGts>").replace("'", '"')


class _GT:
    def __init__(self, source, text):
        self.source = source
        self.text = text


class _Scored:
    """Stands in for gt_score.Scored: the plan reads `located` and `gt`."""

    def __init__(self, source, text, located):
        self.gt = _GT(source, text)
        self.located = located


def _image(path: Path, size=(2938, 3638)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 255, 255)).save(path, "JPEG", quality=60)
    return path


LASSBERG = ["Eppishausen am 21 Januar 1831.", "Hochgeschäzter Herr!",
            "Da ich das vergnügen nicht haben soll, Sie zu bewirten;"]
WACKERNAGEL = ["Basel 30 Augst 33.", "Hochgeehrter Herr Baron,",
               "Der Frauendienst erfolgt anbei mit meinem besten Dank."]


# ── the hand, from the dateline ──────────────────────────────────────────────

def test_a_house_in_the_dateline_is_lassbergs_hand():
    who = hf.writer_of("\n".join(LASSBERG))
    assert who.project == "lassberg"
    assert who.evidence == "eppishausen"


def test_meersburg_too():
    assert hf.writer_of("Auf der alten Meersburg am 17 October. 1840.\nHerr!"
                        ).project == "lassberg"


def test_a_city_in_the_dateline_is_a_correspondent():
    who = hf.writer_of("\n".join(WACKERNAGEL))
    assert who.project == "korrespondenten"
    assert who.evidence == "basel"


def test_no_dateline_is_unbestimmt_not_a_guess():
    """A third bucket is the honest answer. A coin flip would put a letter in the
    wrong hand and the split is the axis everything later is measured against."""
    who = hf.writer_of("Hochverehrter Herr Baron,\nmit vielem Dank sende ich")
    assert who.project == "unbestimmt"
    assert not who.certain
    assert who.evidence == ""


def test_a_place_further_down_the_page_does_not_decide():
    """Past the dateline a place name is as likely to be something the letter
    talks about as where it was written."""
    text = ("Hochgeehrter Herr!\nIch danke für Ihren Brief.\n"
            "In Basel soll ein Codex liegen, den ich suche.")
    assert hf.writer_of(text).project == "unbestimmt"


# ── geometry: the check this module exists for ───────────────────────────────

def test_a_page_whose_xml_matches_its_image_is_exported(tmp_path):
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg", (2938, 3638))
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL, width=2938, height=3638), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "Basel__p1")],
                {"Basel__p1": img})

    assert [e.key for e in p.entries] == ["Basel__p1"]
    assert p.rejected == []


def test_a_mismatched_page_is_refused_not_rescaled(tmp_path):
    """The failure this would paper over is invisible: the crops are plausible
    images and the text is real text."""
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg", (1469, 1819))
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL, width=2938, height=3638), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "Basel__p1")],
                {"Basel__p1": img})

    assert p.entries == []
    assert "geometry" in p.rejected[0].reason
    assert "2938x3638" in p.rejected[0].reason
    assert "1469x1819" in p.rejected[0].reason


def test_xml_without_geometry_is_unverifiable_not_fine(tmp_path):
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg")
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL, geometry=False), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "Basel__p1")],
                {"Basel__p1": img})

    assert p.entries == []
    assert "no geometry" in p.rejected[0].reason


def test_the_check_can_be_turned_off_explicitly(tmp_path):
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg", (100, 100))
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "Basel__p1")],
                {"Basel__p1": img}, require_geometry=False)

    assert len(p.entries) == 1


def test_either_attribute_order_is_read():
    assert hf.xml_size('<Page imageHeight="3638" imageWidth="2938">') == (2938, 3638)
    assert hf.xml_size('<Page imageWidth="2938" imageHeight="3638">') == (2938, 3638)
    assert hf.xml_size('<Page imageFilename="x.tif">') is None


# ── what is left out ─────────────────────────────────────────────────────────

def test_an_unlocated_page_is_not_paired_with_a_guess(tmp_path):
    """Pairing it with the best match would put a different letter's scan beside
    this letter's transcription."""
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "")], {})

    assert p.entries == []
    assert "not located" in p.rejected[0].reason


def test_the_same_page_is_exported_once(tmp_path):
    """Transkribus holds the same page under several doc ids; twice in the export
    is one page with twice the weight."""
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg")
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    for f in (a, b):
        f.write_text(_xml(WACKERNAGEL), encoding="utf-8")

    p = hf.plan([_Scored(a, "\n".join(WACKERNAGEL), "Basel__p1"),
                 _Scored(b, "\n".join(WACKERNAGEL), "Basel__p1")],
                {"Basel__p1": img})

    assert len(p.entries) == 1
    assert "duplicate" in p.rejected[0].reason


def test_a_key_with_no_image_under_the_source_is_named(tmp_path):
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "Basel__p9")], {})

    assert "no image" in p.rejected[0].reason


# ── the tree pagexml-hf reads ────────────────────────────────────────────────

def test_the_tree_is_one_folder_per_project_with_page_beside_it(tmp_path):
    img_a = _image(tmp_path / "src" / "Basel" / "p1.jpg")
    img_b = _image(tmp_path / "src" / "Basel" / "p2.jpg")
    a, b = tmp_path / "a.xml", tmp_path / "b.xml"
    a.write_text(_xml(WACKERNAGEL), encoding="utf-8")
    b.write_text(_xml(LASSBERG), encoding="utf-8")

    p = hf.plan([_Scored(a, "\n".join(WACKERNAGEL), "Basel__p1"),
                 _Scored(b, "\n".join(LASSBERG), "Basel__p2")],
                {"Basel__p1": img_a, "Basel__p2": img_b})
    out = tmp_path / "export"
    written = hf.build(p, out)

    assert written["projects"] == {"korrespondenten": 1, "lassberg": 1}
    assert (out / "korrespondenten" / "Basel__p1.jpg").is_file()
    assert (out / "korrespondenten" / "page" / "Basel__p1.xml").is_file()
    assert (out / "lassberg" / "Basel__p2.jpg").is_file()
    assert (out / "lassberg" / "page" / "Basel__p2.xml").is_file()


def test_the_image_and_the_xml_share_a_stem(tmp_path):
    """pagexml-hf pairs them by stem, so a key that is safe in one name and not
    the other would silently drop the transcription."""
    img = _image(tmp_path / "src" / "a b" / "p,1.jpg")
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL), encoding="utf-8")

    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "a b__p,1")],
                {"a b__p,1": img})
    out = tmp_path / "export"
    hf.build(p, out)

    stem = p.entries[0].stem
    assert (out / "korrespondenten" / f"{stem}.jpg").is_file()
    assert (out / "korrespondenten" / "page" / f"{stem}.xml").is_file()
    assert "," not in stem and " " not in stem


# ── the plan a human reads before uploading ──────────────────────────────────

def test_the_plan_names_the_size_of_the_guess(tmp_path):
    img = _image(tmp_path / "src" / "Basel" / "p1.jpg")
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(["Hochverehrter Herr Baron,", "mit Dank"]), encoding="utf-8")

    p = hf.plan([_Scored(gt, "Hochverehrter Herr Baron,\nmit Dank", "Basel__p1")],
                {"Basel__p1": img})
    text = hf.format_plan(p)

    assert "unbestimmt" in text
    assert "no recognisable dateline" in text


def test_the_plan_counts_what_it_left_out_by_reason(tmp_path):
    gt = tmp_path / "gt.xml"
    gt.write_text(_xml(WACKERNAGEL), encoding="utf-8")
    p = hf.plan([_Scored(gt, "\n".join(WACKERNAGEL), "")], {})

    text = hf.format_plan(p)
    assert "left out: 1" in text
    assert "not located" in text


# ── the index, and what it must not need ─────────────────────────────────────
#
# 2026-10-02, the first real run of `export-hf`:
#
#     File "hf_export.py", line 165, in page_index
#       listed = batch.discover_pages(Path(source_root))
#     ...
#     File "utils/images.py", line 101, in pdf_page_count
#       import pypdfium2 as pdfium
#     ModuleNotFoundError: No module named 'pypdfium2'
#
# The export ended on the first PDF in the page cache, four frames below
# anything that mentions PDFs. Two separate faults met there: this function went
# through `discover_pages`, which expands every PDF into a page count it then
# discarded, and `pdf_page_count` imported its renderer outside its own `try`
# although its docstring promised 0 when a PDF "cannot be opened".

def test_the_index_keys_images_by_the_runners_rule(tmp_path):
    import atr_batch as batch

    for rel in ("Basel/lassberg-letter-1/p1.jpg", "Aarau/upload/x/p2.jpg"):
        _image(tmp_path / rel)

    index = hf.page_index(tmp_path)

    assert set(index) == {"Basel__lassberg-letter-1__p1", "Aarau__upload__x__p2"}
    assert index["Basel__lassberg-letter-1__p1"].name == "p1.jpg"
    # the same function the runner names its outputs with, not a second spelling
    assert batch.page_key(Path("Basel/lassberg-letter-1/p1.jpg")) in index


def test_a_pdf_in_the_source_is_ignored_without_being_opened(tmp_path, monkeypatch):
    """The export has nothing to copy for a page inside a PDF, so counting those
    pages was work it discarded — and on a host without the renderer it was the
    end of the run."""
    import utils.images as images

    _image(tmp_path / "Basel" / "p1.jpg")
    (tmp_path / "Basel" / "letter.pdf").write_bytes(b"%PDF-1.4 not really")

    def explode(_data):
        raise AssertionError("page_index must not count PDF pages")

    monkeypatch.setattr(images, "pdf_page_count", explode)
    index = hf.page_index(tmp_path)

    assert set(index) == {"Basel__p1"}


def test_the_index_survives_a_host_without_the_pdf_renderer(tmp_path, monkeypatch):
    """The failure as it happened: import the renderer and there is none."""
    import builtins

    real = builtins.__import__

    def no_pypdfium(name, *a, **k):
        if name == "pypdfium2":
            raise ModuleNotFoundError("No module named 'pypdfium2'")
        return real(name, *a, **k)

    _image(tmp_path / "Basel" / "p1.jpg")
    (tmp_path / "Basel" / "letter.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(builtins, "__import__", no_pypdfium)

    assert set(hf.page_index(tmp_path)) == {"Basel__p1"}


# ── the cache is a cache, not a store ────────────────────────────────────────
#
# 2026-10-02, the export of 276 pages:
#
#     pages to export: 0
#     left out: 552
#       no image            357
#       not located         195
#
# Every located page reported "no image". The page cache is not storage: a daily
# job moves anything older than two days to the research share with paths
# preserved (#487), which is what `images.cold_fetch` looks in before the wire.
# The corpus run that made those working copies was twelve days old, so all of
# them had been evicted — and `page_index` walked only the hot directory. "Where
# are the pages" had two answers and the code knew one.

def test_a_page_only_in_the_cold_tier_is_found(tmp_path):
    hot = tmp_path / "cache"
    cold = tmp_path / "archive"
    hot.mkdir()
    _image(cold / "Basel" / "p1.jpg")

    index = hf.page_index(hot, archive=cold)

    assert set(index) == {"Basel__p1"}
    assert index["Basel__p1"].is_relative_to(cold)


def test_the_two_tiers_are_merged(tmp_path):
    _image(tmp_path / "cache" / "Basel" / "p1.jpg")
    _image(tmp_path / "archive" / "Basel" / "p2.jpg")

    index = hf.page_index(tmp_path / "cache", archive=tmp_path / "archive")

    assert set(index) == {"Basel__p1", "Basel__p2"}


def test_the_hot_copy_wins(tmp_path):
    """The same key in both tiers means an entry was brought back; the archived
    copy is then the older generation of the two."""
    _image(tmp_path / "cache" / "Basel" / "p1.jpg")
    _image(tmp_path / "archive" / "Basel" / "p1.jpg")

    index = hf.page_index(tmp_path / "cache", archive=tmp_path / "archive")

    assert index["Basel__p1"].is_relative_to(tmp_path / "cache")


def test_no_archive_still_works(tmp_path):
    _image(tmp_path / "cache" / "Basel" / "p1.jpg")
    assert set(hf.page_index(tmp_path / "cache")) == {"Basel__p1"}


def test_an_archive_that_is_not_there_is_not_fatal(tmp_path):
    """A host with no cold tier configured is a normal host."""
    _image(tmp_path / "cache" / "Basel" / "p1.jpg")
    index = hf.page_index(tmp_path / "cache", archive=tmp_path / "nope")
    assert set(index) == {"Basel__p1"}


def test_keys_are_relative_to_each_tiers_own_root(tmp_path):
    """Paths are preserved across the eviction, so the same page has the same key
    in either tier — which is the property that makes the merge sound."""
    import atr_batch as batch

    rel = Path("Donaueschingen") / "Photos-1-001" / "letter-1247" / "PXL_1.jpg"
    _image(tmp_path / "archive" / rel)

    index = hf.page_index(tmp_path / "cache", archive=tmp_path / "archive")

    assert batch.page_key(rel) in index
    assert "Donaueschingen__Photos-1-001__letter-1247__PXL_1" in index


# ── where the cache is, answered once ────────────────────────────────────────
#
# 2026-10-03. The runbook said `--source "$ATR_PAGE_CACHE"`, because `--source`
# was required while `--run-dir` and `--gt` resolved their own defaults. That
# shell variable was set and **not exported**: bash put it in the argv,
# `os.environ` never saw it, `config.ATR_PAGE_CACHE` was None, and the path it
# named was a directory that existed and held other images. So the export
# indexed 357 pages under keys that could not match and reported "no image" —
# indistinguishable, from the outside, from an empty cache.
#
# Three diagnoses in a row (a PDF renderer, a cache eviction, this) that an
# argument default would have prevented.

def test_the_default_source_is_the_configured_cache(monkeypatch):
    import config

    monkeypatch.setattr(config, "ATR_PAGE_CACHE", Path("/somewhere/cache"))
    assert hf.default_source() == Path("/somewhere/cache")


def test_without_the_variable_it_is_the_runners_own_fallback(monkeypatch, tmp_path):
    import config

    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert hf.default_source() == tmp_path / "page_cache"


def test_it_agrees_with_the_runner(monkeypatch, tmp_path):
    """Two answers to "where is the cache" is how a run reads one directory and
    an export looks in another — which is exactly what happened."""
    import config
    from mcp_atr import jobs

    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    # what the runner is handed for a dav: source, and what the export defaults to
    assert Path(jobs.cache_dir_for("dav:digitalisate")) == hf.default_source()


def test_it_agrees_with_the_runner_when_the_variable_is_set(monkeypatch, tmp_path):
    import config
    from mcp_atr import jobs

    monkeypatch.setattr(config, "ATR_PAGE_CACHE", tmp_path / "cache")
    assert Path(jobs.cache_dir_for("dav:digitalisate")) == hf.default_source()
