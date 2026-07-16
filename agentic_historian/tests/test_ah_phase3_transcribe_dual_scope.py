"""Phase 3 must actually run on a default (use_dual_htr=False) pipeline.

Regression for a scoping bug that silently killed the kraken re-run for three
weeks (introduced 1d47069c, 2026-06-26). Phase 1's `from agent_a.dual_pipeline
import transcribe_dual` bound the name **function-locally** for the whole of
`run_full_pipeline`, shadowing the module-level import. That import only executes
when `use_dual_htr=True` — but every production caller (`__main__`, the bot's
hot-watch, the batch loop) uses the default `False`. So Phase 3's call site hit an
UnboundLocalError, which the phase's `except Exception` swallowed into ctx.errors.

Effect: kraken/TrOCR never ran, nothing was reconciled or fused, and the final
transcription was silently VLM-only — the exact single-engine condition behind the
u-17__/BAT repetition-collapse garbage.

Offline — transcribe_dual and the agents are stubbed. Run from the repo root:
    pytest agentic_historian/tests/test_ah_phase3_transcribe_dual_scope.py
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

KRAKEN_TEXT = "unser fruntlich gruos vor liebe getruwe"
VLM_TEXT = "uuuu uuuu uuuu uuuu"          # the collapse Phase 3 is meant to outvote


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """A default single-doc run: use_dual_htr=False, kraken available."""
    calls = []
    monkeypatch.setattr(orchestrator, "DUAL_AVAILABLE", True)
    monkeypatch.setattr(orchestrator, "refresh_kraken_registry", None)
    monkeypatch.setattr(orchestrator.config, "ENABLE_MULTI_ENGINE_FUSION", False)
    monkeypatch.setattr(orchestrator.agent_a, "process_file",
                        lambda p, **k: {"transcription": VLM_TEXT, "qa_score": 0.8})
    monkeypatch.setattr(orchestrator.agent_b, "describe",
                        lambda **k: {"source_description": "Urkunde, Kurrent",
                                     "source_json": {"schrift": "Kurrent"}})
    monkeypatch.setattr(orchestrator.agent_c, "extract_entities",
                        lambda *a, **k: {"entities": []})
    monkeypatch.setattr(orchestrator, "_save_pipeline_result", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "_publish_outputs", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.config, "DATA_DIR", tmp_path)

    def fake_dual(img, **kwargs):
        calls.append(kwargs)
        # Phase 1 (use_dual_htr=True) reads the vlm_* fields; Phase 3 reads the
        # kraken_* ones. One stub serves both call sites.
        return SimpleNamespace(
            recognitions=[RecognitionResult(engine="kraken", model_id="catmus-medieval",
                                            text=KRAKEN_TEXT, confidence=0.87)],
            kraken_transcription=KRAKEN_TEXT, party_transcription="",
            error_kraken="", error_party="",
            vlm_transcription=VLM_TEXT, vlm_score=0.8,
            to_dict=lambda: {"qa_score": 0.8, "source": "dual"})
    monkeypatch.setattr(orchestrator, "transcribe_dual", fake_dual)
    monkeypatch.setattr(orchestrator, "reconcile", lambda a, b: SimpleNamespace(
        reconciled=b, method="llm", agreement_score=0.42))

    img = tmp_path / "d-phase3.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    return SimpleNamespace(img=img, calls=calls)


def test_phase3_runs_on_a_default_pipeline(pipeline):
    """The default path must reach the kraken re-run. Before the fix this raised
    UnboundLocalError and was swallowed, so Phase 3 never executed at all."""
    result = orchestrator.run_full_pipeline(str(pipeline.img))

    assert pipeline.calls, "Phase 3 never called transcribe_dual"
    assert pipeline.calls[0]["run_kraken"] is True
    assert not [e for e in result["errors"] if e.get("agent") == "kraken_rerun"]


def test_phase3_output_reaches_the_final_transcription(pipeline):
    """Not just 'it ran' — the kraken reading must actually replace the VLM
    collapse, which is the whole point of the re-run."""
    result = orchestrator.run_full_pipeline(str(pipeline.img))

    assert result["transcription"] == KRAKEN_TEXT
    assert result["transcription"] != VLM_TEXT
    assert any(r["engine"] == "kraken" for r in result["recognitions"])


def test_phase3_still_runs_when_dual_htr_is_requested(pipeline, monkeypatch):
    """use_dual_htr=True was the one path that worked; keep it working."""
    result = orchestrator.run_full_pipeline(str(pipeline.img), use_dual_htr=True)

    assert pipeline.calls, "transcribe_dual was never called"
    assert result["transcription"]
