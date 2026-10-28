"""#758 — a page with a name is scored as that page, however badly it was read.

On 2026-10-27 `score_ground_truth` over `korrespondenten_eval` reported a 14.0 %
median for kraken; over all 22 pages it was 23.0 %. Six pages had dropped out as
"not located": the tables and lists where kraken's segmentation fell apart, read
so badly that the content search could not tell them from any other page. In a
dataset project the identity is not in question — `page/<stem>.xml` is the
reading stored under `<stem>` — so the name decides, and the page stays in every
row with its real error.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
import gt_score                            # noqa: E402
from mcp_atr import jobs                   # noqa: E402

NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"

A = ("Hochwohlgeborner Herr, Ihr Schreiben vom dritten dieses Monats habe ich "
     "mit grosser Freude erhalten und beantworte es sogleich.")
B = ("Die Handschrift aus dem Kloster Reichenau enthaelt eine Abschrift des "
     "Nibelungenliedes, welche ich Ihnen gerne zur Ansicht uebersende.")
# A table, and kraken's reading of it: nothing in common with the page.
TABLE = ("Ellwangen zwölf Gulden zwanzig Kreuzer, Waldkirch acht Gulden, Bündten drei "
         "Gulden vierzig Kreuzer, Pündten und Lervgsyphien je ein Gulden.")
GARBLED = ("Hopg grp bul ve dhr uneo uus meuvut omur vsu mujoysus Dueg Sev Mnocs vuzg "
           "som elehs vels Hrf oge oog nem au Hoz doro Urther resbe Torg")


def _page(stem: str, text: str, image: str = "") -> str:
    body = "\n".join(f"<TextLine id='l{i}'><TextEquiv><Unicode>{t}</Unicode>"
                     f"</TextEquiv></TextLine>" for i, t in enumerate(text.splitlines()))
    return (f'<?xml version="1.0" encoding="UTF-8"?><PcGts xmlns="{NS}">'
            f'<Page imageFilename="{image or stem + ".jpg"}" imageWidth="1" '
            f'imageHeight="1"><TextRegion id="r">{body}<TextEquiv><Unicode>{text}'
            f'</Unicode></TextEquiv></TextRegion></Page></PcGts>')


@pytest.fixture
def project(tmp_path):
    """A dataset project with three pages, and a run that read them."""
    gt = tmp_path / "lassberg_eval" / "page"
    gt.mkdir(parents=True)
    for stem, text in (("K1_0001", A), ("K1_0002", B), ("K2_0001", TABLE)):
        (gt / f"{stem}.xml").write_text(_page(stem, text), encoding="utf-8")
    run = tmp_path / "runs" / "blb-eval" / "kraken"
    run.mkdir(parents=True)
    (run / "K1_0001.txt").write_text(A.replace("Herr", "Hen"), encoding="utf-8")
    (run / "K1_0002.txt").write_text(B, encoding="utf-8")
    (run / "K2_0001.txt").write_text(GARBLED, encoding="utf-8")
    return gt, run.parent


def _score(project, match):
    gt, run = project
    scored, unusable, report = gt_score.score_run_dirs(
        sorted(gt.glob("*.xml")), [run], match=match)
    return scored, report


# ── the failure stays in the row ─────────────────────────────────────────────

def test_by_content_the_unreadable_page_drops_out(project):
    """What happened on 27.10., kept as the reason for the change."""
    scored, _ = _score(project, "content")
    row = gt_score.score_by_reading(scored)[0]
    table = next(s for s in scored if s.gt.source.stem == "K2_0001")
    # As with K2911-103_0004: the garbled reading is no nearer to its own page
    # than another reading is, so content places it nowhere and the row
    # silently loses its worst page.
    assert table.located == "", "the garbled table is not placed confidently"
    assert row.pages == 2 and row.worst < 0.1


@pytest.mark.parametrize("match", ["auto", "name"])
def test_by_name_every_page_is_in_the_row_with_its_own_error(project, match):
    scored, _ = _score(project, match)
    row = gt_score.score_by_reading(scored)[0]
    assert row.pages == 3 and row.by_name == 3 and row.missing == 0
    table = next(s for s in scored if s.gt.source.stem == "K2_0001")
    m = table.matches[0]
    assert table.located == "K2_0001" and m.by_name and m.confident
    assert m.cer > 0.6, "the error is the reading's error on this page"
    assert row.worst == pytest.approx(m.cer)


def test_a_reading_nearer_to_another_page_is_named_not_moved(project):
    gt, run = project
    # The reading of K1_0002 is in fact K1_0001's text: content would move it.
    (run / "kraken" / "K1_0002.txt").write_text(A, encoding="utf-8")
    scored, report = _score(project, "name")
    page = next(s for s in scored if s.gt.source.stem == "K1_0002")
    m = page.matches[0]
    assert m.key == "K1_0002" and m.content_key == "K1_0001"
    assert "closer in content to another page" in report


# ── a gap is a gap ───────────────────────────────────────────────────────────

def test_with_match_name_a_missing_reading_is_reported_not_dropped(project):
    gt, run = project
    (run / "kraken" / "K2_0001.txt").unlink()
    scored, report = _score(project, "name")
    row = gt_score.score_by_reading(scored)[0]
    assert row.pages == 2 and row.missing == 1
    data = gt_score.report_data(scored, [])
    assert data["missing_reading"] == [{"file": "K2_0001.xml",
                                        "reading": "blb-eval/kraken"}]
    assert data["by_reading"][0]["missing"] == 1
    assert "no page under the ground truth's name" in report


def test_auto_without_the_page_falls_back_to_content(project):
    gt, run = project
    (run / "kraken" / "K2_0001.txt").unlink()
    scored, _ = _score(project, "auto")
    table = next(s for s in scored if s.gt.source.stem == "K2_0001")
    assert not table.matches[0].by_name and not table.matches[0].missing


# ── Transkribus harvests are untouched ───────────────────────────────────────

def test_a_transkribus_name_matches_no_key_and_content_decides(tmp_path):
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "doc1__001.xml").write_text(_page("x", A, image="61849396.tif"),
                                      encoding="utf-8")
    run = tmp_path / "runs" / "r" / "m"
    run.mkdir(parents=True)
    (run / "done_K 2911,17__001.txt").write_text(A, encoding="utf-8")
    (run / "other.txt").write_text(B, encoding="utf-8")
    scored, _, _ = gt_score.score_run_dirs([gt / "doc1__001.xml"], [run.parent])
    m = scored[0].matches[0]
    assert not m.by_name and m.key == "done_K 2911,17__001" and m.confident


def test_the_image_filename_is_a_name_too(tmp_path):
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "renamed.xml").write_text(_page("x", A, image="K9_0003.jpg"),
                                    encoding="utf-8")
    run = tmp_path / "runs" / "r" / "m"
    run.mkdir(parents=True)
    (run / "K9_0003.txt").write_text(GARBLED, encoding="utf-8")
    (run / "K9_0004.txt").write_text(A, encoding="utf-8")
    scored, _, _ = gt_score.score_run_dirs([gt / "renamed.xml"], [run.parent])
    m = scored[0].matches[0]
    assert m.by_name and m.key == "K9_0003" and m.content_key == "K9_0004"


# ── the weighted figure ──────────────────────────────────────────────────────

def test_weighted_cer_is_errors_over_reference_characters():
    def item(name, text, cer):
        gt = gt_score.PageGT(text=text, source=Path(name))
        return gt_score.Scored(gt=gt, matches=[gt_score.Match(
            reading="r", key=Path(name).stem, cer=cer, by_name=True)])
    scored = [item("a.xml", "x" * 900, 0.05), item("b.xml", "x" * 100, 0.85)]
    row = gt_score.score_by_reading(scored)[0]
    assert row.median == pytest.approx(0.45)
    assert row.weighted == pytest.approx((900 * 0.05 + 100 * 0.85) / 1000)
    assert "weighted CER" in gt_score.format_summary(scored)


# ── the wiring ───────────────────────────────────────────────────────────────

def test_a_dataset_ground_truth_is_scored_by_name(tmp_path, monkeypatch):
    datasets = tmp_path / "datasets"
    (datasets / "lassberg-blb-gt" / "lassberg_eval" / "page").mkdir(parents=True)
    (datasets / "lassberg-blb-gt" / "export.json").write_text("{}", encoding="utf-8")
    (tmp_path / "vlm_test" / "blb-eval-lassberg").mkdir(parents=True)
    monkeypatch.setattr(config, "ATR_DATASETS_DIR", datasets)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "vlm_test")
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.setdefault("argv", argv) and {})
    jobs.score_job(["blb-eval-lassberg"],
                   gt_dir="datasets:lassberg-blb-gt/lassberg_eval")
    argv = seen["argv"]
    assert argv[argv.index("--match") + 1] == "name"


def test_a_harvest_under_gt_root_keeps_the_default(tmp_path, monkeypatch):
    (tmp_path / "gt" / "set").mkdir(parents=True)
    (tmp_path / "vlm_test" / "r").mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "vlm_test")
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.setdefault("argv", argv) and {})
    jobs.score_job(["r"], gt_dir="set")
    assert "--match" not in seen["argv"]


def test_the_cli_writes_how_each_page_was_matched(project, tmp_path, monkeypatch):
    import importlib.util
    import json
    spec = importlib.util.spec_from_file_location("ah_main_758", PKG / "__main__.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    gt, run = project
    monkeypatch.setattr(config, "VLM_TEST_ROOT", run.parent)
    out = run.parent / "score-gt-758.md"
    args = SimpleNamespace(gt=[str(gt)], run_dir=[run.name], out=str(out),
                           limit=None, keys_out=None, writer_agreement=False,
                           match="name")
    assert cli.score_gt(args) == 0
    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert {r["match"] for p in data["pages"] for r in p["readings"]} == {"by_name"}
    assert data["by_reading"][0]["pages"] == 3
    assert "weighted" in data["by_reading"][0] and data["missing_reading"] == []
