"""
eval/metrics.py

CER (Character Error Rate) and WER (Word Error Rate) computation
for evaluating HTR transcription quality against ground truth.

Uses pure Python — no external dependencies.
"""

import re
from typing import Sequence


def normalise(text: str) -> str:
    """Lowercase + collapse whitespace. Punctuation and diacritics preserved."""
    return re.sub(r"\s+", " ", text.lower().strip())


def levenshtein(s1: str, s2: str) -> int:
    """Space-optimised Levenshtein distance (O(min(m,n)) memory)."""
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


def cer(reference: str, hypothesis: str) -> float:
    """Character Error Rate = Levenshtein distance / reference length. 0.0=perfect, 1.0=useless."""
    ref = normalise(reference)
    if not ref:
        return 0.0 if not normalise(hypothesis) else 1.0
    return levenshtein(ref, normalise(hypothesis)) / len(ref)


def wer(reference: str, hypothesis: str) -> float:
    """Word Error Rate = word-level Levenshtein / reference word count."""
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
    return prev[m] / m


def format_report(results: list[dict]) -> str:
    """
    Markdown table from eval results.
    Each dict needs: doc_id, cer, wer, gt_len, hyp_len, errors.
    """
    if not results:
        return "No results."
    header = (
        "| Dok-ID | CER   | WER   | GT-Len | Hyp-Len | Fehler |\n"
        "|--------|-------|-------|--------|---------|--------|\n"
    )
    rows = []
    for r in results:
        rows.append(
            f"| {r.get('doc_id','?')} "
            f"| {r.get('cer',0):.3f} "
            f"| {r.get('wer',0):.3f} "
            f"| {r.get('gt_len',0)} "
            f"| {r.get('hyp_len',0)} "
            f"| {r.get('errors',0)} |"
        )
    avg_cer = sum(r.get("cer", 0) for r in results) / len(results)
    avg_wer = sum(r.get("wer", 0) for r in results) / len(results)
    summary = f"\n**Average** | **{avg_cer:.3f}** | **{avg_wer:.3f}** | — | — | — |"
    return header + "\n".join(rows) + summary