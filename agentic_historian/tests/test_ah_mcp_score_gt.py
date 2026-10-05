"""Scoring against ground truth through MCP (`jobs.score_ground_truth`).

Every other number this stack reports is disagreement: who differs from whom,
never who is right. This is the one tool where one side is truth, which makes it
the one worth being able to call from where the decision is made rather than only
over ssh on tei.

It carries the same containment rule as every tool on this path, and one more:
`gt_dir` names a harvest **under `config.GT_ROOT`**, validated exactly like a run
name. A caller chooses which ground truth, never a directory. The server sits
behind a bearer token on the public internet and that validation is what stands
between the token and a box with two A40s.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402

NS = 'xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"'

LETTER_LINES = [
    "Eppishausen am 21 Januar 1831.",
    "Hochgeschäzter Herr!",
    "Da ich das vergnügen nicht haben soll, Sie in meiner Waldklause zu",
    "bewirten; so bliebt mir freilich nichts anders übrig, als Inen die Siegel",
    "die erlaubnis die urkunden länger zu behalten, habe ich zwar selbst anti¬",
]
LETTER = "\n".join(LETTER_LINES)


def _pagexml(lines, *, status="FINAL", doc_id="1682295", page_id="61849396") -> str:
    body = "".join(
        f"<TextLine id='tl_{i}'><TextEquiv><Unicode>{t}</Unicode>"
        f"</TextEquiv></TextLine>" for i, t in enumerate(lines))
    return (
        f"<?xml version='1.0' encoding='UTF-8'?><PcGts {NS}>"
        f"<Metadata><TranskribusMetadata docId='{doc_id}' pageId='{page_id}' "
        f"pageNr='1' status='{status}'/></Metadata>"
        f"<Page imageFilename='{page_id}.tif'><TextRegion id='tr_2'>{body}"
        f"<TextEquiv><Unicode>{chr(10).join(lines)}</Unicode></TextEquiv>"
        f"</TextRegion></Page></PcGts>".replace("'", '"')
    )


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """A VLM_TEST_ROOT with two runs, and a GT_ROOT with one harvest of two pages."""
    runs = tmp_path / "vlm_test"
    runs.mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", runs)

    def write(run, model, pages):
        d = runs / run / model
        d.mkdir(parents=True, exist_ok=True)
        for key, text in pages.items():
            (d / f"{key}.txt").write_text(text, encoding="utf-8")

    write("atr_trocr_corpus", "trocr-kurrent",
          {"Freiburg__p1": LETTER, "Freiburg__p2": "irrelevant " * 30})
    write("atr_corpus_qwen35_line", "qwen3.5-4b-german-xix-v2",
          {"Freiburg__p1": LETTER.replace("Januar", "Januer"),
           "Freiburg__p2": "irrelevant " * 30})

    gt = tmp_path / "gt" / "col36479"
    gt.mkdir(parents=True)
    (gt / "doc1682295_page1_ts1.xml").write_text(
        _pagexml(LETTER_LINES), encoding="utf-8")
    (gt / "doc1682295_page2_ts2.xml").write_text(
        _pagexml(LETTER_LINES, status="DONE", page_id="61849397"), encoding="utf-8")
    monkeypatch.setattr(config, "GT_ROOT", tmp_path / "gt")
    return tmp_path


# ── the containment rule ─────────────────────────────────────────────────────

def test_a_gt_dir_cannot_escape_the_gt_root(stack):
    """The assertion that matters for a tool reachable from the public internet."""
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="../../etc")
    assert "invalid run name" in str(err.value)


def test_an_absolute_gt_dir_is_not_a_name(stack):
    with pytest.raises(jobs.JobError):
        jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="/etc")


def test_a_run_name_cannot_escape_the_run_root(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["../../etc"])
    assert "invalid run name" in str(err.value)


def test_a_gt_dir_that_is_not_there_says_so(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col99999")
    assert "no ground truth at" in str(err.value)


# ── what a caller may ask for ────────────────────────────────────────────────

def test_scoring_needs_at_least_one_run(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth([])
    assert "at least one run" in str(err.value)


def test_the_same_run_twice_is_refused(stack):
    """Its CER against itself is 0 and it would sit in the table looking like the
    best reading there is."""
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["atr_trocr_corpus", "atr_trocr_corpus"])
    assert "given twice" in str(err.value)


def test_too_many_runs_is_refused(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth([f"run{i}" for i in range(jobs.MAX_COMPARE_RUNS + 1)])
    assert "at most" in str(err.value)


# ── the numbers ──────────────────────────────────────────────────────────────

def test_both_runs_are_scored_against_every_page(stack):
    out = jobs.score_ground_truth(
        ["atr_trocr_corpus", "atr_corpus_qwen35_line"], gt_dir="col36479")
    assert out["pages_scored"] == 2
    assert out["ground_truth_dir"].endswith("col36479")
    for page in out["pages"]:
        assert [r["reading"].split("/")[0] for r in page["readings"]] == [
            "atr_trocr_corpus", "atr_corpus_qwen35_line"]


def test_the_exact_reading_scores_zero_and_the_altered_one_does_not(stack):
    out = jobs.score_ground_truth(
        ["atr_trocr_corpus", "atr_corpus_qwen35_line"], gt_dir="col36479")
    by_run = {r["reading"].split("/")[0]: r for r in out["pages"][0]["readings"]}
    assert by_run["atr_trocr_corpus"]["cer"] == 0.0
    assert by_run["atr_corpus_qwen35_line"]["cer"] > 0.0


def test_the_match_carries_its_own_reliability(stack):
    """A best at 0 % against a runner-up at 90 % is a found page. Two at 70 % mean
    the page is not in that run and the number describes nothing."""
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479")
    reading = out["pages"][0]["readings"][0]
    assert reading["key"] == "Freiburg__p1"
    assert reading["reading"] == "atr_trocr_corpus/trocr-kurrent"
    assert reading["confident"] is True
    assert reading["runner_up_cer"] > reading["cer"]


def test_the_status_mix_is_reported(stack):
    """DONE and FINAL are not obviously equally reliable. If the CER splits along
    that line, the status is describing the correction and not the model."""
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479")
    assert out["status_mix"] == {"FINAL": 1, "DONE": 1}


def test_the_default_gt_dir_is_the_whole_root(stack):
    out = jobs.score_ground_truth(["atr_trocr_corpus"])
    assert out["pages_scored"] == 2


def test_limit_takes_the_first_pages(stack):
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479", limit=1)
    assert out["pages_scored"] == 1


def test_an_unusable_file_is_named_rather_than_losing_the_run(stack):
    """The crash of 2026-10-02, over MCP: one empty file must cost that file."""
    (Path(stack) / "gt" / "col36479" / "doc1_page3_ts3.xml").write_text(
        _pagexml([]), encoding="utf-8")
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479")
    assert out["pages_scored"] == 2
    assert [u["file"] for u in out["pages_unusable"]] == ["doc1_page3_ts3.xml"]


def test_the_report_comes_back_with_the_numbers(stack):
    """The structured pages are for a caller that computes; the report is for one
    that reads. Both, because this tool answers a question a human asked."""
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479")
    assert "Readings against ground truth" in out["report_md"]
    assert len(out["report_md"]) <= 40000


# ── the handover: keys_out → keys_from ───────────────────────────────────────
#
# Without it the only run an MCP caller could start was the whole corpus. On
# 2026-10-02 the eight candidates over all 6723 pages came to 53,784 calls and
# about two weeks of one card — to compare the 276 pages that have
# hand-corrected text.
#
# A *name* on both ends, not a path. The file `score_ground_truth` writes is the
# file `start_batch` reads, both under the ground-truth root, so a tool that
# chooses pages never becomes one that chooses files.

def test_the_keys_file_is_written_under_the_gt_root(stack):
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479",
                                  keys_out="gt-keys.txt")

    dest = Path(config.GT_ROOT) / "gt-keys.txt"
    assert dest.is_file()
    # One key, not two: both ground-truth pages of this fixture carry the same
    # text and so locate the same corpus page. That is the real harvest's shape
    # too — Transkribus holds pages under several doc ids — and a key read twice
    # would double that page's weight in every average computed afterwards.
    assert out["keys_file"] == {"name": "gt-keys.txt", "path": str(dest),
                               "pages": 1}


def test_the_written_keys_are_the_located_pages(stack):
    jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479",
                            keys_out="gt-keys.txt")

    import atr_batch as batch
    keys = batch.read_keys(Path(config.GT_ROOT) / "gt-keys.txt")
    assert keys == ["Freiburg__p1"]


def test_no_keys_file_unless_asked(stack):
    out = jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479")
    assert out["keys_file"] is None


def test_a_keys_out_name_cannot_escape_the_root(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479",
                                keys_out="../../etc/passwd")
    assert "invalid run name" in str(err.value)


def test_nothing_located_is_an_error_not_an_empty_file(stack):
    """A key that names the wrong page sends every model to read it. An empty
    list would be better than a wrong one, but saying so is better than both."""
    decoy = "Völlig anderer Text ohne jede Beziehung zu diesem Brief. " * 4
    for model_dir in (Path(config.VLM_TEST_ROOT) / "atr_trocr_corpus").iterdir():
        for txt in model_dir.glob("*.txt"):
            txt.write_text(decoy, encoding="utf-8")

    with pytest.raises(jobs.JobError) as err:
        jobs.score_ground_truth(["atr_trocr_corpus"], gt_dir="col36479",
                                keys_out="gt-keys.txt")
    assert "confidently" in str(err.value)
    assert not (Path(config.GT_ROOT) / "gt-keys.txt").exists()


# ── and reading it back ──────────────────────────────────────────────────────

def test_a_keys_file_is_resolved_by_name(stack):
    (Path(config.GT_ROOT) / "gt-keys.txt").write_text("Freiburg__p1\n",
                                                      encoding="utf-8")
    assert jobs.resolve_keys_file("gt-keys.txt") == \
        Path(config.GT_ROOT) / "gt-keys.txt"


def test_a_keys_name_cannot_escape_the_root(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_keys_file("../../etc/passwd")
    assert "invalid run name" in str(err.value)


def test_an_absolute_keys_path_is_not_a_name(stack):
    with pytest.raises(jobs.JobError):
        jobs.resolve_keys_file("/tmp/gt-keys.txt")


def test_a_missing_keys_file_says_where_one_comes_from(stack):
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_keys_file("nope.txt")
    assert "score_ground_truth" in str(err.value)


def test_the_argv_carries_the_keys_file(stack):
    keys = Path(config.GT_ROOT) / "gt-keys.txt"
    keys.write_text("Freiburg__p1\n", encoding="utf-8")
    argv = jobs.batch_argv(Path("/corpus"), ["trocr-kurrent"], "r",
                           keys_from=jobs.resolve_keys_file("gt-keys.txt"))
    assert "--keys-from" in argv
    assert argv[argv.index("--keys-from") + 1] == str(keys)


def test_no_keys_flag_without_a_keys_file(stack):
    argv = jobs.batch_argv(Path("/corpus"), ["trocr-kurrent"], "r")
    assert "--keys-from" not in argv


# ── scoring is a job, because it takes a quarter of an hour ──────────────────
#
# 2026-10-05: this tool ran the scoring *inside* the MCP server. The broker cut
# the request at 60 s, the work carried on to the end, and the result reached
# nobody — the exact failure `share_list`'s docstring already describes, in a
# second place.

def test_the_argv_is_the_documented_cli():
    """So the MCP path and the terminal path cannot drift, and a job that
    misbehaves can be reproduced by a person pasting the line."""
    argv = jobs.score_argv([Path("/runs/a"), Path("/runs/b")],
                           gt_dir=Path("/gt"), limit=5,
                           keys_out=Path("/gt/keys.txt"),
                           out=Path("/out/report.md"))

    assert argv[1:4] == ["-m", "agentic_historian", "score-gt"]
    assert argv.count("--run-dir") == 2
    assert argv[argv.index("--gt") + 1] == "/gt"
    assert argv[argv.index("--limit") + 1] == "5"
    assert argv[argv.index("--keys-out") + 1] == "/gt/keys.txt"
    assert argv[argv.index("--out") + 1] == "/out/report.md"


def test_the_optional_arguments_are_left_out_when_unset():
    argv = jobs.score_argv([Path("/runs/a")])

    for flag in ("--gt", "--limit", "--keys-out", "--out"):
        assert flag not in argv


def test_the_job_validates_what_the_in_process_version_validated(monkeypatch,
                                                                 tmp_path):
    """Same names, same containment: a caller still cannot reach outside the
    ground-truth root or score the same run twice."""
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)

    with pytest.raises(jobs.JobError):
        jobs._score_run_names([])
    with pytest.raises(jobs.JobError) as err:
        jobs._score_run_names(["a", "a"])
    assert "twice" in str(err.value)
    with pytest.raises(jobs.JobError):
        jobs._score_gt_dir("../../etc")

    assert jobs._score_gt_dir(None) == tmp_path


def test_the_report_goes_to_a_file_so_the_log_tail_is_the_answer(monkeypatch,
                                                                tmp_path):
    """`job_log` returns a *tail*. The per-page sections are thousands of lines,
    so the summary has to be last — which is what `--out` arranges."""
    monkeypatch.setattr(jobs.config, "GT_ROOT", tmp_path)
    monkeypatch.setattr(jobs.config, "VLM_TEST_ROOT", tmp_path / "runs")
    (tmp_path / "runs" / "atr_gt_candidates").mkdir(parents=True)
    seen = {}

    def _peek(kind, argv, **kw):
        seen["kind"], seen["argv"] = kind, list(argv)
        return {"done": False, "job_id": "j1", "state": "running"}

    monkeypatch.setattr(jobs, "start_and_peek", _peek)
    result = jobs.score_job(["atr_gt_candidates"])

    assert seen["kind"] == "score-gt"
    assert "--out" in seen["argv"]
    assert result["report"].endswith(".md")
    assert result["job_id"] == "j1"
