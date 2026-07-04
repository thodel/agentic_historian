"""Tests for #107: dual_pipeline VLM self-QA is broken (self-referential, regex, temperature).

The VLM path asked the SAME VLM to grade its own transcription (worthless
signal), parsed it with a regex that missed integer scores, and truncated at
max_tokens=50. Removed: QA is now an independent heuristic and the transcription
runs at temperature 0.0 (diplomatic/verbatim).

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_107_dual_selfqa.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

SRC = (PKG / "agent_a" / "dual_pipeline.py").read_text(encoding="utf-8")


def test_no_self_qa_prompt_remains():
    for marker in ("Begutachte", "QC-Score", "Vergib einen"):
        assert marker not in SRC, f"self-QA prompt marker still present: {marker}"


def test_quality_score_is_independent_heuristic():
    from agent_a import dual_pipeline as dp
    assert dp._quality_score("") == 0.0
    assert dp._quality_score(".,;:!?---") == 0.2
    assert dp._quality_score("Hans") == 0.3
    assert dp._quality_score("Wir Hans von Wiler tuend kund allen den die disen brief") == 0.8


def test_run_vlm_calls_model_exactly_once(monkeypatch, tmp_path):
    """The producing VLM must be called ONCE (transcription) — not a second
    time to grade itself. And transcription must use temperature 0.0."""
    from agent_a import dual_pipeline as dp

    calls = []

    def _fake_chat_vision(prompt, image_source, temperature=None, max_tokens=None, **kw):
        calls.append({"temperature": temperature, "prompt": prompt})
        return "Wir Hans von Wiler tuend kund allen den die disen brief ansehent"

    monkeypatch.setattr(dp.gs, "chat_vision", _fake_chat_vision)

    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    text, score = dp._run_vlm(img)

    assert len(calls) == 1, f"VLM called {len(calls)} times — self-QA not removed"
    assert calls[0]["temperature"] == 0.0, "diplomatic transcription must be temperature 0.0"
    assert text.startswith("Wir Hans")
    # score comes from the independent heuristic, not the model
    assert score == dp._quality_score(text)


def test_run_vlm_handles_failure(monkeypatch, tmp_path):
    from agent_a import dual_pipeline as dp

    def _boom(*a, **k):
        raise RuntimeError("gpustack down")

    monkeypatch.setattr(dp.gs, "chat_vision", _boom)
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff")
    text, score = dp._run_vlm(img)
    assert text == "" and score == 0.0
