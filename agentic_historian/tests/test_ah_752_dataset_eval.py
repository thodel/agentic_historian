"""#752 — a dataset's held-out pages are read and scored in place.

On 2026-10-27 `kraken-german-xix-blb-v1` was trained on `lassberg-blb-gt` and
the pages to measure it on — the `*_eval` projects of that tree — sat under
ATR_DATASETS_DIR, which neither `start_batch` (sources: staging, runs, mount,
page cache) nor `score_ground_truth` (ground truth: under GT_ROOT) could name.

`datasets:<tree>/<project>` is both: the project's images as a source, its
`page/` PAGE XML as the reference. Each part passes the run-name check.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import gt_score                            # noqa: E402
from mcp_atr import jobs                   # noqa: E402

NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"

# What `oai_gt` writes: no TranskribusMetadata, a region TextEquiv per region.
BLB_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="{NS}">
  <Metadata><Creator>agentic_historian oai_gt (ALTO→PAGE)</Creator></Metadata>
  <Page imageFilename="K2911-17_0001.jpg" imageWidth="100" imageHeight="100">
    <TextRegion id="r1" type="paragraph">
      <Coords points="0,0 100,0 100,100 0,100"/>
      <TextLine id="l1"><Coords points="0,0 100,0 100,10 0,10"/>
        <TextEquiv><Unicode>Hochgeehrter Herr</Unicode></TextEquiv></TextLine>
      <TextLine id="l2"><Coords points="0,10 100,10 100,20 0,20"/>
        <TextEquiv><Unicode>und Freund</Unicode></TextEquiv></TextLine>
      <TextEquiv><Unicode>Hochgeehrter Herr
und Freund</Unicode></TextEquiv>
    </TextRegion>
  </Page>
</PcGts>
"""


@pytest.fixture
def datasets(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    tree = root / "lassberg-blb-gt"
    for project, stems in {"lassberg": ["K2911-47_0001"],
                           "lassberg_eval": ["K2911-17_0001", "K2911-17_0002"]}.items():
        (tree / project / "page").mkdir(parents=True)
        for stem in stems:
            (tree / project / f"{stem}.jpg").write_bytes(b"\xff\xd8")
            (tree / project / "page" / f"{stem}.xml").write_text(BLB_XML, encoding="utf-8")
    (tree / "export.json").write_text('{"kind": "ground-truth"}', encoding="utf-8")
    (root / "_hf_scratch").mkdir()
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", root)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "vlm_test")
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    (tmp_path / "vlm_test").mkdir()
    (tmp_path / "gt").mkdir()
    return tree


# ── one name, two uses ───────────────────────────────────────────────────────

def test_a_project_name_is_a_batch_source(datasets):
    assert jobs.resolve_source("datasets:lassberg-blb-gt/lassberg_eval") \
        == datasets / "lassberg_eval"


def test_the_same_name_is_the_ground_truth(datasets):
    assert jobs._score_gt_dir("datasets:lassberg-blb-gt/lassberg_eval") \
        == datasets / "lassberg_eval"


def test_the_tree_alone_is_the_whole_tree(datasets):
    assert jobs.resolve_source("datasets:lassberg-blb-gt") == datasets


@pytest.mark.parametrize("bad", [
    "datasets:../etc", "datasets:lassberg-blb-gt/../lassberg",
    "datasets:lassberg-blb-gt/lassberg_eval/page", "datasets:lassberg-blb-gt/",
    "datasets:/etc/passwd",
])
def test_a_name_never_becomes_a_path(datasets, bad):
    if bad.endswith("/"):
        # an empty project is the tree, which is a directory and allowed
        assert jobs.resolve_source(bad) == datasets
        return
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(bad)


def test_an_unknown_project_names_the_ones_there_are(datasets):
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_source("datasets:lassberg-blb-gt/manuskripte_eval")
    msg = str(err.value)
    assert "lassberg_eval" in msg and "lassberg," in msg


def test_without_the_setting_the_name_is_refused(datasets, monkeypatch):
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", None)
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_source("datasets:lassberg-blb-gt/lassberg_eval")
    assert "ATR_DATASETS_DIR is not configured" in str(err.value)


# ── what each side then sees ────────────────────────────────────────────────

def test_the_batch_finds_the_images_and_not_the_xml(datasets):
    import atr_batch
    pages = atr_batch.discover_pages(jobs.resolve_source(
        "datasets:lassberg-blb-gt/lassberg_eval"))
    keys = sorted(str(getattr(p, "key", p)) for p in pages)
    # keys are the stems, the same stems the PAGE XML under page/ carries
    assert keys == ["K2911-17_0001", "K2911-17_0002"]


def test_a_dataset_source_gets_no_working_copy_cache(datasets):
    # A copy would land under the page cache keyed by the project's relative
    # path, beside the share's keys; two reads over CIFS are the cheaper cost.
    assert jobs.cache_dir_for(datasets / "lassberg_eval") is None


def test_the_reference_is_the_page_xml_and_it_counts_without_a_status(datasets):
    files = gt_score.expand_gt_paths([str(jobs._score_gt_dir(
        "datasets:lassberg-blb-gt/lassberg_eval"))])
    assert [f.parent.name for f in files] == ["page", "page"]
    gt = gt_score.read_pagexml(files[0])
    assert gt.text == "Hochgeehrter Herr\nund Freund"
    assert gt.status == "" and gt.image == "K2911-17_0001.jpg"


def test_the_scoring_job_hands_the_cli_the_project(datasets, monkeypatch):
    run = Path(config.VLM_TEST_ROOT) / "blb-eval-kraken"
    run.mkdir()
    seen = {}

    def _peek(kind, argv, **kw):
        seen["argv"] = argv
        return {"done": False, "job_id": "x"}

    monkeypatch.setattr(jobs, "start_and_peek", _peek)
    out = jobs.score_job(["blb-eval-kraken"],
                         gt_dir="datasets:lassberg-blb-gt/lassberg_eval")
    argv = seen["argv"]
    assert argv[argv.index("--gt") + 1] == str(datasets / "lassberg_eval")
    assert out["ground_truth_dir"] == str(datasets / "lassberg_eval")
