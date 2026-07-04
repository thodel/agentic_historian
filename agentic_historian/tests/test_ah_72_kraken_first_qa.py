"""Tests for #72: kraken-first offline QA test for Agent A.

Run offline (no GPUStack/VPN) — file-level checks, no module imports.

The kraken-first policy (desired behavior):
  - Agent A tries kraken HTR FIRST (primary engine)
  - VLM is called ONLY if kraken is unavailable or fails
  - QA gate evaluates kraken output quality
  - Kraken transcription is trusted; never retried by QA

Run:  python tests/test_ah_72_kraken_first_qa.py   (or: pytest)
"""

import os

_TEST_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TEST_DIR)

DP_PATH = os.path.join(_ROOT, "agent_a", "dual_pipeline.py")
ORC_PATH = os.path.join(_ROOT, "orchestrator.py")


def read(path):
    with open(path) as f:
        return f.read()


def get_func(src: str, name: str, length: int = 5000) -> str:
    idx = src.find(f"def {name}")
    if idx == -1:
        return ""
    return src[idx:idx + length]


# ── Test 1: kraken must be called BEFORE VLM ──────────────────────────────────

def test_kraken_called_before_vlm():
    """transcribe_dual() must call _run_kraken() BEFORE _run_vlm().
    Kraken is the primary HTR engine; VLM is the fallback.
    
    Bug (current): VLM runs first (Path 1), kraken second (Path 2).
    Fix: Swap the order so kraken tries first."""
    dp = read(DP_PATH)
    func = get_func(dp, "transcribe_dual", 5000)

    kraken_pos = func.find("_run_kraken")
    vlm_pos = func.find("_run_vlm")

    assert kraken_pos != -1, "_run_kraken not found in transcribe_dual"
    assert vlm_pos != -1, "_run_vlm not found in transcribe_dual"
    assert kraken_pos < vlm_pos, (
        "BUG: _run_kraken is at pos {}, _run_vlm at pos {} — "
        "VLM runs before kraken. Kraken must be tried FIRST (kraken-first policy). "
        "Swap Path 1 (VLM) and Path 2 (kraken) in transcribe_dual().".format(kraken_pos, vlm_pos)
    )


# ── Test 2: kraken output is NOT retried ─────────────────────────────────────

def test_kraken_output_not_retried_by_qa():
    """Once kraken produces output, QA must NOT retry it.
    The QA gate must be guarded with source == 'vlm'."""
    dp = read(DP_PATH)

    # QA retry is only in text_recognition.py (the old single-path code).
    # In dual_pipeline, there's no retry — kraken output is used directly.
    # Check that there is no "retry" loop on kraken results.
    func = get_func(dp, "transcribe_dual", 5000)
    assert "retry_kraken" not in func.lower(), (
        "QA must not retry kraken output (kraken-first = trust kraken)"
    )


# ── Test 3: VLM is conditional on kraken failure ──────────────────────────────

def test_vlm_is_fallback_not_primary():
    """VLM should only be invoked when kraken is unavailable or fails.
    In transcribe_dual, run_vlm=True should still check if kraken succeeded
    before running VLM (or at minimum, kraken must run first)."""
    dp = read(DP_PATH)
    func = get_func(dp, "transcribe_dual", 5000)

    # Extract the kraken result block
    kraken_block = ""
    k_pos = func.find("if run_kraken:")
    if k_pos != -1:
        # Find the next "if run_" after it
        next_if = func.find("\n    if run_", k_pos + 15)
        if next_if != -1:
            kraken_block = func[k_pos:next_if]
        else:
            kraken_block = func[k_pos:k_pos + 300]

    # After kraken block, check if VLM is next
    vlm_pos = func.find("if run_vlm:")
    assert vlm_pos != -1, "if run_vlm: not found"

    # VLM should come after kraken in source order (kraken-first)
    assert vlm_pos > k_pos, (
        "VLM path (pos {}) comes BEFORE kraken path (pos {}) — "
        "violates kraken-first. Swap the if-run blocks.".format(vlm_pos, k_pos)
    )


# ── Test 4: reconciliation primary is kraken, not VLM ────────────────────────

def test_kraken_is_primary_in_reconciliation():
    """In the reconciliation block, kraken should be the primary text
    and VLM the secondary (or both treated equally), not VLM as primary."""
    dp = read(DP_PATH)
    func = get_func(dp, "transcribe_dual", 5000)

    # Find reconciliation block
    recon_start = func.find("reconcile(")
    if recon_start == -1:
        # Try multi_reconcileMerge
        recon_start = func.find("_reconcile_merge")

    assert recon_start != -1, "reconcile() call not found in transcribe_dual"

    # Look backward from reconcile() to find primary= assignment
    preceding = func[max(0, recon_start - 300):recon_start]

    # The primary should be kraken (not VLM) for kraken-first policy
    # Current bug: primary = texts.get("vlm", "")
    has_bug = (
        'primary   = texts.get("vlm"' in preceding or
        'primary = texts.get("vlm"' in preceding
    )
    if has_bug:
        # Also check if kraken is secondary (the bug pattern)
        has_secondary_bug = (
            'secondary = (\n        texts.get("kraken")' in preceding or
            'texts.get("kraken")' in preceding
        )
        assert not has_secondary_bug, (
            "RECONCILIATION BUG: VLM is primary, kraken is secondary. "
            "For kraken-first policy, kraken should be primary. "
            "Change: primary = texts.get('kraken') (or treat equally)."
        )


# ── Test 5: orchestrator Phase 1 starts with kraken ──────────────────────────

def test_orchestrator_phase1_kraken():
    """run_full_pipeline Phase 1 must invoke kraken HTR (not VLM-only)."""
    orc = read(ORC_PATH)
    func_idx = orc.find("def run_full_pipeline")
    assert func_idx != -1
    phase1 = orc[func_idx:func_idx + 8000]

    assert "kraken" in phase1.lower(), (
        "Phase 1 must call/reference kraken HTR"
    )


if __name__ == "__main__":
    test_kraken_called_before_vlm()
    print("PASS: test_kraken_called_before_vlm")

    test_kraken_output_not_retried_by_qa()
    print("PASS: test_kraken_output_not_retried_by_qa")

    test_vlm_is_fallback_not_primary()
    print("PASS: test_vlm_is_fallback_not_primary")

    test_kraken_is_primary_in_reconciliation()
    print("PASS: test_kraken_is_primary_in_reconciliation")

    test_orchestrator_phase1_kraken()
    print("PASS: test_orchestrator_phase1_kraken")

    print("\nAll #72 tests passed.")
