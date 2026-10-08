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

import pytest


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


# ── driving the export from a session ────────────────────────────────────────
#
# The build and the upload both have to happen on tei, so a session that cannot
# reach it was reduced to dictating commands. This is the same job shape as
# `score_job`, with two things deliberately missing from the surface.

def _jobs():
    import importlib
    import sys as _sys
    if str(PKG) not in _sys.path:
        _sys.path.insert(0, str(PKG))
    return importlib.import_module("mcp_atr.jobs")


def test_the_argv_is_the_documented_cli():
    jobs = _jobs()
    argv = jobs.export_hf_argv([Path("/runs/a")], gt_dir=Path("/gt"),
                               out=Path("/out/tree"), dry_run=False)

    assert argv[1:4] == ["-m", "agentic_historian", "export-hf"]
    assert argv[argv.index("--run-dir") + 1] == "/runs/a"
    assert argv[argv.index("--gt") + 1] == "/gt"
    assert argv[argv.index("--out") + 1] == "/out/tree"
    assert "--dry-run" not in argv


def test_no_source_or_archive_path_comes_from_the_caller():
    """`$ATR_PAGE_CACHE`, set but not exported, once named a directory of
    unrelated images and produced 357 "no image" lines. Both default to the
    configuration instead."""
    jobs = _jobs()
    argv = jobs.export_hf_argv([Path("/runs/a")])

    assert "--source" not in argv
    assert "--archive" not in argv


def test_the_geometry_check_cannot_be_turned_off_from_here():
    """A page whose XML geometry disagrees with its image crops the wrong strip
    out of every line, and nothing downstream of the dataset would say so."""
    jobs = _jobs()
    argv = jobs.export_hf_argv([Path("/runs/a")], dry_run=False)

    assert "--no-geometry-check" not in argv
    import inspect
    assert "no_geometry_check" not in inspect.signature(
        jobs.export_hf_job).parameters


def test_a_dry_run_is_the_default_and_writes_nowhere(monkeypatch, tmp_path):
    """Writing the tree copies an image per page — gigabytes for this corpus."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    (tmp_path / "runs" / "atr_gt_candidates").mkdir(parents=True)
    seen = {}

    def _peek(kind, argv, **kw):
        seen["kind"], seen["argv"] = kind, list(argv)
        return {"done": False, "job_id": "j1", "state": "running"}

    monkeypatch.setattr(jobs, "start_and_peek", _peek)
    result = jobs.export_hf_job(["atr_gt_candidates"])

    assert seen["kind"] == "export-hf"
    assert "--dry-run" in seen["argv"] and "--out" not in seen["argv"]
    assert result["out_dir"] is None


def test_writing_names_its_own_output_directory(monkeypatch, tmp_path):
    """Under VLM_TEST_ROOT and stamped, because the CLI refuses a directory that
    is not empty: a half-written export mixed with an older one would upload
    both."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    (tmp_path / "runs" / "atr_gt_candidates").mkdir(parents=True)
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: {"done": True, "argv": list(argv)})

    result = jobs.export_hf_job(["atr_gt_candidates"], dry_run=False)

    assert result["out_dir"].startswith(str(tmp_path / "runs" / "hf-export-"))
    assert "--dry-run" not in result["argv"]


def test_the_job_always_writes_the_writers_table(monkeypatch, tmp_path):
    """Dry run included, and under VLM_TEST_ROOT rather than /tmp: it is the only
    way to disagree with an inferred hand, and the key list that lived in /tmp did
    not survive tei's reboot (#535)."""
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    (tmp_path / "runs" / "atr_gt_candidates").mkdir(parents=True)
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: {"done": True, "argv": list(argv)})

    result = jobs.export_hf_job(["atr_gt_candidates"])

    argv = result["argv"]
    assert argv[argv.index("--writers-out") + 1] == result["writers"]
    assert result["writers"].startswith(str(tmp_path / "runs" / "hf-writers-"))


def test_the_same_containment_rules_as_scoring(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)

    with pytest.raises(jobs.JobError):
        jobs.export_hf_job(["../../etc"])
    with pytest.raises(jobs.JobError):
        jobs.export_hf_job(["a"], gt_dir="../../etc")


# ── the upload ───────────────────────────────────────────────────────────────
#
# `pagexml-hf` owns the parquet layout; this repository only hands it the tree.
# What is worth testing here is the preflight — every one of these failures
# otherwise surfaces minutes into a transfer, from inside a tool this repository
# does not own — and that private stays private.

def _tree(root: Path, projects=(("lassberg", 2), ("korrespondenten", 1))) -> Path:
    for name, n in projects:
        (root / name / "page").mkdir(parents=True, exist_ok=True)
        for i in range(n):
            (root / name / f"p{i}.jpg").write_bytes(b"\xff\xd8\xff")
            (root / name / "page" / f"p{i}.xml").write_text("<PcGts/>",
                                                            encoding="utf-8")
    return root


def test_the_command_is_private_by_default_and_says_so_explicitly():
    """A default that lives in somebody else's tool can change between versions,
    and the difference here is whether unpublished archival images are on the
    open web."""
    assert "--private" in hf.upload_argv(Path("/t"), "dh-unibe/x")
    assert "--private" not in hf.upload_argv(Path("/t"), "dh-unibe/x",
                                             private=False)
    argv = hf.upload_argv(Path("/t"), "dh-unibe/x")
    assert argv[0] == "pagexml-hf"
    assert argv[argv.index("--mode") + 1] == hf.UPLOAD_MODE


def test_a_complete_tree_has_no_problems(tmp_path, monkeypatch):
    monkeypatch.setattr(hf.config, "HF_TOKEN", "t")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)

    plan = hf.inspect_upload(_tree(tmp_path / "tree"), "dh-unibe/x")

    assert plan.ok and plan.pages == 3
    assert plan.projects == {"lassberg": 2, "korrespondenten": 1}


def test_an_image_without_its_xml_is_a_problem(tmp_path, monkeypatch):
    """`pagexml-hf` pairs them by stem and would silently drop the odd ones."""
    monkeypatch.setattr(hf.config, "HF_TOKEN", "t")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)
    tree = _tree(tmp_path / "tree")
    (tree / "lassberg" / "orphan.jpg").write_bytes(b"\xff\xd8\xff")

    plan = hf.inspect_upload(tree, "dh-unibe/x")

    assert not plan.ok
    assert any("silently drop" in p for p in plan.problems)


def test_an_empty_tree_is_a_problem_not_an_empty_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(hf.config, "HF_TOKEN", "t")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)
    (tmp_path / "empty").mkdir()

    plan = hf.inspect_upload(tmp_path / "empty", "dh-unibe/x")

    assert not plan.ok and plan.pages == 0


@pytest.mark.parametrize("bad", ["", "name", "--private", "a/b/c", "/b"])
def test_a_repo_id_of_the_wrong_shape_is_refused(tmp_path, monkeypatch, bad):
    """`pagexml-hf` would otherwise create a repository nobody meant."""
    monkeypatch.setattr(hf.config, "HF_TOKEN", "t")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)

    plan = hf.inspect_upload(_tree(tmp_path / "tree"), bad)

    assert any("owner/name" in p for p in plan.problems)


def test_a_missing_token_is_reported_without_being_printed(tmp_path, monkeypatch):
    monkeypatch.setattr(hf.config, "HF_TOKEN", "")
    monkeypatch.delenv("HUGGINGFACE_HUB_TOKEN", raising=False)
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)

    plan = hf.inspect_upload(_tree(tmp_path / "tree"), "dh-unibe/x")

    assert plan.token is False
    assert any("no Hugging Face token" in p for p in plan.problems)


def test_the_token_never_appears_in_the_printed_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(hf.config, "HF_TOKEN", "hf_secretvalue")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)

    text = hf.format_upload(hf.inspect_upload(_tree(tmp_path / "tree"),
                                              "dh-unibe/x"))

    assert "hf_secretvalue" not in text
    assert "token     : present" in text


def test_a_public_plan_says_so_loudly(tmp_path, monkeypatch):
    monkeypatch.setattr(hf.config, "HF_TOKEN", "t")
    monkeypatch.setattr(hf.shutil, "which", lambda name: "/usr/bin/" + name)

    text = hf.format_upload(hf.inspect_upload(_tree(tmp_path / "tree"),
                                              "dh-unibe/x", private=False))

    assert "** PUBLIC **" in text


def test_the_mcp_tool_cannot_upload_publicly():
    """The CLI can, with a second confirmation. This cannot, and the flag is
    absent from the argv rather than defaulted to false."""
    jobs = _jobs()
    argv = jobs.upload_hf_argv(Path("/runs/tree"), dry_run=False)

    assert "--public" not in argv and "--yes" not in argv
    import inspect
    assert "public" not in inspect.signature(jobs.upload_hf_job).parameters


def test_the_mcp_tool_takes_a_name_not_a_path(monkeypatch, tmp_path):
    jobs = _jobs()
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path)
    _tree(tmp_path / "hf-export-1")
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: {"done": True, "argv": list(argv)})

    result = jobs.upload_hf_job("hf-export-1")

    assert result["tree"] == str(tmp_path / "hf-export-1")
    assert result["private"] is True
    with pytest.raises(jobs.JobError):
        jobs.upload_hf_job("../../etc")
    with pytest.raises(jobs.JobError):
        jobs.upload_hf_job("never-exported")


# ── the hand belongs to the letter ───────────────────────────────────────────
#
# The first dry run with images, 2026-10-05, labelled 57 of 211 pages and left
# 154 `unbestimmt`. Not unreadable datelines: a dateline stands on a letter's
# *first* page, so every continuation page is a page the rule cannot read,
# because there is nothing there to read. The hand is a property of the letter.
#
# Which made the grouping key the whole question, and the filenames carry a
# shelfmark that looked like the better one. Measured over the 241 keys of
# `atr_gt_candidates` before it was chosen:
#
#     Basel | 'PA 82a B 9'   -> 36 letters, 115 pages
#     Staatsarchiv Thurgau   -> '…75-1' -> 21 letters
#
# Those shelfmarks name an archival bundle, and Basel is exactly the folder
# holding both sides of the correspondence. The letter folder it is.

APPEAL = ["Hochverehrter Herr Baron,", "mit vielem Dank sende ich"]


def _letter(tmp_path, pages):
    """`(scored, index)` for pages given as `(key, lines)`."""
    scored, index = [], {}
    for i, (key, lines) in enumerate(pages):
        img = _image(tmp_path / "src" / f"p{i}.jpg")
        gt = tmp_path / f"gt{i}.xml"
        gt.write_text(_xml(lines), encoding="utf-8")
        scored.append(_Scored(gt, "\n".join(lines), key))
        index[key] = img
    return scored, index


def test_the_letter_folder_is_the_group():
    g = hf.letter_of("Basel__lassberg-letter-1209__PA 82a B 9_Seite_144")
    assert g.name == "lassberg-letter-1209"
    assert g.basis == "letter"


def test_the_shelfmark_in_the_filename_is_not_the_group():
    """`PA 82a B 9` is 115 Basel pages across 36 letters, and Basel holds both
    sides of the correspondence. Grouping on it would hand 36 letters one hand."""
    a = hf.letter_of("Basel__lassberg-letter-1209__PA 82a B 9_Seite_144")
    b = hf.letter_of("Basel__lassberg-letter-1729__PA 82a B 9_Seite_146")
    assert a.name != b.name


def test_a_folder_below_the_archive_is_the_group_when_there_is_no_letter_id():
    """`blb lassberg__K 2911,104` is a shelfmark folder whose pages are one
    piece — 23 of the 241 candidate pages are shaped like this."""
    g = hf.letter_of("blb lassberg__K 2911,104__page_0001")
    assert g.name == "blb lassberg__K 2911,104"
    assert g.basis == "folder"


def test_a_page_loose_in_an_archive_root_has_no_group():
    """`Winterthur__101-MsBRH_466-56-071` and `…-072` are two shelfmarks side by
    side; grouping on the archive would make the whole of Winterthur one letter."""
    assert not hf.letter_of("Winterthur__101-MsBRH_466-56-071")


def test_a_continuation_page_takes_its_letters_hand(tmp_path):
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__PA 82a B 9_Seite_144", WACKERNAGEL),
        ("Basel__lassberg-letter-1209__PA 82a B 9_Seite_145", APPEAL),
    ])

    p = hf.plan(scored, index)

    assert p.by_project == {"korrespondenten": 2}
    heir = [e for e in p.entries if e.inherited]
    assert len(heir) == 1
    assert "lassberg-letter-1209" in heir[0].evidence
    assert p.hands.decided == 1 and p.hands.inherited == 1 and p.hands.alone == 0


def test_a_page_that_read_its_own_dateline_keeps_it(tmp_path):
    """Two letters in one archive folder: neither takes the other's hand."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
        ("Basel__lassberg-letter-1730__a_Seite_9", LASSBERG),
    ])

    p = hf.plan(scored, index)

    assert p.by_project == {"korrespondenten": 1, "lassberg": 1}
    assert not any(e.inherited for e in p.entries)


def test_a_letter_whose_dated_pages_disagree_inherits_nothing(tmp_path):
    """Two hands under one letter folder is either a folder holding a letter and
    its reply or a misfire of the dateline rule. Averaging them would hide both."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
        ("Basel__lassberg-letter-1209__a_Seite_2", LASSBERG),
        ("Basel__lassberg-letter-1209__a_Seite_3", APPEAL),
    ])

    p = hf.plan(scored, index)

    assert p.by_project["unbestimmt"] == 1
    assert not any(e.inherited for e in p.entries)
    assert p.hands.conflicts and p.hands.conflicts[0][0] == "lassberg-letter-1209"
    text = hf.format_plan(p)
    assert "disagree" in text and "lassberg-letter-1209" in text


def test_an_undated_letter_stays_undetermined(tmp_path):
    """Inheritance adds a hand where one was named. It does not invent one."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", APPEAL),
        ("Basel__lassberg-letter-1209__a_Seite_2", APPEAL),
    ])

    p = hf.plan(scored, index)

    assert p.by_project == {"unbestimmt": 2}
    assert p.hands.alone == 2 and p.hands.inherited == 0


def test_a_letter_decided_by_a_later_page_is_named(tmp_path):
    """A dateline on a continuation page is the shape a misfire has, and
    inheritance spreads it over the whole letter. Inherited, and said out loud."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", APPEAL),
        ("Basel__lassberg-letter-1209__a_Seite_2", WACKERNAGEL),
    ])

    p = hf.plan(scored, index)

    assert p.hands.late == ["lassberg-letter-1209"]
    text = hf.format_plan(p)
    assert "is not their first" in text and "lassberg-letter-1209" in text
    assert all(e.project == "korrespondenten" for e in p.entries)


def test_the_plan_counts_the_three_sources_of_a_hand(tmp_path):
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
        ("Basel__lassberg-letter-1209__a_Seite_2", APPEAL),
        ("Basel__lassberg-letter-1730__a_Seite_9", APPEAL),
    ])

    text = hf.format_plan(hf.plan(scored, index))

    assert "from its own dateline" in text
    assert "inherited from its letter" in text
    assert "no hand" in text


def test_every_page_is_in_the_writers_table_with_its_letter(tmp_path):
    """The plan's counts are a claim about an inference; this is what makes it
    checkable, so it has one row per exported page and names the decider."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
        ("Basel__lassberg-letter-1209__a_Seite_2", APPEAL),
    ])

    rows = hf.writers_table(hf.plan(scored, index)).strip().splitlines()

    assert rows[0].split("\t") == ["key", "letter", "basis", "project",
                                   "source", "evidence", "head"]
    assert len(rows) == 3
    assert [r.split("\t")[4] for r in rows[1:]] == ["dateline", "inherited"]
    assert all("lassberg-letter-1209" in r for r in rows[1:])


# ── measuring the place list instead of guessing at it ───────────────────────
#
# After the inheritance, 100 of 211 pages were still `unbestimmt` — and all of
# them sat in letters where *no* page named a place the rule knows. Whether that
# is because those datelines are unreadable or because nineteen places is a short
# list for a correspondence across half of Europe is not a thing to reason about.
# It is the openings of those letters, side by side.

def test_the_opening_is_the_lines_the_rule_reads():
    assert hf.opening("Basel 30 Augst 33.\nHochgeehrter Herr Baron,\nDer Dank"
                      ) == "Basel 30 Augst 33. / Hochgeehrter Herr Baron,"


def test_the_opening_carries_no_tab_into_a_tsv_column():
    """A dateline with a tab would silently shift every column after it."""
    assert "\t" not in hf.opening("Basel\t30 Augst\nHerr!")


def test_a_long_opening_is_cut_and_says_so():
    head = hf.opening("x" * 400 + "\ny", chars=20)
    assert len(head) == 21 and head.endswith("…")


def test_the_survey_names_one_row_per_undated_letter(tmp_path):
    """The dateline is on a first page, so a letter's other pages add nothing."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", APPEAL),
        ("Basel__lassberg-letter-1209__a_Seite_2", ["mehr Text", "noch mehr"]),
        ("Basel__lassberg-letter-1730__a_Seite_9", WACKERNAGEL),
    ])

    survey = hf.dateline_survey(hf.plan(scored, index))

    assert len(survey) == 1
    assert survey[0][0] == "lassberg-letter-1209"
    assert survey[0][1].startswith(APPEAL[0])


def test_a_dated_letter_is_not_in_the_survey(tmp_path):
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
        ("Basel__lassberg-letter-1209__a_Seite_2", APPEAL),
    ])

    assert hf.dateline_survey(hf.plan(scored, index)) == []


def test_a_page_with_no_letter_is_its_own_row(tmp_path):
    """It is its own first page — `Winterthur__101-MsBRH_466-56-071` inherits
    nothing, so nothing but its own opening can say anything about it."""
    scored, index = _letter(tmp_path, [("Winterthur__101-MsBRH", APPEAL)])

    survey = hf.dateline_survey(hf.plan(scored, index))

    assert survey == [("Winterthur__101-MsBRH", hf.opening("\n".join(APPEAL)))]


def test_the_plan_prints_the_openings(tmp_path):
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", APPEAL),
    ])

    text = hf.format_plan(hf.plan(scored, index))

    assert "1 letter(s) nothing dated" in text
    assert APPEAL[0] in text


def test_the_survey_is_capped_and_counts_the_rest(tmp_path, monkeypatch):
    """A survey the length of the corpus is one nobody reads."""
    monkeypatch.setattr(hf, "SURVEY_SHOWN", 2)
    pages = [(f"Basel__lassberg-letter-1{i:03d}__a_Seite_1", APPEAL)
             for i in range(5)]
    scored, index = _letter(tmp_path, pages)

    text = hf.format_plan(hf.plan(scored, index))

    assert "5 letter(s) nothing dated" in text
    assert "and 3 more" in text


def test_every_pages_opening_is_in_the_writers_table(tmp_path):
    """Including the dated ones: a wrong label is as interesting as a missing
    one, and the column is what makes it checkable."""
    scored, index = _letter(tmp_path, [
        ("Basel__lassberg-letter-1209__a_Seite_1", WACKERNAGEL),
    ])

    rows = hf.writers_table(hf.plan(scored, index)).strip().splitlines()

    assert rows[1].split("\t")[6].startswith(WACKERNAGEL[0])
