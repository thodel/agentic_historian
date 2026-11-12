"""#491 L3 — the cell's scorer vetoes the garbage edge before the vote.

The scorer artefact is read, not trained, here: ``tests/fixtures/scorer`` holds
a cell the training side wrote with its own code (``make_fixture.py``) and the
numbers it computed for the four readings of the #298 trace. The reader must
reproduce them to the last digit; the veto must drop the two kraken readings
and never the best; and the switch is off until L2 says otherwise.

Offline — no engines, no I/O beyond the fixture. Run from the repo root:
    pytest agentic_historian/tests/test_ah_491_scorer_veto.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import scorer_reader as sr                                   # noqa: E402
from fusion import fuse                                      # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "scorer"
ARTEFACT = FIX / "de-Swiss-charter-15-16.scorer.json"
EXPECTED = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))
TRACE = EXPECTED["trace"]
CONF = {"trocr": 0.95, "kraken-catmus": 0.6, "kraken-mccatmus": 0.6, "vlm": 0.5}


def _recs(labels=TRACE, conf=CONF):
    return [{"engine": lbl, "text": text, "confidence": conf.get(lbl, 0.5)}
            for lbl, text in labels.items()]


@pytest.fixture
def scorer():
    return sr.ScorerArtefact.load(ARTEFACT)


# ── the reader reproduces the training side's numbers ───────────────────────

def test_the_reader_scores_exactly_as_the_training_code_did(scorer):
    for lbl, text in TRACE.items():
        exp = EXPECTED["expected"][lbl]
        assert scorer.bits_per_char(text) == pytest.approx(exp["bits_per_char"], abs=1e-12)
        z, bpc = scorer.z(text)
        assert z == pytest.approx(exp["z"], abs=1e-12) and bpc == pytest.approx(exp["bits_per_char"])
    prev = EXPECTED["expected"]["_with_previous"]["bits_per_char"]
    assert scorer.bits_per_char(TRACE["trocr"], previous=TRACE["vlm"]) == pytest.approx(prev, abs=1e-12)
    assert scorer.heldout == EXPECTED["heldout"]
    assert scorer.cell.key() == "de|Swiss|charter|15-16"
    # the shape of the pre-experiment: the good reading well below the garbage
    assert scorer.bits_per_char(TRACE["trocr"]) < scorer.bits_per_char(TRACE["vlm"]) \
        < scorer.bits_per_char(TRACE["kraken-catmus"]) < scorer.bits_per_char(TRACE["kraken-mccatmus"])


def test_the_reader_refuses_what_is_not_a_scorer(tmp_path):
    with pytest.raises(ValueError):
        sr.ScorerArtefact({"kind": "model", "cell": {}, "order": 3, "backoff": 0.4, "counts": {"": {"a": 1}}})
    with pytest.raises(ValueError):
        sr.ScorerArtefact({"kind": sr.ARTEFACT_KIND, "cell": {}, "order": 3, "backoff": 0.4, "counts": {}})
    with pytest.raises(ValueError):
        sr.ScorerArtefact.load(ARTEFACT).bits_per_char("   ")


# ── cells, bands and the matrix fall back as the model selector does ────────

def test_cells_bands_and_backoff_chain():
    assert sr.century_band(15) == "15-16" and sr.century_band(16) == "15-16"
    assert sr.century_band(19) == "19-20" and sr.century_band(None) is None and sr.century_band("x") is None
    c = sr.Cell.from_criteria({"lang": "de", "region": "Swiss", "document_type": "charter", "century": 16})
    assert c.key() == "de|Swiss|charter|15-16"
    assert [x.key() for x in c.backoff_chain()] == \
        ["de|Swiss|charter|15-16", "de|Swiss|charter|*", "de|Swiss|*|*", "de|*|*|*"]
    assert sr.document_of("12345_4711_r_0001.jpg") == "4711"
    assert sr.document_of("BAT_664_r_00027.jpg") is None


def test_the_matrix_resolves_the_cell_or_its_nearest_fallback(tmp_path):
    data = json.loads(ARTEFACT.read_text(encoding="utf-8"))
    (tmp_path / "a.scorer.json").write_text(json.dumps(data), encoding="utf-8")
    generic = {**data, "cell": {"lang": "de", "region": None, "document_type": None, "century_band": None}}
    (tmp_path / "b.scorer.json").write_text(json.dumps(generic), encoding="utf-8")
    (tmp_path / "c.scorer.json").write_text("{broken", encoding="utf-8")
    m = sr.ScorerMatrix.load_dir(tmp_path)
    assert set(m.scorers) == {"de|Swiss|charter|15-16", "de|*|*|*"}
    s, fb = m.resolve(sr.Cell("de", "Swiss", "charter", "15-16"))
    assert s.cell.key() == "de|Swiss|charter|15-16" and fb is False
    s, fb = m.resolve(sr.Cell("de", "Swiss", "charter", "19-20"))
    assert s.cell.key() == "de|*|*|*" and fb is True
    assert m.resolve(sr.Cell("fr")) == (None, True)


def test_veto_scorer_is_off_by_default_and_refuses_a_document_it_trained_on(monkeypatch):
    import config
    sr._MATRIX.clear()
    crit = {"lang": "de", "region": "Swiss", "document_type": "charter", "century": 15}
    monkeypatch.setattr(config, "FUSION_SCORER_VETO", False)
    monkeypatch.setattr(config, "FUSION_SCORER_DIR", str(FIX))
    assert sr.veto_scorer(crit) is None
    monkeypatch.setattr(config, "FUSION_SCORER_VETO", True)
    assert sr.veto_scorer(crit).cell.key() == "de|Swiss|charter|15-16"
    assert sr.veto_scorer(crit, page="99_4711_r_0001.jpg") is None          # trained on 4711 (#23)
    assert sr.veto_scorer(crit, page="99_0815_r_0001.jpg") is not None
    assert sr.veto_scorer({"lang": "fr"}) is None                           # no cell, no fallback
    assert sr.veto_scorer(crit, directory=str(FIX / "nowhere")) is None
    sr._MATRIX.clear()


# ── the veto in the fusion, on the #298 trace ───────────────────────────────

def test_the_two_kraken_readings_are_vetoed_and_trocr_stands(scorer):
    # the most confident reading is a vetoed one: the no-merge pick must not see it
    conf = {**CONF, "kraken-mccatmus": 0.99}
    res = fuse(_recs(conf=conf), llm_fn=lambda p: "", scorer=scorer, veto_margin=2.5)
    assert [v["label"] for v in res.vetoed] == ["kraken-catmus", "kraken-mccatmus"]
    assert all(v["cell"] == "de|Swiss|charter|15-16" and v["z"] > 2 for v in res.vetoed)
    assert set(res.scores) == set(TRACE) and res.n_candidates == 4
    assert res.scores["trocr"]["bits_per_char"] == pytest.approx(EXPECTED["expected"]["trocr"]["bits_per_char"], abs=1e-4)
    # the two that remain disagree far beyond the no-merge band → the confident one, verbatim
    assert res.text == TRACE["trocr"] and res.strategy == "vote+no-merge"
    assert res.provenance[0].backers == ["trocr"]


def test_the_default_margin_leaves_one_survivor_who_is_returned_as_veto(scorer, monkeypatch):
    import config
    monkeypatch.setattr(config, "FUSION_VETO_MARGIN", 1.5)
    res = fuse(_recs(), llm_fn=lambda p: "", scorer=scorer)
    assert [v["label"] for v in res.vetoed] == ["kraken-catmus", "kraken-mccatmus", "vlm"]
    assert res.text == TRACE["trocr"] and res.strategy == "vote+veto"
    assert res.provenance == [type(res.provenance[0])(TRACE["trocr"], "veto", ["trocr"])]
    assert res.n_candidates == 4


def test_without_a_scorer_nothing_changes(scorer):
    before = fuse(_recs(), llm_fn=lambda p: "")
    assert before.vetoed == [] and before.scores == {} and before.strategy == "vote+no-merge"
    assert before.text == TRACE["trocr"] and before.n_candidates == 4


def test_the_best_candidate_is_never_vetoed(scorer):
    garbage = {k: v for k, v in TRACE.items() if k.startswith("kraken")}
    res = fuse(_recs(garbage), llm_fn=lambda p: "", scorer=scorer, veto_margin=0.0)
    assert [v["label"] for v in res.vetoed] == ["kraken-mccatmus"]
    assert res.text == TRACE["kraken-catmus"] and res.provenance[0].source == "veto"
    same = {"a": TRACE["trocr"], "b": TRACE["trocr"], "c": TRACE["trocr"]}
    res = fuse(_recs(same), llm_fn=lambda p: "", scorer=scorer, veto_margin=0.0)
    assert res.vetoed == [] and len(res.scores) == 3 and res.text == TRACE["trocr"]


def test_the_vote_runs_on_the_survivors_and_says_so(scorer):
    good = TRACE["trocr"]
    cands = {"a": good, "b": good.replace("grus", "gruss"), "c": good.replace("liebe", "lieben"),
             "garbage": TRACE["kraken-mccatmus"]}
    res = fuse(_recs(cands, {"a": 0.9, "b": 0.9, "c": 0.9, "garbage": 0.99}),
               llm_fn=lambda p: "", scorer=scorer, veto_margin=2.0, arbitrate=False)
    assert [v["label"] for v in res.vetoed] == ["garbage"]
    assert res.strategy == "vote+veto" and res.text == good and res.n_candidates == 4
    assert not any("garbage" in sp.backers for sp in res.provenance)


# ── the orchestrator asks for the cell Agent B and the pins describe ────────

def test_the_orchestrator_hook_reads_agent_b_and_the_pins(monkeypatch):
    import config
    import orchestrator
    from agent_a.model_selector import SourceCriteria
    sr._MATRIX.clear()
    ctx = orchestrator.PipelineContext("doc")
    ctx.description = {"source_description": "Urkunde", "source_json": {}}
    monkeypatch.setattr(config, "FUSION_SCORER_VETO", False)
    assert orchestrator._veto_scorer_for(ctx, "p.jpg") is None

    monkeypatch.setattr(config, "FUSION_SCORER_VETO", True)
    monkeypatch.setattr(config, "FUSION_SCORER_DIR", str(FIX))
    monkeypatch.setattr(SourceCriteria, "from_agent_b_and_json",
                        classmethod(lambda cls, d, j: SourceCriteria(lang="de", region="Swiss",
                                                                     document_type="charter", century=15)))
    import runstate

    class _State:
        criteria = {}
    monkeypatch.setattr(runstate.RunState, "load", classmethod(lambda cls, doc_id, path=None: _State()))
    assert orchestrator._veto_scorer_for(ctx, "p.jpg").cell.key() == "de|Swiss|charter|15-16"
    _State.criteria = {"century": 19}                      # the historian pinned the century
    assert orchestrator._veto_scorer_for(ctx, "p.jpg") is None
    sr._MATRIX.clear()


def test_config_defaults_keep_the_veto_off():
    import config
    assert config.FUSION_SCORER_VETO is False
    assert config.FUSION_VETO_MARGIN == 1.5
    assert config.FUSION_SCORER_DIR.endswith("scorers")
