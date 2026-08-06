"""#320 (cause 2): /run must use the same recognition machinery as a grouped order.

The single-doc path used `transcribe_dual` (VLM + kraken + party) while the grouped
path ran the multi-engine ensemble. So /run got no TrOCR, no #300 no-merge band and
no #299 criteria re-run — every recognition-quality fix from #298 reached grouped
orders and not /run, which still produced `uuuu` garbage on the very page a grouped
run reads correctly.

Offline — the ensemble is stubbed; no GPUStack, no gateway. Run from the repo root:
    pytest agentic_historian/tests/test_ah_320_single_doc_ensemble.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config          # noqa: E402
import orchestrator    # noqa: E402


class _Rec(SimpleNamespace):
    pass


def _rec(engine, model, text):
    return _Rec(engine=engine, model_id=model, text=text, error="", confidence=0.5)


ENSEMBLE_RECS = [
    _rec("vlm", "internvl3-8b-instruct", "uuuuuuuu"),
    _rec("kraken", "kraken-early_modern_german", "luser femitlich"),
    _rec("trocr", "trocr-medieval-escriptmask", "unser früntlich grůs"),
]


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """Records what the pipeline asked the ensemble to do."""
    calls = []

    def _fake_ensemble(img, criteria):
        calls.append({"page": Path(img).name, "criteria": criteria})
        return SimpleNamespace(
            recognitions=list(ENSEMBLE_RECS), text="unser früntlich grůs",
            max_pairwise_cer=0.96, loops=1, no_merge=True,
            selected=ENSEMBLE_RECS[2], ran=[])

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", _fake_ensemble)
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription",
                        lambda *a, **k: None)
    return calls


# ── the ensemble reaches the single-doc path at all ──────────────────────────

def test_the_single_doc_phase1_runs_the_ensemble(stub, tmp_path):
    ctx = orchestrator.PipelineContext("d")
    img = tmp_path / "BAT_664_r_00027.jpg"
    img.write_bytes(b"\x00")

    from agent_a.model_selector import SourceCriteria
    parts, scores = orchestrator._ensemble_pass(
        [img], SourceCriteria(), ctx, "d", None, label="pass 1", page_headers=False)

    assert stub, "the ensemble was never invoked"
    assert parts == ["unser früntlich grůs"]


def test_all_three_engines_reach_the_candidates(stub, tmp_path):
    """TrOCR in the mix is the acceptance criterion — it was absent from /run."""
    ctx = orchestrator.PipelineContext("d")
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")

    from agent_a.model_selector import SourceCriteria
    orchestrator._ensemble_pass([img], SourceCriteria(), ctx, "d", None,
                                label="pass 1", page_headers=False)

    engines = {r.engine for r in ctx.recognitions}
    assert engines == {"vlm", "kraken", "trocr"}


# ── page headers: right for an order, wrong for one page ─────────────────────

def test_a_single_doc_transcription_has_no_page_header(stub, tmp_path):
    """`--- page.jpg ---` keeps a multi-page order attributable, but on /run it
    would be new noise in an output format that never had it."""
    ctx = orchestrator.PipelineContext("d")
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")

    from agent_a.model_selector import SourceCriteria
    parts, _ = orchestrator._ensemble_pass([img], SourceCriteria(), ctx, "d", None,
                                           label="pass 1", page_headers=False)
    assert "---" not in parts[0]


def test_a_grouped_order_keeps_its_page_headers(stub, tmp_path):
    ctx = orchestrator.PipelineContext("d")
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")

    from agent_a.model_selector import SourceCriteria
    parts, _ = orchestrator._ensemble_pass([img], SourceCriteria(), ctx, "d", None,
                                           label="pass 1")
    assert parts[0].startswith("--- p.jpg ---")


# ── the #299 criteria re-run, shared by both pipelines ───────────────────────

def _ctx_with_description(doc_id="d-rerun"):
    ctx = orchestrator.PipelineContext(doc_id)
    ctx.transcription = "uuuuuuuu"
    ctx.a_meta = {"qa_score": 0.1}
    ctx.description = {
        "source_description": "Kurrentschrift, 15. Jahrhundert, deutsch",
        "source_json": {"Schrift": {"wert": "Kurrent"},
                        "Sprache": {"wert": "Mittelhochdeutsch"},
                        "Datierung": {"wert": "15. Jahrhundert"}},
    }
    return ctx


def test_the_criteria_rerun_reaches_the_single_doc_path(stub, tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    ctx = _ctx_with_description()

    text, qa = orchestrator._criteria_rerun(
        [img], ctx, "d-rerun", None, avg_qa=0.1,
        source_tag="single-ensemble-criteria", page_headers=False)

    assert ctx.a_meta.get("criteria_rerun") is True
    assert text == "unser früntlich grůs"
    assert ctx.a_meta["source"] == "single-ensemble-criteria"


def test_the_rerun_uses_agent_bs_criteria_not_blind_ones(stub, tmp_path):
    """The whole point of #299: read again with the model the description implies."""
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    orchestrator._criteria_rerun([img], _ctx_with_description(), "d", None,
                                 avg_qa=0.1, source_tag="t", page_headers=False)

    crit = stub[-1]["criteria"]
    assert crit.script == "kurrent" and crit.century == 15
    assert crit.lang == "de", "the #348 normalisation must hold here too"


def test_no_criteria_means_no_rerun(stub, tmp_path):
    """A re-run with nothing to match on would just repeat Phase 1 blind."""
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    ctx = _ctx_with_description()
    ctx.description = {"source_description": "", "source_json": {}}

    text, qa = orchestrator._criteria_rerun([img], ctx, "d", None, avg_qa=0.1,
                                            source_tag="t", page_headers=False)
    assert not stub
    assert text == "uuuuuuuu" and qa == 0.1


def test_a_missing_description_leaves_the_transcription_alone(stub, tmp_path):
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    ctx = _ctx_with_description()
    ctx.description = None

    text, qa = orchestrator._criteria_rerun([img], ctx, "d", None, avg_qa=0.1,
                                            source_tag="t", page_headers=False)
    assert text == "uuuuuuuu" and qa == 0.1 and not stub


def test_a_failing_rerun_does_not_lose_the_phase1_transcription(stub, tmp_path,
                                                                monkeypatch):
    """Degrading to the blind reading is bad; losing the run is worse."""
    def _boom(img, criteria):
        raise RuntimeError("gateway down")
    monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", _boom)

    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")
    ctx = _ctx_with_description()
    text, qa = orchestrator._criteria_rerun([img], ctx, "d", None, avg_qa=0.1,
                                            source_tag="t", page_headers=False)
    assert text == "uuuuuuuu"


# ── the no-merge card must now appear for /run too ───────────────────────────

def test_a_single_doc_no_merge_records_vote_paths(stub, tmp_path):
    """#313's card was only ever reachable from a grouped order."""
    from runstate import RunState
    ctx = orchestrator.PipelineContext("d-vote")
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\x00")

    from agent_a.model_selector import SourceCriteria
    orchestrator._ensemble_pass([img], SourceCriteria(), ctx, "d-vote", None,
                                label="pass 1", page_headers=False)

    st = RunState.load_or_new("d-vote")
    assert len(st.artifacts.get("paths") or {}) == 3
    assert st.gate_decisions.get("gate2_vote_warranted") is True
