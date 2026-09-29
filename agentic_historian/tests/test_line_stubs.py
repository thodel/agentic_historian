"""The stub filter has to cut the specks and keep the foliation.

Both halves are load-bearing. A filter that keeps `S.` leaves a third of the
Schöni readings as noise; a filter that also eats `109` throws away the recto
folio numerals, which are the one datum on those pages that measured reliable.
The cases below are the real ones, taken from the run of 29.09.2026.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from line_stubs import clean_lines, clean_run, is_stub


def test_the_stubs_the_model_actually_wrote_are_cut():
    for text in ("S.", "s.", "d.", "1.", ".", " S. "):
        assert is_stub(text), text


def test_folio_numerals_survive():
    # 109 and 406 are folio numbers read correctly off 120-px boxes; a rule
    # that cut them would be tidier and wrong.
    for text in ("109", "406", "28"):
        assert not is_stub(text), text


def test_a_short_word_is_a_reading_not_a_stub():
    # Wrong, probably — the model under-reading a real line — but hiding it
    # would make the run look better than it is.
    for text in ("und", "ist", "vermeint", "de", "in"):
        assert not is_stub(text), text


def test_clean_lines_counts_what_it_drops():
    lines = [{"text": "109"}, {"text": "S."}, {"text": "vermeint"},
             {"text": "S."}, {"text": ""}, {"text": "und"}]
    kept, dropped = clean_lines(lines)
    assert kept == ["109", "vermeint", "und"]
    assert dropped == 2


def test_clean_run_rebuilds_text_and_leaves_the_json_alone(tmp_path):
    meta = {"lines": [{"text": "109"}, {"text": "S."}, {"text": "der herr"}]}
    (tmp_path / "page_1.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "page_1.txt").write_text("109\nS.\nder herr\n", encoding="utf-8")

    result = clean_run(tmp_path)

    assert result["lines_dropped"] == 1
    assert (tmp_path / "page_1.txt").read_text() == "109\nder herr\n"
    # the record of what the model returned is untouched
    assert json.loads((tmp_path / "page_1.json").read_text()) == meta


def test_running_it_twice_changes_nothing_more(tmp_path):
    meta = {"lines": [{"text": "S."}, {"text": "der herr"}]}
    (tmp_path / "page_1.json").write_text(json.dumps(meta), encoding="utf-8")

    first = clean_run(tmp_path)
    after_first = (tmp_path / "page_1.txt").read_text()
    second = clean_run(tmp_path)

    assert (tmp_path / "page_1.txt").read_text() == after_first
    assert first["lines_dropped"] == second["lines_dropped"] == 1


def test_dry_run_writes_nothing(tmp_path):
    (tmp_path / "page_1.json").write_text(
        json.dumps({"lines": [{"text": "S."}, {"text": "der herr"}]}),
        encoding="utf-8")

    result = clean_run(tmp_path, dry_run=True)

    assert result["lines_dropped"] == 1
    assert not (tmp_path / "page_1.txt").exists()
