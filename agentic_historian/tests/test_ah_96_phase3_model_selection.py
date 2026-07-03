"""Tests for #96: Phase 3 crashes — select_best_kraken_model contract mismatch.

Two real crashes were present:
  1. orchestrator/dual_pipeline treated select_best_kraken_model (single
     KrakenModel|None) as a list -> TypeError: not subscriptable, and used
     .matched_fields (which does not exist; ModelMatch has .matched_on).
  2. model_selector.score_model referenced an undefined name `ns` -> NameError,
     crashing ALL model scoring.

Offline: no network, kraken client is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_96_phase3_model_selection.py
"""

import sys
from pathlib import Path

PKG = str(Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from agent_a.model_selector import select_kraken_model, SourceCriteria, ModelMatch

_DESC = "Gotische Kursive, deutsch, 15. Jahrhundert, Urkunde"


# ── The contract Phase 3 relies on ───────────────────────────────────────────

def test_select_kraken_model_returns_list_of_matches():
    matches = select_kraken_model(SourceCriteria.from_agent_b(_DESC), top_k=3)
    assert isinstance(matches, list)
    assert matches, "expected at least one model match for a gothic-cursive desc"
    top = matches[0]
    assert isinstance(top, ModelMatch)
    assert hasattr(top, "model") and hasattr(top, "score")
    assert hasattr(top, "matched_on"), "ModelMatch exposes .matched_on"
    assert not hasattr(top, "matched_fields"), "there is no .matched_fields"


def test_score_model_no_nameerror_over_all_models():
    """Regression for the `ns` NameError: scoring every model must not raise,
    including scripts that hit the fuzzy/partial script branch."""
    for desc in (_DESC, "Humanistische Kursive latein 16. Jh.", "bastarda", "unknown"):
        matches = select_kraken_model(SourceCriteria.from_agent_b(desc), top_k=5)
        assert isinstance(matches, list)  # no exception is the assertion


# ── Functional: Phase 3 no longer crashes at model selection ─────────────────

def test_phase3_runs_without_typeerror(monkeypatch, tmp_path):
    import orchestrator

    class _FakeOCR:
        text = "hallo welt"
        confidence = 0.88

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def transcribe(self, **kwargs):
            return _FakeOCR()

    monkeypatch.setattr(orchestrator, "KrakenHTTPClient", lambda *a, **k: _FakeClient())

    img = tmp_path / "scan.jpg"
    img.write_bytes(b"\xff\xd8\xff")  # not read (client mocked)

    result = orchestrator._rerun_kraken_with_model_selection(img, _DESC)

    assert result["kraken_model"] is not None, "a model should be selected"
    assert result["kraken_transcription"] == "hallo welt"
    assert not result["error_kraken"], f"unexpected error: {result['error_kraken']}"


# ── Source guards against the buggy pattern returning ────────────────────────

def test_callers_use_ranked_api_not_single_wrapper():
    for rel in ("orchestrator.py", "agent_a/dual_pipeline.py"):
        src = Path(PKG, rel).read_text()
        # the fixed callers use select_kraken_model + .matched_on
        assert "select_kraken_model(" in src, f"{rel}: must use select_kraken_model"
        assert ".matched_fields" not in src, f"{rel}: .matched_fields does not exist"
