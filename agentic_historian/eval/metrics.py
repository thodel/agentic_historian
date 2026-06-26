"""
eval/metrics.py

CER (Character Error Rate) and WER (Word Error Rate) computation
for evaluating HTR transcription quality against ground truth.

Uses pure Python — no external dependencies.
"""

import re
from typing import Sequence


def normalise(text: str) -> str:
    """
    Normalise historical German text for comparison.
    - Lowercase
    - Collapse whitespace
    - Remove punctuation (optional)
    - Expand common medieval abbreviations (optional)
    """
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text


def cer(reference: str, hypothesis: str) -> float:
    """
    Character Error Rate = Levenshtein distance / reference length.
    Returns value between 0.0 (perfect) and 1.0 (completely wrong).
    """
    ref = normalise(reference)
    hyp = normalise(hypothesis)

    if not ref:
        return 0.0 if not hyp else 1.0

    # Levenshtein distance via dynamic programming
    m, n = len(ref), len(hyp)
    # Space-optimised: only keep two rows
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            if ref[j - 1] == hyp[i - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, prev

    distance = prev[m]
    return distance / m


def wer(reference: str, hypothesis: str) -> float:
    """
    Word Error Rate = Levenshtein distance on words / reference word count.
    Returns value between 0.0 (perfect) and 1.0 (completely wrong).
    """
    ref_words = normalise(reference).split()
    hyp_words = normalise(hypothesis).split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    m, n = len(ref_words), len(hyp_words)
    prev = list(range(m + 1))
    curr = [0] * (m + 1)

    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            if ref_words[j - 1] == hyp_words[i - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, prev

    distance = prev[m]
    return distance / m


def levenshtein(s1: str, s2: str) -> int:
    """Plain Levenshtein distance (used by cer/wer)."""
    ref = normalise(s1)
    hyp = normalise(s2)
    m, n = len(ref), len(hyp)
    if m == 0:
        return n
    if n == 0:
        return m
    prev = list(range(m + 1))
    curr = [0] * (m + 1)
    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            if ref[j - 1] == hyp[i - 1]:
                curr[j] = prev[j - 1]
            else:
                curr[j] = 1 + min(prev[j], curr[j - 1], prev[j - 1])
        prev, curr = curr, prev
    return prev[m]


def format_report(results: list[dict]) -> str:
    """
    Build a markdown table from evaluation results.
    results: list of dicts with keys: doc_id, cer, wer, gt_len, hyp_len, errors
    """
    if not results:
        return "No results to report."

    lines = [
        "| Dok-ID | CER | WER | GT-Len | Hyp-Len | Fehler |",
        "|--------|-----|-----|--------|---------|--------|",
    ]
    for r in results:
        lines.append(
            f"| {r.get('doc_id','?')} "
            f"| {r.get('cer',0):.3f} "
            f"| {r.get('wer',0):.3f} "
            f"| {r.get('gt_len',0)} "
            f"| {r.get('hyp_len',0)} "
            f"| {r.get('errors',0)} |"
        )

    # Summary row
    avg_cer = sum(r.get("cer", 0) for r in results) / len(results)
    avg_wer = sum(r.get("wer", 0) for r in results) / len(results)
    lines.append(f"\n**Average** | **{avg_cer:.3f}** | **{avg_wer:.3f}** | — | — | — |")
    return "\n".join(lines)