"""Comparing two runs' readings through MCP (`jobs.compare_readings`).

The comparison is a CLI command on tei, which meant the one step of this workflow
that produces a decision could not be driven from where the decision is made.
This is the tool that closes that, and it carries the same rule as every other
tool on this path: a remote caller chooses *which* runs, never a directory. The
server sits behind a bearer token on the public internet, and the validation here
is what stands between that token and the box.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402

LETTER = ("Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag ertheilt, mich "
          "für ein vollständiges Exemplar des Blattes umzusehen, das zu den "
          "Seltenheiten gehören mag.")


@pytest.fixture
def runs(tmp_path, monkeypatch):
    """A VLM_TEST_ROOT with two runs of the same two pages."""
    root = tmp_path / "vlm_test"
    root.mkdir(parents=True)
    monkeypatch.setattr(config, "VLM_TEST_ROOT", root)

    def write(run, model, pages):
        d = root / run / model
        d.mkdir(parents=True, exist_ok=True)
        for key, text in pages.items():
            (d / f"{key}.txt").write_text(text, encoding="utf-8")

    write("atr_trocr_corpus", "trocr-kurrent",
          {"Aarau__p1": LETTER, "Aarau__p2": "den |"})
    write("atr_corpus_qwen35_line", "qwen3.5-4b-german-xix-v2",
          {"Aarau__p1": LETTER.replace("ertheilt", "entheilt"),
           "Aarau__p2": "der 1850"})
    return root


# ── the containment rule ─────────────────────────────────────────────────────

def test_a_run_name_cannot_escape_the_root(runs):
    """The one assertion that matters for a tool on the public internet."""
    with pytest.raises(jobs.JobError) as err:
        jobs.compare_readings(["../../etc", "atr_trocr_corpus"])
    assert "invalid run name" in str(err.value)


def test_an_absolute_path_is_not_a_run_name(runs):
    with pytest.raises(jobs.JobError):
        jobs.compare_readings(["/etc/passwd", "atr_trocr_corpus"])


def test_a_run_that_does_not_exist_says_so(runs):
    with pytest.raises(jobs.JobError) as err:
        jobs.compare_readings(["atr_trocr_corpus", "no_such_run"])
    assert "no run directory" in str(err.value)


# ── the arguments ────────────────────────────────────────────────────────────

def test_one_run_is_not_a_comparison(runs):
    with pytest.raises(jobs.JobError):
        jobs.compare_readings(["atr_trocr_corpus"])


def test_the_same_run_twice_is_refused(runs):
    """It would report a run agreeing perfectly with itself, which is true and
    useless, and would read as evidence that the engines agree."""
    with pytest.raises(jobs.JobError) as err:
        jobs.compare_readings(["atr_trocr_corpus", "atr_trocr_corpus"])
    assert "twice" in str(err.value)


def test_too_many_runs_are_refused(runs):
    """Every pair is measured, so the work grows quadratically."""
    with pytest.raises(jobs.JobError) as err:
        jobs.compare_readings(["atr_trocr_corpus"] + [f"r{i}" for i in range(9)])
    assert "at most" in str(err.value)


def test_worst_is_clamped_not_trusted(runs):
    out = jobs.compare_readings(["atr_trocr_corpus", "atr_corpus_qwen35_line"],
                                worst=10_000)
    assert len(out["pairs"][0]["worst"]) <= 50


# ── the answer ───────────────────────────────────────────────────────────────

def test_it_returns_the_numbers_and_the_report(runs):
    """Structured for a caller to reason with, markdown for a human to read —
    both, because re-deriving one from the other is how they drift apart."""
    out = jobs.compare_readings(["atr_trocr_corpus", "atr_corpus_qwen35_line"])

    assert out["runs"] == ["atr_trocr_corpus", "atr_corpus_qwen35_line"]
    assert out["common_pages"] == 2
    assert out["compared_pages"] == 1          # the fragment pair is too short
    assert out["too_short"] == 1
    assert [r["label"] for r in out["readings"]] == [
        "atr_trocr_corpus/trocr-kurrent",
        "atr_corpus_qwen35_line/qwen3.5-4b-german-xix-v2",
    ]
    pair = out["pairs"][0]
    assert pair["pages"] == 1
    assert 0.0 < pair["median_disagreement"] < 0.05
    assert pair["above_no_merge"] == 0
    assert "# Reading comparison" in out["report_md"]
    assert "not quality" in out["report_md"]


def test_min_chars_can_pull_the_fragments_back_in(runs):
    out = jobs.compare_readings(["atr_trocr_corpus", "atr_corpus_qwen35_line"],
                                min_chars=1)
    assert out["compared_pages"] == 2 and out["too_short"] == 0


def test_the_report_is_capped(runs, monkeypatch):
    """`batch_report` caps at the same size for the same reason: an MCP reply that
    carries a corpus is a reply nobody can read."""
    import compare_runs

    monkeypatch.setattr(compare_runs, "format_report", lambda c: "x" * 60_000)
    out = jobs.compare_readings(["atr_trocr_corpus", "atr_corpus_qwen35_line"])
    assert len(out["report_md"]) == 40_000
