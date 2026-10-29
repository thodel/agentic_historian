"""#416 on a line draw: the loader, the pooled CER, and the order check.

The measurement itself ran on 29.10.2026 on the T4 draw of training-atr-models
(187 pages, 4,836 lines, a VLM and two TrOCRs of comparable strength) and is
written up in docs/EVALUATION_HARNESS.md. These tests pin the tool that made
it, on a draw of three pages built here, so the numbers in the document can be
re-made from the share and not only re-read.

Offline. Run from the repo root.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import fusion_on_lineset as fl  # noqa: E402

TRUTH = ["Dem Edlen und Vesten Herrn", "Hauptmann zu Sanct Gallen", "unserm lieben getreuen"]


def _draw(tmp_path: Path, arms: dict[str, list[str]], pages=("p1", "p1", "p2")) -> Path:
    d = tmp_path / "draw"
    d.mkdir()
    with open(d / "draw.jsonl", "w", encoding="utf-8") as f:
        for i, (text, page) in enumerate(zip(TRUTH, pages)):
            f.write(json.dumps({"image": f"crops/{i}.jpg", "text": text,
                                "page": f"data/pages/{page}.xml"}) + "\n")
    for name, lines in arms.items():
        with open(d / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for i, text in enumerate(lines):
                key = "prediction" if name.startswith("trocr") else "raw"
                f.write(json.dumps({"image": f"crops/{i}.jpg", key: text}) + "\n")
    return d


def test_a_pages_lines_are_joined_in_draw_order_for_every_reader(tmp_path):
    d = _draw(tmp_path, {"vlm": ["a", "b", "c"], "trocr": ["x", "y", "z"]})
    draw = fl.load_draw(d / "draw.jsonl")
    arms = {"vlm": fl.load_arm(d / "vlm.jsonl"), "trocr": fl.load_arm(d / "trocr.jsonl")}
    readings, references, n_lines = fl.page_texts(draw, arms)
    assert references == {"p1": TRUTH[0] + "\n" + TRUTH[1], "p2": TRUTH[2]}
    assert readings["vlm"] == {"p1": "a\nb", "p2": "c"}
    assert readings["trocr"] == {"p1": "x\ny", "p2": "z"}
    assert n_lines == {"p1": 2, "p2": 1}


def test_both_raw_shapes_are_read_and_a_missing_line_is_an_error_not_an_empty_reading(tmp_path):
    """TrOCR's evaluate writes ``prediction``, the VLM rescorer ``raw``. An arm
    that did not read a line of the draw is a different measurement, and the
    tool says so instead of scoring an empty string as a reading."""
    d = _draw(tmp_path, {"vlm": ["a", "b", "c"], "trocr": ["x", "y"]})
    draw = fl.load_draw(d / "draw.jsonl")
    arms = {"vlm": fl.load_arm(d / "vlm.jsonl"), "trocr": fl.load_arm(d / "trocr.jsonl")}
    assert arms["vlm"]["crops/0.jpg"] == "a" and arms["trocr"]["crops/0.jpg"] == "x"
    with pytest.raises(SystemExit, match="crops/2.jpg: not read by trocr"):
        fl.page_texts(draw, arms)


def test_excluded_pages_leave_the_draw_entirely(tmp_path):
    """The 100-page TrOCR arm trained on 27 of the draw's pages; a reader's own
    training pages are not a measurement of it."""
    d = _draw(tmp_path, {"vlm": ["a", "b", "c"]})
    draw = fl.load_draw(d / "draw.jsonl")
    readings, references, _ = fl.page_texts(draw, {"vlm": fl.load_arm(d / "vlm.jsonl")}, exclude={"p1"})
    assert set(references) == {"p2"} and set(readings["vlm"]) == {"p2"}


def test_pooled_cer_is_edits_over_characters_with_nothing_folded():
    """The number TROCR.md reports: raw, so one arm alone reproduces its
    published figure. Case and punctuation count — the harness CER folds both,
    and the two must not be confused."""
    refs = {"p1": "abcd", "p2": "ef"}
    assert fl.pooled_cer({"p1": "abcd", "p2": "ef"}, refs) == 0.0
    assert fl.pooled_cer({"p1": "abcX", "p2": "ef"}, refs) == pytest.approx(1 / 6)
    assert fl.pooled_cer({"p1": "ABCD", "p2": "ef"}, refs) == pytest.approx(4 / 6)


def test_the_order_check_fuses_the_same_pages_twice_and_the_vote_can_differ():
    """A column where every candidate reads differently falls to the first
    candidate. Measured on the T4 draw that is worth 3.8 points between the
    production order and its reverse — so the report prints both, and this
    test holds the mechanism on three readings that disagree in one place."""
    readings = {"a": {"p": "Dem Edlen und Vesten Herrn"},
                "b": {"p": "Dem Edlan und Vesten Herrn"},
                "c": {"p": "Dem Edlun und Vesten Herrn"}}
    refs = {"p": "Dem Edlen und Vesten Herrn"}
    forward = fl.fused_pages(readings, refs, ["a", "b", "c"])["p"]
    backward = fl.fused_pages(readings, refs, ["c", "b", "a"])["p"]
    assert forward == "Dem Edlen und Vesten Herrn"
    assert backward == "Dem Edlun und Vesten Herrn"
    assert fl.pooled_cer({"p": forward}, refs) < fl.pooled_cer({"p": backward}, refs)


def test_the_report_carries_both_fused_rows_and_the_strata(tmp_path, capsys):
    """End to end on the three-page draw: a reader exactly right, a reader
    wrong on one page, a reader wrong everywhere — and the report names the
    pooled rows, the gap between the two orders, and both kinds of field."""
    d = _draw(tmp_path, {"vlm": TRUTH,
                         "trocr-full": [TRUTH[0], "Hauptmann zu Sanct Gallon", TRUTH[2]],
                         "trocr-100p": ["Dam Edlan und Vastan Harrn", "Hauptmann zu Sanct Gallan",
                                        "unsarm liaban gatrauan"]})
    (d / "skip.txt").write_text("p2\n")
    assert fl.main([str(d), "--draw", "draw.jsonl", "--arm", "vlm=vlm.jsonl",
                    "--arm", "trocr-full=trocr-full.jsonl", "--arm", "trocr-100p=trocr-100p.jsonl",
                    "--exclude-pages", str(d / "skip.txt"), "--out", str(d / "r.md")]) == 0
    text = (d / "r.md").read_text(encoding="utf-8")
    assert "1 pages excluded" in text
    assert "| `vlm` | 0.0000 |" in text
    assert "candidates in the order above" in text and "candidates reversed" in text
    assert "Kandidaten vergleichbar stark" in text and "ein Kandidat führt klar" in text
    assert capsys.readouterr().out.startswith("# Fusion on `draw.jsonl`")


def test_the_tool_measures_and_does_not_gate():
    """Same guard as test_ah_416_fusion_by_field: the measurement must not
    leak a threshold into the pipeline. This file imports fusion only to fuse."""
    source = (PKG / "eval" / "fusion_on_lineset.py").read_text(encoding="utf-8")
    assert "import config" not in source
    assert "COMPARABLE_LEAD" in source            # read from the bench …
    assert "FUSION_COMPARABLE_LEAD" not in source  # … never written anywhere
