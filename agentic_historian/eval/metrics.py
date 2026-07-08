"""
eval/metrics.py

CER (Character Error Rate) and WER (Word Error Rate) computation
for evaluating HTR transcription quality against ground truth.

Uses pure Python — no external dependencies.
"""

import re
import string
from typing import Sequence


def normalise(text: str) -> str:
    """
    Normalise historical German text for comparison.
    - Lowercase
    - Collapse whitespace
    - Strip leading/trailing punctuation from each token (so "Welt!!" == "Welt")
    """
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(tok.strip(string.punctuation) for tok in text.split())


def cer(
    reference: str,
    hypothesis: str,
    *,
    ignore_case: bool = True,
    ignore_whitespace: bool = True,
    ignore_punctuation: bool = True,
    abbrev_fold: bool = False,
) -> float:
    """
    Character Error Rate = Levenshtein edit distance / reference length.

    All normalisation behaviour is **explicit** via keyword arguments so
    callers can reason precisely about what is being measured.

    Parameters
    ----------
    reference, hypothesis : str
        The reference (ground-truth) text and hypothesis (engine output).
    ignore_case : bool  (default True)
        Case-insensitive comparison.
    ignore_whitespace : bool  (default True)
        Collapse runs of whitespace to single spaces; strip ends.
    ignore_punctuation : bool  (default True)
        Remove punctuation characters from both strings before comparing.
    abbrev_fold : bool  (default False)
        Fold common abbreviation markers in historical German:
        '̄' (macron) → '', 'ʒ' → 'z', 'ç' → 'c', 'ı' → 'i', 'ß' → 'ss'.
        Use when comparing early-modern print with modern editorial text.

    Returns a float that can exceed 1.0 when the hypothesis contains many
    insertions (e.g. hallucinated text) relative to the reference length.
    """
    def _normalise(text: str) -> str:
        if abbrev_fold:
            text = _fold_abbrevs(text)
        if ignore_case:
            text = text.lower()
        if ignore_whitespace:
            text = re.sub(r"\s+", " ", text).strip()
        if ignore_punctuation:
            text = text.translate(str.maketrans("", "", string.punctuation))
        return text

    ref = _normalise(reference)
    hyp = _normalise(hypothesis)

    if not ref:
        return 0.0 if not hyp else 1.0

    m, n = len(ref), len(hyp)
    # Space-optimised DP: two rows
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
    return prev[m] / m


# ─── Private helpers (not exported at package level) ─────────────────────────

_ABBREV_FOLD_MAP = {
    "ā": "a", "ē": "e", "ī": "i", "ō": "o", "ū": "u",  # Latin macron
    "Ā": "A", "Ē": "E", "Ī": "I", "Ō": "O", "Ū": "U",
    "ʒ": "z", "Ç": "C", "ç": "c", "ı": "i",             # historical variants
    "ß": "ss",                                          # long-s → ss
    "ſ": "s",                                           # long-s → s
}


def _fold_abbrevs(text: str) -> str:
    """Apply abbreviation folding for early-modern German comparison."""
    for old, new in _ABBREV_FOLD_MAP.items():
        text = text.replace(old, new)
    return text


def wer(
    reference: str,
    hypothesis: str,
    *,
    ignore_case: bool = True,
    ignore_whitespace: bool = True,
    ignore_punctuation: bool = True,
    abbrev_fold: bool = False,
) -> float:
    """
    Word Error Rate = Levenshtein word-level edit distance / word count.

    Shares all normalisation switches with cer(); see cer() docstring for
    parameter semantics.

    Returns a float that can exceed 1.0 when the hypothesis contains many
    word-level insertions (e.g. hallucinated text).
    """
    def _normalise_words(text: str) -> list[str]:
        if abbrev_fold:
            text = _fold_abbrevs(text)
        if ignore_case:
            text = text.lower()
        if ignore_whitespace:
            text = re.sub(r"\s+", " ", text).strip()
        if ignore_punctuation:
            text = text.translate(str.maketrans("", "", string.punctuation))
        return text.split()

    ref_words = _normalise_words(reference)
    hyp_words = _normalise_words(hypothesis)

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
    """Plain Levenshtein distance (used internally by cer/wer)."""
    # Use the default switch combo that cer() uses by default
    def _n(t):
        t = t.lower()
        t = re.sub(r"\s+", " ", t).strip()
        t = t.translate(str.maketrans("", "", string.punctuation))
        return t
    ref = _n(s1)
    hyp = _n(s2)
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


def normalise(text: str) -> str:
    """
    Backward-compatible single-string normalisation.
    Equivalent to cer(text, text, ignore_case=True, ignore_whitespace=True,
    ignore_punctuation=True, abbrev_fold=False) but only applies to the
    provided string (not pairwise). Used by format_report and external callers.
    """
    text = text.lower()
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(tok.strip(string.punctuation) for tok in text.split())


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