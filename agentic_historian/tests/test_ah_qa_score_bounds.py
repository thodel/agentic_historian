"""The agreement-based QA score must stay in [0, 1].

QA is computed as 1 - max_pairwise_cer. CER is unbounded above — it is
edits/reference-length, so candidates that disagree in length as well as content
exceed 100%. A real tei run on 2026-07-17 measured 194.8% CER across seven engines
(a Middle Latin model and a German Kurrent model reading the same page), producing:

    QA -0.95   → averaged into the order score → written into the .txt header

"Negative quality" is not something a historian can act on, and it silently poisons
any downstream averaging. Total disagreement is 0.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_qa_score_bounds.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402
from agent_a.model_selector import RecognitionResult  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    def make(cer):
        def fake(img, criteria):
            return SimpleNamespace(
                recognitions=[RecognitionResult(engine="trocr", model_id="t0",
                                                text="eine lesart", confidence=0.9)],
                text="eine lesart", loops=1, max_pairwise_cer=cer)
        monkeypatch.setattr(orchestrator, "_recognize_page_ensemble", fake)

    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator.config, "ENABLE_ENSEMBLE_HTR", True)
    monkeypatch.setattr(orchestrator.agent_a, "save_transcription", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: {"source_description": "", "source_json": {},
                                     "low_confidence": True})
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities", lambda *a, **k: {})
    monkeypatch.setattr(orchestrator, "_save_pipeline_result", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_publish_outputs", lambda *a, **k: (False, "off"))
    monkeypatch.setattr(orchestrator.config, "DATA_DIR", tmp_path)

    img = tmp_path / "p1.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return SimpleNamespace(make=make, img=img)


def test_cer_above_100_percent_does_not_produce_a_negative_qa(rig):
    """The regression: 194.8% CER gave QA -0.95 in production."""
    rig.make(1.948)
    result = orchestrator.run_full_pipeline_group("order-qa", [str(rig.img)])
    assert result["a_meta"]["qa_score"] == 0.0


def test_total_agreement_is_one(rig):
    rig.make(0.0)
    result = orchestrator.run_full_pipeline_group("order-qa", [str(rig.img)])
    assert result["a_meta"]["qa_score"] == 1.0


def test_partial_agreement_is_unchanged(rig):
    """The clamp must not distort the normal range."""
    rig.make(0.30)
    result = orchestrator.run_full_pipeline_group("order-qa", [str(rig.img)])
    assert result["a_meta"]["qa_score"] == 0.7
