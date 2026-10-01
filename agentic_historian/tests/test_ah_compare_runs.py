"""Comparing two batch readings of the same pages (`compare_runs`).

The Laßberg corpus ended up with two readings of the same share — trocr-kurrent
over 899 pages, qwen3.5-4b over 6719 — and nothing that reads both. The question
they have to answer first is not "which is better" (no ground truth, #326) but
"are these a field of equals", because #416 measured majority voting LOSING to
the best single engine where one candidate dominates weaker ones, and winning only
where the candidates are comparable with uncorrelated errors.

Offline — nothing but files on disk.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import compare_runs as cr                       # noqa: E402


def write_run(root: Path, model: str, pages: dict[str, str]) -> Path:
    d = root / model
    d.mkdir(parents=True, exist_ok=True)
    for key, text in pages.items():
        (d / f"{key}.txt").write_text(text, encoding="utf-8")
        (d / f"{key}.json").write_text('{"text": "x"}', encoding="utf-8")
    return root


# ── the measure ──────────────────────────────────────────────────────────────

def test_disagreement_is_symmetric():
    """A comparison table whose numbers depend on column order is not a
    measurement. `cer` divides by the reference, so it is asymmetric by itself."""
    a, b = "Euer Hochwohlgeboren haben", "seine Hochwohlgeboren haben nie"
    assert cr.disagreement(a, b) == cr.disagreement(b, a)


def test_disagreement_is_bounded_by_one():
    """`cer` is not: a short reference against a long hypothesis exceeds 1.0."""
    from eval.metrics import cer
    short, long = "abc", "abcdefghijklmnop"
    assert cer(short, long) > 1.0
    assert cr.disagreement(short, long) <= 1.0


def test_identical_readings_do_not_disagree():
    assert cr.disagreement("Hochwohlgeboren", "Hochwohlgeboren") == 0.0


def test_layout_and_punctuation_are_not_disagreement():
    """trocr writes ' , ' where the VLM writes ','; the engines differ about line
    ends on nearly every page. Counting that would drown the real differences."""
    trocr = "mich für die nach einem Exemplar ,\ndas zu den Seltenheiten gehört ."
    vlm = "mich für die nach einem Exemplar, das zu den Seltenheiten gehört."
    assert cr.disagreement(trocr, vlm) == 0.0


def test_two_empty_readings_agree():
    assert cr.disagreement("", "   \n ") == 0.0


# ── loading ──────────────────────────────────────────────────────────────────

def test_a_run_with_several_models_is_several_readings(tmp_path):
    """'two runs of one model' and 'one run of two models' are the same shape."""
    run = write_run(tmp_path / "r", "trocr-kurrent", {"p1": "a"})
    write_run(run, "qwen3.5-4b", {"p1": "b"})
    labels = [r.label for r in cr.discover_readings([run])]
    assert labels == ["r/qwen3.5-4b", "r/trocr-kurrent"]


def test_a_directory_without_model_output_is_not_a_reading(tmp_path):
    run = tmp_path / "r"
    (run / "empty").mkdir(parents=True)
    assert cr.discover_readings([run]) == []


def test_a_missing_run_directory_is_an_error(tmp_path):
    with pytest.raises(NotADirectoryError):
        cr.discover_readings([tmp_path / "nope"])


def test_one_reading_cannot_be_compared(tmp_path):
    run = write_run(tmp_path / "r", "m", {"p1": "a"})
    with pytest.raises(ValueError):
        cr.compare(cr.discover_readings([run]))


# ── coverage ─────────────────────────────────────────────────────────────────

def test_only_the_shared_pages_are_compared(tmp_path):
    """899 pages against 6719: a page one side never read is missing coverage,
    not measured disagreement."""
    a = write_run(tmp_path / "trocr", "trocr-kurrent",
                  {"p1": "Hochwohlgeboren", "p2": "Seltenheiten"})
    b = write_run(tmp_path / "vlm", "qwen",
                  {"p1": "Hochwohlgeboren", "p2": "Seltenheiten", "p3": "Weimar"})
    c = cr.compare(cr.discover_readings([a, b]), min_chars=1)   # not the subject
    assert c.common == ["p1", "p2"]
    assert c.pairs[0].pages == 2, "p3 is not a disagreement"


def test_mean_chars_is_taken_over_the_shared_pages_only(tmp_path):
    """Over everything each side holds, the number would describe the corpus
    rather than the model."""
    a = write_run(tmp_path / "a", "m1", {"p1": "x" * 10})
    b = write_run(tmp_path / "b", "m2", {"p1": "x" * 10, "p2": "y" * 1000})
    c = cr.compare(cr.discover_readings([a, b]), min_chars=1)   # not the subject
    assert all(r.chars(c.compared) == 10 for r in c.readings)


# ── empty pages are a different kind of difference ───────────────────────────

def test_a_page_empty_on_one_side_is_counted_not_averaged(tmp_path):
    """Its disagreement is 100% by construction, which says something
    categorically different from 100% between two garbled texts."""
    a = write_run(tmp_path / "a", "m1", {"p1": "Hochwohlgeboren", "p2": ""})
    b = write_run(tmp_path / "b", "m2", {"p1": "Hochwohlgeboren", "p2": "Seltenheiten"})
    c = cr.compare(cr.discover_readings([a, b]), min_chars=1)   # not the subject
    assert c.compared == ["p1"]
    assert c.one_sided_empty == {"a/m1": ["p2"]}
    assert c.pairs[0].median_disagreement == 0.0, "p2 must not inflate this"


def test_a_page_empty_in_both_is_agreement_not_a_gap(tmp_path):
    """9% of this corpus comes back empty from both engines — blank versos."""
    a = write_run(tmp_path / "a", "m1", {"p1": ""})
    b = write_run(tmp_path / "b", "m2", {"p1": "  "})
    c = cr.compare(cr.discover_readings([a, b]))
    assert c.all_empty == ["p1"] and c.compared == []


# ── the two situations #416 distinguishes ────────────────────────────────────

def _field(tmp_path, texts_a, texts_b, min_chars=None):
    pages_a = {f"p{i}": t for i, t in enumerate(texts_a)}
    pages_b = {f"p{i}": t for i, t in enumerate(texts_b)}
    a = write_run(tmp_path / "a", "trocr", pages_a)
    b = write_run(tmp_path / "b", "vlm", pages_b)
    kw = {} if min_chars is None else {"min_chars": min_chars}
    return cr.compare(cr.discover_readings([a, b]), **kw)


def test_a_field_of_equals_shows_a_low_median(tmp_path):
    truth = ("Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag ertheilt, "
             "mich für ein vollständiges Exemplar des Blattes umzusehen, das zu "
             "den Seltenheiten gehören mag.")
    near = truth.replace("ertheilt", "entheilt").replace("Blattes", "Blates")
    c = _field(tmp_path, [truth] * 5, [near] * 5)
    assert c.pairs[0].median_disagreement < 0.05
    assert c.pairs[0].above_no_merge == 0, "fusion would blend all of these"


def test_total_disagreement_shows_up_as_the_no_merge_share(tmp_path):
    """Above 35% fusion refuses to blend and returns one candidate verbatim
    (#300). The share of such pages is the number that decides whether a third
    reading can help at all."""
    c = _field(tmp_path,
               ["Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag "
                "ertheilt, mich für ein vollständiges Exemplar umzusehen."] * 4,
               ["1000000 " * 14] * 4)
    assert c.pairs[0].median_disagreement > 0.5
    assert c.pairs[0].above_no_merge == 4


def test_the_widest_pages_are_named_so_somebody_can_look(tmp_path):
    c = _field(tmp_path,
               ["Hochwohlgeboren", "Hochwohlgeboren", "Hochwohlgeboren"],
               ["Hochwohlgeboren", "Hochwohlgeboren", "völlig anderer Text hier"],
               min_chars=1)                                     # not the subject
    assert c.pairs[0].worst[0][0] == "p2"
    assert c.pairs[0].worst[0][1] > 0.5


def test_three_readings_make_three_pairs(tmp_path):
    """VLM + trocr + kraken is the field #416 asks for; the report has to carry
    every pair, not just the first two."""
    runs = [write_run(tmp_path / n, n, {"p1": t}) for n, t in
            [("trocr", "Hochwohlgeboren"), ("vlm", "Hochwohlgeboren"),
             ("kraken", "Hochwohlgeborn")]]
    c = cr.compare(cr.discover_readings(runs), min_chars=1)
    assert {(p.a, p.b) for p in c.pairs} == {
        ("trocr/trocr", "vlm/vlm"),
        ("trocr/trocr", "kraken/kraken"),
        ("vlm/vlm", "kraken/kraken"),
    }


# ── the report ───────────────────────────────────────────────────────────────

def test_the_report_refuses_to_claim_quality(tmp_path):
    """Every other table in this project carries that warning; this one measures
    something even further from quality and must carry it too."""
    c = _field(tmp_path, ["Hochwohlgeboren"], ["Hochwohlgeborn"], min_chars=1)
    text = cr.format_report(c)
    assert "not quality" in text
    assert "#416" in text and "#326" in text


def test_the_report_names_coverage_and_the_pair(tmp_path):
    a = write_run(tmp_path / "trocr_run", "trocr-kurrent", {"p1": "Hochwohlgeboren"})
    b = write_run(tmp_path / "vlm_run", "qwen3.5-4b", {"p1": "Hochwohlgeborn",
                                                      "p2": "Weimar"})
    _, text = cr.compare_run_dirs([a, b], min_chars=1)
    assert "`trocr_run/trocr-kurrent`" in text
    assert "`vlm_run/qwen3.5-4b`" in text
    assert "pages every reading has: **1**" in text


# ── fragments are not a verdict on the engines ───────────────────────────────

def test_a_fragment_pair_is_counted_not_averaged(tmp_path):
    """`den |` against `der 1850` — five characters and eight — disagree by 75 %
    and say nothing about whether the engines can read this hand. Left in the
    distribution they move the median more than the letters do."""
    letter_a = "Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag ertheilt, " \
               "mich für ein vollständiges Exemplar des Blattes umzusehen."
    letter_b = "Euer Hochwohlgeboren haben vor zwei Jahren den Auftrag entheilt, " \
               "mich für ein vollständiges Exemplar des Blattes umzusehen."
    a = write_run(tmp_path / "a", "trocr", {"letter": letter_a, "frag": "den |"})
    b = write_run(tmp_path / "b", "vlm", {"letter": letter_b, "frag": "der 1850"})
    c = cr.compare(cr.discover_readings([a, b]))
    assert c.compared == ["letter"]
    assert c.short == ["frag"]
    assert c.pairs[0].median_disagreement < 0.05, "the fragment is out of this"


def test_the_threshold_can_be_lowered_to_include_them(tmp_path):
    a = write_run(tmp_path / "a", "trocr", {"frag": "den |"})
    b = write_run(tmp_path / "b", "vlm", {"frag": "der 1850"})
    c = cr.compare(cr.discover_readings([a, b]), min_chars=1)
    assert c.compared == ["frag"] and c.short == []


def test_the_report_says_how_many_were_too_short(tmp_path):
    a = write_run(tmp_path / "a", "trocr", {"frag": "den |"})
    b = write_run(tmp_path / "b", "vlm", {"frag": "der 1850"})
    _, text = cr.compare_run_dirs([a, b])
    assert "too short to compare (under 100 chars somewhere): 1" in text
