"""Line-level accuracy against real ground truth (Zenodo 4746342).

Everything this project measured about quality was relative — pairwise CER,
agreement with a historian's pick, engine strength from preferences — because a
Gate-2 selection is the closest available reading and not truth (#326). This
corpus supplies the missing reference, and with it #300's premise, #406's cost
question and #313's thesis become arithmetic.

Offline — no engines. Run from the repo root:
    pytest agentic_historian/tests/test_ah_linebench.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval.linebench import Line, ModelScore, format_scores, load, score_model  # noqa: E402


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / "lines").mkdir()
    rows = [
        {"image": "a.jpg", "gt": "der Bundesrat beschliesst", "doc": "1"},
        {"image": "b.jpg", "gt": "2100.", "doc": "1"},
        {"image": "c.jpg", "gt": "zur Verlängerung der Konzession", "doc": "2"},
    ]
    for r in rows:
        (tmp_path / "lines" / r["image"]).write_bytes(b"\xff\xd8\xff")
    (tmp_path / "manifest.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return tmp_path


# ── loading ──────────────────────────────────────────────────────────────────

def test_the_manifest_loads(corpus):
    lines = load(corpus)
    assert len(lines) == 3
    assert lines[0].gt == "der Bundesrat beschliesst"


def test_a_line_without_its_image_is_skipped(corpus):
    (corpus / "lines" / "b.jpg").unlink()
    assert {l.image.name for l in load(corpus)} == {"a.jpg", "c.jpg"}


def test_an_empty_reference_is_skipped(corpus):
    """It says nothing about a model, so it must not enter the denominator."""
    (corpus / "manifest.jsonl").write_text(
        json.dumps({"image": "a.jpg", "gt": "   ", "doc": "1"}), encoding="utf-8")
    assert load(corpus) == []


def test_a_corrupt_line_does_not_lose_the_rest(corpus):
    text = (corpus / "manifest.jsonl").read_text(encoding="utf-8")
    (corpus / "manifest.jsonl").write_text("{not json\n" + text, encoding="utf-8")
    assert len(load(corpus)) == 3


def test_a_missing_manifest_is_an_error_not_an_empty_corpus(tmp_path):
    """Silently scoring zero lines would report a perfect run on no data."""
    with pytest.raises(FileNotFoundError):
        load(tmp_path)


# ── scoring ──────────────────────────────────────────────────────────────────

def test_a_perfect_recogniser_scores_zero(corpus):
    lines = load(corpus)
    by_name = {l.image.name: l.gt for l in lines}
    s = score_model(lines, lambda p: by_name[p.name], model="perfect")
    assert s.cer == 0.0 and s.lines == 3 and s.failures == 0


def test_cer_is_corpus_level_not_a_mean_of_lines(corpus):
    """A 5-character line must not weigh as much as a 30-character one — this
    corpus has both, and the mean-of-rates trap already bit us in serving-atr#80."""
    lines = load(corpus)

    def recognise(p):
        return "" if p.name == "b.jpg" else {
            l.image.name: l.gt for l in lines}[p.name]

    s = score_model(lines, recognise, model="one-line-lost")
    # errors = len("2100.") = 5 ; chars = 25 + 5 + 31 = 61
    assert s.cer == pytest.approx(5 / 61, abs=0.01)
    assert s.cer < 0.2                      # a mean of rates would give ~0.33


def test_a_failing_line_is_a_failure_not_a_wrong_reading(corpus):
    """Charging its reference length as errors would make an engine outage look
    like bad recognition — the confusion #367 exists to prevent."""
    lines = load(corpus)

    def recognise(p):
        if p.name == "b.jpg":
            raise RuntimeError("502")
        return {l.image.name: l.gt for l in lines}[p.name]

    s = score_model(lines, recognise, model="flaky")
    assert s.failures == 1 and s.lines == 2
    assert s.cer == 0.0                     # the two that ran were perfect


def test_per_line_scores_are_kept_for_outlier_inspection(corpus):
    lines = load(corpus)
    s = score_model(lines, lambda p: "völlig anderes", model="bad")
    assert len(s.per_line) == 3
    assert all(isinstance(c, float) for _n, c in s.per_line)


def test_timing_is_recorded(corpus):
    s = score_model(load(corpus), lambda p: "x", model="m")
    assert s.seconds >= 0.0


# ── the report ───────────────────────────────────────────────────────────────

def test_the_table_ranks_by_cer(corpus):
    lines = load(corpus)
    good = score_model(lines, lambda p: {l.image.name: l.gt for l in lines}[p.name],
                       model="good")
    bad = score_model(lines, lambda p: "xxx", model="bad")
    text = format_scores([bad, good])
    assert text.index("good") < text.index("bad")


def test_the_report_states_the_period_mismatch(corpus):
    """This corpus is 19th c.; the project's material is 14th-16th. A number quoted
    without that caveat would be read as a verdict on the target corpus."""
    lines = load(corpus)
    text = format_scores([score_model(lines, lambda p: "x", model="m")])
    assert "19. Jh" in text and "14.-16." in text


def test_an_empty_result_set_says_so():
    assert "keine auswertbaren" in format_scores([])


def test_a_model_that_never_ran_is_not_ranked():
    """A model whose every line failed has no CER — reporting 0.0 would rank a
    total outage first."""
    s = ModelScore(model="dead", engine="e", failures=3)
    assert s.cer is None
    assert "dead" not in format_scores([s])
