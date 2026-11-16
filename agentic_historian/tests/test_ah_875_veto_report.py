"""eval/veto_report.py — the scorer veto in production, read back (#875).

#491 calibrated the margin (2.5 bits/char) on eight arms of one model. Since
14.11.2026 production writes ``fusion_scores`` and ``fusion_vetoed`` for every
fused page; this report reads them against the engines that actually run:
veto rate per engine and reading, the gap to the page's best around the
margin, readings just under it, the cells, and each reading's distance to the
fused text (agreement, not quality). Records from before the per-reading keys
name a reading by engine alone and are reported apart.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_875_veto_report.py
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval import veto_report as vr

CELL = "de|*|*|*"


def _page(doc_id, scores, vetoed=(), *, transcription="", recognitions=(), strategy="vote+veto"):
    return {"doc_id": doc_id, "transcription": transcription,
            "recognitions": list(recognitions),
            "a_meta": {"fusion_strategy": strategy,
                       "fusion_scores": {k: {"bits_per_char": b, "z": 0.0, "cell": CELL}
                                         for k, b in scores.items()},
                       "fusion_vetoed": [{"label": k, "bits_per_char": scores[k], "z": 0.0,
                                          "cell": CELL} for k in vetoed]}}


@pytest.fixture
def pages():
    return [
        # the #298 trace under the de cell: mccatmus vetoed, catmus 0.34 under the margin
        _page("doc-a", {"trocr/kurrent-xvi-xvii": 4.85, "vlm/internvl3-8b-instruct": 5.22,
                        "kraken/catmus_medieval": 7.01, "kraken/mccatmus": 8.16},
              vetoed=["kraken/mccatmus"],
              transcription="der brief ist gut",
              recognitions=[
                  {"engine": "trocr", "model_id": "kurrent-xvi-xvii", "text": "der brief ist gut"},
                  {"engine": "vlm", "model_id": "internvl3-8b-instruct", "text": "der brief ist gut."},
                  {"engine": "kraken", "model_id": "catmus_medieval", "text": "der brief lst gnt"},
                  {"engine": "kraken", "model_id": "mccatmus", "text": "dcr bnef ilt guf"}]),
        # nothing vetoed
        _page("doc-b", {"trocr/kurrent-xvi-xvii": 5.0, "kraken/catmus_medieval": 5.6},
              strategy="vote"),
        # an old record: engine-only keys
        _page("doc-c", {"vlm": 4.9, "kraken": 8.0}, vetoed=["kraken"]),
        # no scorer for this page
        {"doc_id": "doc-d", "a_meta": {"fusion_strategy": "vote"}},
    ]


def test_pages_with_a_scorer_and_with_a_veto_are_counted(pages):
    t = vr.analyse(pages, margin=2.5)
    assert (t["pages"], t["pages_with_scorer"], t["pages_with_veto"]) == (4, 3, 2)
    assert t["pages_legacy_keys"] == 1
    assert t["cells"] == {CELL: 8}
    assert t["strategies"] == {"vote+veto": 2, "vote": 2}


def test_rates_per_engine_and_per_reading(pages):
    t = vr.analyse(pages, margin=2.5)
    assert t["by_engine"]["kraken"] == {"scored": 4, "vetoed": 2, "rate": 0.5}
    assert t["by_engine"]["trocr"]["vetoed"] == 0
    mcc = t["by_reading"]["kraken/mccatmus"]
    assert (mcc["scored"], mcc["vetoed"], mcc["vetoed_gap_min"]) == (1, 1, pytest.approx(3.31))
    assert t["by_reading"]["kraken"]["legacy"] is True
    assert t["by_reading"]["kraken/mccatmus"]["legacy"] is False


def test_the_gap_histogram_places_the_vetoed_right_of_the_margin(pages):
    h = vr.analyse(pages, margin=2.5)["gap_histogram"]
    assert h["0"] == {"readings": 3, "vetoed": 0}           # the best of each page
    assert h["2–2.5"] == {"readings": 1, "vetoed": 0}       # catmus, 2.16
    assert h["3–4"] == {"readings": 2, "vetoed": 2}         # mccatmus 3.31, old kraken 3.1
    assert sum(r["readings"] for r in h.values()) == 8


def test_a_reading_just_under_the_margin_is_named(pages):
    near = vr.analyse(pages, margin=2.5)["near_margin"]
    assert near == [{"doc_id": "doc-a", "reading": "kraken/catmus_medieval", "gap": 2.16}]
    assert vr.analyse(pages, margin=3.0)["near_margin"] == []


def test_distance_to_the_fusion_is_reported_per_reading(pages):
    rows = vr.analyse(pages, margin=2.5)["by_reading"]
    assert rows["trocr/kurrent-xvi-xvii"]["median_to_fusion"] == 0.0
    assert rows["kraken/mccatmus"]["median_to_fusion"] > rows["kraken/catmus_medieval"]["median_to_fusion"]
    assert rows["vlm"]["median_to_fusion"] is None          # no recognitions in that record


def test_the_report_says_what_it_does_not_measure(pages):
    md = vr.render(vr.analyse(pages, margin=2.5))
    assert "mit Veto: **2** (67% der Seiten mit Scorer)" in md
    assert "**1 Seite(n) mit alter Aufzeichnung**" in md
    assert "| `kraken` (alt) |" in md
    assert "`kraken/catmus_medieval` auf `doc-a`: +2.16" in md
    assert "Übereinstimmung, keine Qualität" in md


def test_records_are_found_in_both_layouts_and_bad_files_are_skipped(tmp_path, pages, capsys):
    out = tmp_path / "outputs"
    out.mkdir()
    (out / "doc-a_pipeline.json").write_text(json.dumps(pages[0]))
    (out / "broken_pipeline.json").write_text("{nope")
    docs = tmp_path / "docs" / "doc-b"
    docs.mkdir(parents=True)
    (docs / "pipeline.json").write_text(json.dumps(pages[1]))
    js = tmp_path / "veto.json"
    assert vr.main([str(out), str(tmp_path / "docs"), "--margin", "2.5", "--json", str(js)]) == 0
    io = capsys.readouterr()
    assert "übersprungen:" in io.err and "broken_pipeline.json" in io.err
    t = json.loads(js.read_text())
    assert (t["pages"], t["pages_with_veto"]) == (2, 1)


def test_the_margin_defaults_to_the_config(monkeypatch, tmp_path, pages, capsys):
    import config

    monkeypatch.setattr(config, "FUSION_VETO_MARGIN", 3.5)
    (tmp_path / "x_pipeline.json").write_text(json.dumps(pages[0]))
    vr.main([str(tmp_path)])
    assert "Marge: **3.5** bits/char" in capsys.readouterr().out
