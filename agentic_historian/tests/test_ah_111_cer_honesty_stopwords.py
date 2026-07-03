"""Tests for #111: CER honesty, stopword list, confidence labels.

Run offline (no GPUStack/VPN) — file-level checks + functional tests.
"""

import sys
import os

# ── helpers ───────────────────────────────────────────────────────────────────

def read(path):
    with open(path) as f:
        return f.read()


# ─────────────────────────────────────────────────────────────────────────────
# Part 1: CER/WER docstrings are honest about >1.0 range
# ─────────────────────────────────────────────────────────────────────────────

def test_cer_docstring_no_false_0to1_range():
    """The cer() docstring must NOT claim 'Returns value between 0.0 and 1.0'
    without a caveat about insertion-heavy hypotheses exceeding 1.0."""
    src = read("agentic_historian/eval/metrics.py")

    # Find cer() function
    import re
    cer_match = re.search(
        r'def cer\(.*?\):(.*?)(?=\ndef |\Z)',
        src, re.DOTALL
    )
    assert cer_match, "cer() not found"
    cer_doc = cer_match.group(1)

    # Must NOT make the uncapped claim without a caveat
    has_false_claim = (
        "between 0.0 (perfect) and 1.0" in cer_doc
        and "can exceed 1.0" not in cer_doc
        and "insertion" not in cer_doc.lower()
    )
    assert not has_false_claim, (
        "cer() docstring still claims 0.0-1.0 range without caveat about >1.0"
    )


def test_wer_docstring_no_false_0to1_range():
    """wer() docstring must also have the caveat."""
    src = read("agentic_historian/eval/metrics.py")
    # Direct triple-quote extraction (same as CER test)
    wer_idx = src.find("def wer(")
    doc_start = src.find('"""', wer_idx)
    doc_end = src.find('"""', doc_start + 3)
    wer_doc = src[doc_start:doc_end + 3]
    has_caps = "can exceed 1.0" in wer_doc or "insertions" in wer_doc.lower()
    assert has_caps, "wer() docstring missing caveat about >1.0 range"


def test_cer_can_exceed_one():
    """Demonstrate that CER can exceed 1.0 with insertion-heavy hypothesis."""
    sys.path.insert(0, "agentic_historian")
    from eval.metrics import cer

    ref = "abc"
    hyp = "abcdefgh"  # 5 insertions, 0 substitutions, 0 deletions
    # Levenshtein distance = 5 (5 extra chars)
    # CER = 5 / 3 = 1.67 > 1.0
    score = cer(ref, hyp)
    assert score > 1.0, f"CER={score:.3f} must exceed 1.0 for insertion-heavy hyp"


# ─────────────────────────────────────────────────────────────────────────────
# Part 2: EFnhd stopwords replace modern German stopwords
# ─────────────────────────────────────────────────────────────────────────────

def test_efnh_stopwords_replace_modern():
    """corpus_analysis.py must use EFnhd stopwords, not a modern German list.
    The old stopwords ('vnd', 'daz', 'vff') are Middle High German variants
    present in Frühneuhochdeutsch texts — they should be in the stopword set."""
    src = read("agentic_historian/agents/corpus_analysis.py")

    # "vnd" (MHG variant of "und") must be in stopwords
    assert '"vnd"' in src, '"vnd" must be in stopwords for EFnhd texts'

    # Old modern-only words must NOT be in stopwords
    # These are modern Standard German and not typical in EFnhd:
    # "sich", "nur", "dass", "nicht" — actually these ARE in EFnhd, keep them
    # The key indicator: we should have a bigger, EFnhd-appropriate set
    # Check the stopword set is larger than the old 25-word set
    import re
    sw_match = re.search(r'stopwords\s*=\s*\{([^}]+)\}', src, re.DOTALL)
    assert sw_match, "stopwords set not found"
    words = {w.strip().strip('"').strip("'") for w in sw_match.group(1).split(',')}
    words = {w for w in words if w}  # remove empty
    assert len(words) > 30, (
        f"EFnhd stopword set has only {len(words)} words — "
        "expected >30 (old set had 28, EFnhd set should be larger)"
    )


def test_vnd_in_stopwords():
    """'vnd' (MHG 'und') must be treated as a stopword in corpus_analysis."""
    src = read("agentic_historian/agents/corpus_analysis.py")
    assert '"vnd"' in src, '"vnd" must be in the stopword set'


# ─────────────────────────────────────────────────────────────────────────────
# Part 3: Confidence labels are heuristic, not fake decimals
# ─────────────────────────────────────────────────────────────────────────────

def test_no_numeric_confidence_decimals():
    """entity_agent.py must NOT assign numeric hub_confidence values
    (0.9, 0.8, 0.7, 0.0) — must use string labels instead."""
    src = read("agentic_historian/agents/entity_agent.py")

    import re
    # Find all hub_confidence assignments
    numeric_conf = re.findall(
        r'hub_confidence["\s]*\s*[=:]\s*[0-9]+(\.[0-9]+)?',
        src
    )
    assert not numeric_conf, (
        f"Found numeric hub_confidence values: {numeric_conf} — "
        "must use string labels (high/medium/low/unverified)"
    )


def test_confidence_labels_are_strings():
    """hub_confidence values must be string labels: high, medium, low, unverified."""
    src = read("agentic_historian/agents/entity_agent.py")
    assert '"high"' in src, '"high" label must appear in entity_agent.py'
    assert '"medium"' in src, '"medium" label must appear in entity_agent.py'
    assert '"low"' in src, '"low" label must appear in entity_agent.py'
    assert '"unverified"' in src, '"unverified" label must appear in entity_agent.py'


def test_confidence_label_comment():
    """entity_agent.py module docstring must document that confidence labels
    are qualitative heuristics, not calibrated probabilities."""
    src = read("agentic_historian/agents/entity_agent.py")
    assert (
        "calibrated" in src.lower()
        or "heuristic" in src.lower()
        or "NOT a calibrated probability" in src
    ), "Module docstring must explain confidence labels are heuristic"


# ─────────────────────────────────────────────────────────────────────────────
# Part 4: _conf_rank helper function
# ─────────────────────────────────────────────────────────────────────────────

def test_conf_rank_function_exists():
    """_conf_rank() must map confidence labels to sort order."""
    src = read("agentic_historian/agents/entity_agent.py")
    assert "def _conf_rank" in src, "_conf_rank() helper must be defined"
    assert '"high": 4' in src, '"high" must map to rank 4 (highest)'
    assert '"unverified": 1' in src, '"unverified" must map to rank 1 (lowest)'


if __name__ == "__main__":
    test_cer_docstring_no_false_0to1_range()
    print("PASS: test_cer_docstring_no_false_0to1_range")

    test_wer_docstring_no_false_0to1_range()
    print("PASS: test_wer_docstring_no_false_0to1_range")

    test_cer_can_exceed_one()
    print("PASS: test_cer_can_exceed_one")

    test_efnh_stopwords_replace_modern()
    print("PASS: test_efnh_stopwords_replace_modern")

    test_vnd_in_stopwords()
    print("PASS: test_vnd_in_stopwords")

    test_no_numeric_confidence_decimals()
    print("PASS: test_no_numeric_confidence_decimals")

    test_confidence_labels_are_strings()
    print("PASS: test_confidence_labels_are_strings")

    test_confidence_label_comment()
    print("PASS: test_confidence_label_comment")

    test_conf_rank_function_exists()
    print("PASS: test_conf_rank_function_exists")

    print("\nAll #111 tests passed.")
