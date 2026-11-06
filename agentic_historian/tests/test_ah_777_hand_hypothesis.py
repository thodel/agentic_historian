"""#777 — a change along a long volume becomes a hypothesis, never a finding.

Built on the synthetic two-hand pages of the #773 tests: a volume that changes
hand half-way gets the computed element ``Schriftverlauf`` with the changepoint
at the right page, ``quelle = berechnet`` and ``unsicher``; a volume of one hand
gets nothing; a short volume gets nothing whatever its numbers. The orchestrator
hook writes the measurement beside the outputs and adds the element only when
the switch is on; the publisher ships the file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for p in (PKG, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import hand_consistency as hc  # noqa: E402
from test_ah_773_hand_consistency import _feats, _page  # noqa: E402

Image = pytest.importorskip("PIL.Image")


def _two_hands(n_a: int = 7, n_b: int = 5) -> hc.VolumeReport:
    pages = [_feats("A", s, f"p{s:02d}") for s in range(n_a)] + \
            [_feats("B", s, f"p{s + n_a:02d}") for s in range(n_b)]
    return hc.analyse(pages)


def _one_hand(n: int = 12) -> hc.VolumeReport:
    return hc.analyse([_feats("A", s, f"p{s:02d}") for s in range(n)])


# ── the hypothesis ───────────────────────────────────────────────────────────

def test_a_change_of_hand_becomes_a_computed_uncertain_element_at_the_right_page():
    rep = _two_hands()
    el = hc.hypothesis(rep, total_pages=120)
    assert el is not None
    assert el["quelle"] == "berechnet" and el["unsicher"] is True
    assert "Schriftbild ändert sich ab p07" in el["wert"]
    assert el["wert"].endswith("Mögliche neue Hand oder Schreibkampagne. Nicht geprüft.")
    assert el["basis"]["seiten"] == 12 and el["basis"]["von"] == 120
    assert el["basis"]["methode"] == "hand_consistency"
    assert el["basis"]["changepoint"]["page"] == "p07"
    assert set(el["basis"]["schwellen"]) == {"min_pages", "change_ratio", "silhouette", "drift"}
    assert "Schreiber" not in el


def test_one_hand_gives_no_hypothesis():
    assert hc.hypothesis(_one_hand(), total_pages=200) is None


def test_a_short_volume_gives_none_whatever_it_shows():
    rep = _two_hands(4, 3)                      # 7 pages, below HINT_MIN_PAGES
    assert rep.changepoint["ratio"] > 1.0
    assert hc.hypothesis(rep) is None
    assert hc.hypothesis(rep, min_pages=7) is not None


def test_a_single_foreign_page_is_named_but_is_not_a_change():
    pages = [_feats("A", s, f"p{s:02d}") for s in range(11)]
    pages.insert(5, _feats("B", 99, "odd"))
    rep = hc.analyse(pages)
    assert rep.outliers == ["odd"]
    el = hc.hypothesis(rep)
    # an outlier alone is no hypothesis; if the split and drift stay quiet, nothing is said
    if el is not None:
        assert "odd weicht stark ab" in el["wert"]
        assert "Schriftbild ändert sich" not in el["wert"] or rep.silhouette2 >= hc.HINT_SILHOUETTE


def test_drift_alone_is_a_hypothesis_when_blocks_rise():
    rep = hc.VolumeReport(pages=[f"p{i}" for i in range(9)], matrix=[], centrality=[], typical="p0",
                          outliers=[], outlier_threshold=None,
                          blocks=[{"pages": ["p0", "p2"], "centrality": 1.0, "within": 1.0},
                                  {"pages": ["p3", "p5"], "centrality": 1.05, "within": 1.0},
                                  {"pages": ["p6", "p8"], "centrality": 1.2, "within": 1.0}],
                          changepoint={"index": 4, "page": "p4", "ratio": 1.05}, silhouette2=0.02)
    el = hc.hypothesis(rep)
    assert el is not None and "Blockmittel steigen zum Ende (+20 %)" in el["wert"]
    assert "Schriftbild ändert sich" not in el["wert"]
    assert el["basis"]["drift"] == pytest.approx(0.2)
    assert hc.hypothesis(rep, drift=0.25) is None


# ── the sampled volume run ───────────────────────────────────────────────────

def test_describe_volume_samples_evenly_and_reports_the_whole_volume(tmp_path):
    files = []
    for i in range(20):
        hand = "A" if i < 12 else "B"
        f = tmp_path / f"page-{i:04d}.png"
        Image.fromarray(_page(hand, i)).save(f)
        files.append(f)
    rep, skipped, el = hc.describe_volume(files, max_pages=10)
    assert len(rep.pages) == 10 and skipped == []
    assert rep.pages[0] == "page-0000.png" and rep.pages[-1] == "page-0019.png"
    assert el is not None and el["basis"]["von"] == 20 and el["basis"]["seiten"] == 10


# ── the validation table says whether the hypothesis fires where it should ───

def test_validation_table_counts_where_the_hypothesis_fires():
    rows = [{"id": "a", "hands": 1, "pages": 10, "skipped": 0, "silhouette2": 0.1,
             "changepoint_ratio": 1.1, "changepoint_page": "p03", "outliers": 0, "typical": "p01",
             "hint": False},
            {"id": "b", "hands": 2, "pages": 10, "skipped": 0, "silhouette2": 0.5,
             "changepoint_ratio": 1.9, "changepoint_page": "p07", "outliers": 1, "typical": "p02",
             "hint": True}]
    table = hc.format_validation(rows)
    assert "| hint (#777) |" in table and "| fires |" in table
    assert "fires on 1 of 1 several-hands volumes and on 0 of 1 one-hand volumes" in table


# ── the orchestrator hook and the publisher ──────────────────────────────────

def test_the_hook_writes_the_measurement_and_adds_the_element_only_when_on(tmp_path, monkeypatch):
    import config
    import orchestrator
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path)
    pages = [f"p{i}.png" for i in range(10)]
    rep = _two_hands()
    monkeypatch.setattr(hc, "describe_volume", lambda paths, **kw: (rep, [], hc.hypothesis(rep, total_pages=len(paths))))
    description = {"source_json": {"Schrift": {"wert": "Kursive"}}}
    events = []
    monkeypatch.setattr(orchestrator, "_emit", lambda on_phase, doc_id, phase, agent, **kw: events.append((phase, kw)))

    monkeypatch.setattr(config, "HAND_CONSISTENCY_HINT", False)
    assert orchestrator._hand_hint("doc", pages, description) is None
    assert "Schriftverlauf" not in description["source_json"] and not (tmp_path / "doc_hand.json").exists()

    monkeypatch.setattr(config, "HAND_CONSISTENCY_HINT", True)
    el = orchestrator._hand_hint("doc", pages, description)
    assert el and description["source_json"]["Schriftverlauf"] is el
    written = json.loads((tmp_path / "doc_hand.json").read_text(encoding="utf-8"))
    assert written["hint"]["quelle"] == "berechnet" and written["changepoint"]["page"] == "p07"
    assert events[-1][0] == "hand_consistency" and "hypothesis" in events[-1][1]["decision"]
    # the VLM's elements are untouched
    assert description["source_json"]["Schrift"] == {"wert": "Kursive"}


def test_the_hook_never_breaks_the_run(monkeypatch):
    import config
    import orchestrator
    monkeypatch.setattr(config, "HAND_CONSISTENCY_HINT", True)

    def boom(paths, **kw):
        raise RuntimeError("no numpy today")
    monkeypatch.setattr(hc, "describe_volume", boom)
    assert orchestrator._hand_hint("doc", [f"p{i}" for i in range(10)], {"source_json": {}}) is None


def test_the_publisher_ships_the_measurement_when_it_exists(tmp_path, monkeypatch):
    import config
    from utils import publish_github as pg
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path / "d")
    monkeypatch.setattr(config, "TRANSCRIPTIONS_DIR", tmp_path / "t")
    (tmp_path / "doc_pipeline.json").write_text("{}", encoding="utf-8")
    assert "hand_consistency.json" not in pg.collect_artifacts("doc")
    (tmp_path / "doc_hand.json").write_text("{}", encoding="utf-8")
    assert pg.collect_artifacts("doc")["hand_consistency.json"] == tmp_path / "doc_hand.json"
