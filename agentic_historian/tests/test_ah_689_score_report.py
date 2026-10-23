"""#689 — the scoring report as data, readable from a session.

A scoring of more than a few pages runs as a job, and a job hands back the tail
of its log: the comparison table, nothing else. The report itself — 484 lines on
2026-10-22 — held what the next call needed (which three of 38 pages were not
located, the CER per page, whether the error splits along DONE/FINAL) and could
not be opened from a session. The CLI now writes the same report as JSON beside
`--out`, the synchronous MCP path returns the same shape, and `score_report`
pages through it.
"""

import json
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

PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">
  <Metadata><TranskribusMetadata docId="{doc}" pageId="{pg}" status="{status}"/></Metadata>
  <Page imageFilename="{pg}.tif" imageWidth="100" imageHeight="100">
    <TextRegion id="r1"><Coords points="0,0 1,0 1,1 0,1"/>
      <TextEquiv><Unicode>{text}</Unicode></TextEquiv>
    </TextRegion>
  </Page>
</PcGts>
"""

A = ("Hochwohlgeborner Herr, Ihr Schreiben vom dritten dieses Monats habe ich "
     "mit grosser Freude erhalten und beantworte es sogleich.")
B = ("Die Handschrift aus dem Kloster Reichenau enthaelt eine Abschrift des "
     "Nibelungenliedes, welche ich Ihnen gerne zur Ansicht uebersende.")


def _scored(file, status, key, cer, runner_up=0.9, doc="1", text=A):
    gt = gt_score.PageGT(text=text, source=Path(file), doc_id=doc, page_id="7",
                         status=status, lines=3)
    m = gt_score.Match(reading="r/m", key=key, cer=cer,
                       runner_up_key="other" if runner_up else "",
                       runner_up_cer=runner_up)
    return gt_score.Scored(gt=gt, matches=[m])


# ── the data ─────────────────────────────────────────────────────────────────

def test_report_data_carries_the_table_and_every_page():
    scored = [_scored("doc1__001.xml", "FINAL", "A__001", 0.05),
              _scored("doc1__002.xml", "DONE", "A__002", 0.20),
              _scored("doc2__001.xml", "FINAL", "B__001", 0.60, runner_up=0.62)]
    unusable = [gt_score.Unusable(source=Path("doc3__001.xml"), reason="no text")]

    data = gt_score.report_data(scored, unusable)

    assert data["pages_scored"] == 3
    assert data["pages_unusable"] == [{"file": "doc3__001.xml", "reason": "no text"}]
    assert data["status_mix"] == {"FINAL": 2, "DONE": 1}
    assert [r["reading"] for r in data["by_reading"]] == ["r/m"]
    assert data["by_reading"][0]["pages"] == 2, "the unlocated page is in no row"
    files = [p["file"] for p in data["pages"]]
    assert files == ["doc1__001.xml", "doc1__002.xml", "doc2__001.xml"]
    third = data["pages"][2]
    assert third["located_key"] == "" and third["agreed_key"] == "B__001"
    assert third["readings"][0]["confident"] is False
    assert third["readings"][0]["runner_up_key"] == "other"
    assert data["pages"][0]["located_key"] == "A__001"


def test_the_synchronous_mcp_path_returns_the_same_shape(monkeypatch, tmp_path):
    """One shape whether the scoring ran inside the server or as a job."""
    run = tmp_path / "runs" / "r" / "m"
    run.mkdir(parents=True)
    (run / "A__001.txt").write_text(A, encoding="utf-8")
    (run / "B__001.txt").write_text(B, encoding="utf-8")
    gt = tmp_path / "gt" / "set"
    gt.mkdir(parents=True)
    (gt / "doc1__001.xml").write_text(
        PAGE.format(doc="1", pg="1", status="FINAL", text=A), encoding="utf-8")
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "runs")
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")

    result = jobs.score_ground_truth(["r"], gt_dir="set")

    expected = set(gt_score.report_data([], []).keys())
    assert expected <= set(result)
    assert result["pages"][0]["located_key"] == "A__001"
    assert result["by_reading"][0]["pages"] == 1


# ── the CLI writes it beside the Markdown ────────────────────────────────────

def _cli():
    """The CLI module, loaded from its file so no `-m` machinery runs."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ah_main_689", PKG / "__main__.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_cli_writes_the_json_beside_out(monkeypatch, tmp_path, capsys):
    cli = _cli()
    run = tmp_path / "runs" / "r" / "m"
    run.mkdir(parents=True)
    (run / "A__001.txt").write_text(A, encoding="utf-8")
    (run / "B__001.txt").write_text(B, encoding="utf-8")
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "doc1__001.xml").write_text(
        PAGE.format(doc="1", pg="1", status="FINAL", text=A), encoding="utf-8")
    (gt / "doc2__001.xml").write_text(
        PAGE.format(doc="2", pg="1", status="DONE", text=B), encoding="utf-8")
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "runs")
    out = tmp_path / "runs" / "score-gt-20261022T164235.md"
    args = SimpleNamespace(gt=[str(gt)], run_dir=["r"], out=str(out), limit=None,
                           keys_out=None, writer_agreement=False)

    assert cli.score_gt(args) == 0

    data = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert data["pages_scored"] == 2 and len(data["pages"]) == 2
    assert data["runs"] == [str(tmp_path / "runs" / "r")]
    assert data["written_at"]
    assert "report json:" in capsys.readouterr().out
    assert out.is_file(), "the Markdown is still written for the person"


# ── score_report ─────────────────────────────────────────────────────────────

def _write_report(root, stem="score-gt-20261022T164235", n=38, unlocated=(5, 17, 30)):
    pages = []
    for i in range(1, n + 1):
        located = i not in unlocated
        pages.append({"file": f"doc{i}__001.xml", "doc_id": str(i), "page_id": "p",
                      "status": "FINAL" if i % 10 else "DONE", "lines": 3,
                      "chars": 100, "agreed_key": f"K{i}",
                      "located_key": f"K{i}" if located else "",
                      "readings": [{"reading": "g/m", "cer": 0.05, "key": f"K{i}",
                                    "runner_up_key": "x", "runner_up_cer": 0.9,
                                    "confident": located}]})
    data = {"runs": ["g"], "ground_truth": ["gt"], "written_at": "t",
            "pages_scored": n, "pages_unusable": [], "status_mix": {"FINAL": 34, "DONE": 4},
            "by_reading": [{"reading": "g/m", "pages": n - len(unlocated),
                            "median": 0.048, "p90": 0.128, "best": 0.007, "worst": 0.171}],
            "pages": pages}
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{stem}.md").write_text("# report\n", encoding="utf-8")
    (root / f"{stem}.json").write_text(json.dumps(data), encoding="utf-8")
    return stem


def test_the_report_names_the_unlocated_pages_and_pages_through(monkeypatch, tmp_path):
    """The 2026-10-22 question: which three of 38, and what each page scored."""
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    stem = _write_report(tmp_path)

    first = jobs.score_report(stem, limit=10)

    assert first["ok"] and first["name"] == stem
    assert first["not_located"] == ["doc5__001.xml", "doc17__001.xml", "doc30__001.xml"]
    assert first["by_reading"][0]["median"] == 0.048
    assert first["status_mix"] == {"FINAL": 34, "DONE": 4}
    assert first["pages_total"] == 38 and first["returned"] == 10
    assert first["remaining"] == 28 and first["next_offset"] == 10
    assert first["pages"][0]["file"] == "doc1__001.xml"

    last = jobs.score_report(stem, offset=30, limit=10)
    assert last["returned"] == 8 and last["remaining"] == 0
    assert last["next_offset"] is None


def test_located_only_is_the_view_in_which_cers_mean_something(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    stem = _write_report(tmp_path)
    result = jobs.score_report(stem, limit=100, located_only=True)
    assert result["pages_total"] == 35 and result["returned"] == 35
    assert all(p["located_key"] for p in result["pages"])
    assert len(result["not_located"]) == 3, "still named, so nothing is hidden"


def test_the_name_tolerates_the_path_the_job_printed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    stem = _write_report(tmp_path)
    assert jobs.score_report(stem + ".md")["name"] == stem
    assert jobs.score_report(stem + ".json")["name"] == stem


def test_the_name_is_a_name_not_a_path(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    with pytest.raises(jobs.JobError):
        jobs.score_report("../../etc/passwd")
    with pytest.raises(jobs.JobError):
        jobs.score_report("/etc/passwd.json")


def test_a_report_from_before_says_it_has_no_data(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path)
    (tmp_path / "score-gt-20261005T120000.md").write_text("# old\n", encoding="utf-8")
    with pytest.raises(jobs.JobError) as err:
        jobs.score_report("score-gt-20261005T120000")
    assert "#689" in str(err.value) and "again" in str(err.value)
    with pytest.raises(jobs.JobError) as err:
        jobs.score_report("score-gt-19991231T000000")
    assert "no scoring report" in str(err.value)


def test_the_scoring_job_names_the_data_it_will_write(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    monkeypatch.setattr(config, "VLM_TEST_ROOT", tmp_path / "runs")
    (tmp_path / "gt").mkdir()
    (tmp_path / "runs" / "r").mkdir(parents=True)
    seen = {}
    monkeypatch.setattr(jobs, "start_and_peek",
                        lambda kind, argv, **kw: seen.update(argv=list(argv)) or {})

    result = jobs.score_job(["r"])

    assert result["report_json"] == result["report"].replace(".md", ".json")
    assert result["report_name"].startswith("score-gt-")
    assert seen["argv"][seen["argv"].index("--out") + 1] == result["report"]


def test_the_tool_is_on_the_server():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    assert "def score_report(" in src
