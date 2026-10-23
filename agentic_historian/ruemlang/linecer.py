"""linecer.py — CER per line, matched by position on the page, not by order.

The page CER in ``pipeline.py eval`` joins every line in the order each side
has: Transkribus' reading order for the ground truth, the kraken segmenter's
for the models. Where those orders differ (a marginal note, a line split in
two, a heading read last) a correct reading costs as much as a wrong one. On
Hertwig (22.10.) every reader landed at 0.37–0.60 page CER, and nothing said
how much of that was order.

Here each recognized line is assigned to the ground-truth line its box lies
on (vertical overlap with that line, any horizontal overlap), the pieces that
land on one GT line are joined left to right, and

    CER = (Σ edit(gt line, its pieces) + chars of lines that matched nothing)
          / Σ chars of the gt lines

A GT line nothing landed on counts its full length; a recognized line on no
GT line counts as inserted text. Order no longer matters; segmentation still
does, which is right — a line the segmenter missed is text the reader lost.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

Box = Sequence[float]

#: How much of the shorter of the two heights must overlap for a recognized
#: line to belong to a GT line. Half a line: neighbours in a tight hand touch,
#: they do not share half their height.
MIN_V_OVERLAP = 0.5


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _score(gt: Box, hyp: Box) -> float:
    """Vertical overlap ratio when the boxes also overlap horizontally, else 0."""
    if min(gt[2], hyp[2]) <= max(gt[0], hyp[0]):
        return 0.0
    ov = min(gt[3], hyp[3]) - max(gt[1], hyp[1])
    h = min(gt[3] - gt[1], hyp[3] - hyp[1])
    return ov / h if h > 0 and ov > 0 else 0.0


def assign(gt_boxes: list[Optional[Box]], hyp_boxes: list[Optional[Box]]) -> list[Optional[int]]:
    """For every recognized line, the index of its GT line, or None."""
    out: list[Optional[int]] = []
    for hb in hyp_boxes:
        best, best_s = None, MIN_V_OVERLAP
        if hb is not None:
            for i, gb in enumerate(gt_boxes):
                if gb is None:
                    continue
                s = _score(gb, hb)
                if s >= best_s:
                    best, best_s = i, s
        out.append(best)
    return out


def matched_cer(gt: list[tuple[Optional[Box], str]],
                hyp: list[tuple[Optional[Box], str]]) -> Optional[dict]:
    """``{"cer", "errors", "gt_chars", "unmatched_chars", "missed_lines"}``, or
    None when the ground truth has no text."""
    from rapidfuzz.distance import Levenshtein

    gt = [(b, _norm(t)) for b, t in gt if _norm(t)]
    total = sum(len(t) for _, t in gt)
    if not total:
        return None
    hyp = [(b, _norm(t)) for b, t in hyp if _norm(t)]
    owner = assign([b for b, _ in gt], [b for b, _ in hyp])
    pieces: dict[int, list[tuple[float, str]]] = {}
    unmatched = 0
    for (b, t), i in zip(hyp, owner):
        if i is None:
            unmatched += len(t)
        else:
            pieces.setdefault(i, []).append((b[0], t))
    errors = unmatched
    missed = 0
    for i, (_, t) in enumerate(gt):
        got = " ".join(t2 for _, t2 in sorted(pieces.get(i, [])))
        missed += not got
        errors += Levenshtein.distance(t, got)
    return {"cer": errors / total, "errors": errors, "gt_chars": total,
            "unmatched_chars": unmatched, "missed_lines": missed}
