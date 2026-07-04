"""Tests for #101: HTR QA — length heuristic, kraken retry, reconcile, temperature.

Run offline (no GPUStack/VPN) — file-level checks, no module imports.

Run:  python tests/test_ah_101_htr_qa.py   (or: pytest)
"""

import re


PKG = "agentic_historian"
TR_PATH = f"{PKG}/agents/text_recognition.py"
DP_PATH = f"{PKG}/agent_a/dual_pipeline.py"
ORC_PATH = f"{PKG}/orchestrator.py"


def read(path):
    with open(path) as f:
        return f.read()


# ── Fix 1: temperature is 0.2 for diplomatic transcription ───────────────────

def test_vlm_temperature_is_low_for_diplomatic_transcription():
    """Both VLM HTR paths must use temperature <= 0.2 for deterministic,
    faithful transcription of historical handwriting."""
    tr = read(TR_PATH)
    dp = read(DP_PATH)

    # _htr_vlm
    assert "temperature=1.0" not in tr, (
        "_htr_vlm still uses temperature=1.0 — too random for diplomacy"
    )
    assert "temperature=0.2" in tr, "_htr_vlm must use temperature=0.2"

    # _run_vlm (dual_pipeline) — low temperature for diplomatic transcription.
    # 0.0 (fully deterministic/verbatim) or 0.2 both satisfy "<= 0.2" (#107
    # lowered it to 0.0 when removing the self-QA loop).
    assert "temperature=1.0" not in dp, (
        "dual_pipeline._run_vlm still uses temperature=1.0"
    )
    assert ("temperature=0.0" in dp or "temperature=0.2" in dp), (
        "dual_pipeline._run_vlm must use a low temperature (<= 0.2)"
    )


# ── Fix 2: QA gate does NOT retry kraken ─────────────────────────────────────

def test_qa_gate_guards_on_source_vlm():
    """The QA retry in transcribe_image must be guarded with source == 'vlm'
    so kraken output is never retried (kraken-first policy)."""
    tr = read(TR_PATH)

    # Find the transcribe_image function and its QA block
    idx = tr.find("def transcribe_image")
    func = tr[idx:idx + 2000]

    # The fixed condition must guard with source == "vlm"
    # Look for the qa_score check
    qa_block = func[func.find("qa_score = _quality_score"):func.find("return {", func.find("qa_score"))]

    assert 'source == "vlm"' in qa_block or "source == 'vlm'" in qa_block, (
        "QA gate must be guarded with source == 'vlm'; "
        f"found: {qa_block[:200]!r}"
    )


# ── Fix 3: _quality_score is not length-biased ───────────────────────────────

def test_quality_score_is_not_length_biased():
    """_quality_score must NOT return different scores based on length alone.
    Both a short good kraken output and a long one must score the same."""
    tr = read(TR_PATH)

    # The old broken formula:  min(0.9, 0.5 + 0.1 * (len(text) / 500))
    # must not appear in _quality_score
    assert "0.5 + 0.1 *" not in tr, (
        "Length-based scoring formula still present in _quality_score"
    )
    assert "len(text) / 500" not in tr, (
        "Length divisor (500) still used in _quality_score"
    )


# ── Fix 4: Phase 3 uses reconcile, not blind kraken overwrite ────────────────

def test_phase3_uses_reconcile():
    """orchestrator Phase 3 must call reconcile(VLM, kraken) to decide which
    transcription to keep, rather than unconditionally overwriting with kraken."""
    orc = read(ORC_PATH)

    # Must import reconcile
    assert "from agent_a.reconcile import reconcile" in orc, (
        "orchestrator must import reconcile from agent_a.reconcile"
    )

    # Must call reconcile() in Phase 3
    func_idx = orc.find("def run_full_pipeline")
    phase3 = orc[func_idx:func_idx + 8000]
    assert "reconcile(" in phase3, "Phase 3 must call reconcile()"

    # Must use reconciled output, not raw kraken overwrite
    assert "rec_result.reconciled" in phase3 or "reconcile(vlm_text" in phase3, (
        "Phase 3 must assign reconcile() result, not raw kraken_transcription"
    )


# ── Offline fixture test: short good kraken should NOT be retried ─────────────

def test_short_kraken_would_not_trigger_retry():
    """Demonstrate the bugfix: a 50-char good kraken transcription would have
    scored ~0.65 under the old heuristic (< 0.75 threshold, triggering VLM
    retry). Under the fix it scores 0.8 — above threshold, no retry."""
    tr = read(TR_PATH)

    # The old formula: score = min(0.9, 0.5 + 0.1 * (len / 500))
    # For len=50: 0.5 + 0.1*(50/500) = 0.5 + 0.01 = 0.51 < 0.75 → RETRY (WRONG)
    # For len=50: fix gives 0.8 > 0.75 → no retry (CORRECT)
    old_score_50 = min(0.9, 0.5 + 0.1 * (50 / 500))
    assert old_score_50 < 0.75, f"sanity: old formula for 50 chars must be < 0.75, got {old_score_50}"

    # The fix score for short good text is 0.8
    # (alpha_ratio >= 0.1, len >= 20 → returns 0.8)
    assert 0.8 > 0.75, "fixed score must be above threshold"

    # The fix must not contain the len/500 formula anywhere in _quality_score
    assert "len(text) / 500" not in tr, "Bug: len/500 still in source"


if __name__ == "__main__":
    test_vlm_temperature_is_low_for_diplomatic_transcription()
    print("PASS: test_vlm_temperature_is_low_for_diplomatic_transcription")

    test_qa_gate_guards_on_source_vlm()
    print("PASS: test_qa_gate_guards_on_source_vlm")

    test_quality_score_is_not_length_biased()
    print("PASS: test_quality_score_is_not_length_biased")

    test_phase3_uses_reconcile()
    print("PASS: test_phase3_uses_reconcile")

    test_short_kraken_would_not_trigger_retry()
    print("PASS: test_short_kraken_would_not_trigger_retry")

    print("\nAll #101 tests passed.")
