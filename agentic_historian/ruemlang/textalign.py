"""textalign.py — where a manuscript page sits in the print, and which line is
which across readings.

Two alignments, both over a **normalised** form of the text that a 1528 scribe
and a 1528 typesetter would agree on: lowercase, no diacritics, u/v and i/j/y
folded, ß → ss, the ů/uo/ue digraphs reduced to the vowel, punctuation gone.
The normalisation is only ever used to *find* text; what is kept and shown is
always the original string, mapped back through a character index.

* :class:`Reference` holds the print (any transcription or OCR of it) and
  answers ``locate(page_text)`` — the passage the page corresponds to — by
  voting with word trigrams and refining with a fuzzy partial alignment, then
  ``snippet(line_text, passage)`` for one line inside that passage.
* :func:`align_line_lists` pairs the lines of two readings of the same page
  (the page model's text split at newlines against the line model's lines) by
  dynamic programming on line similarity, so a dropped or merged line shifts
  nothing below it.

A score is a similarity in [0, 1] from rapidfuzz; below ``MIN_PASSAGE_SCORE`` a
passage is reported as not found rather than as the best bad guess, because a
wrong print passage handed to the arbiter is worse than none.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

MIN_PASSAGE_SCORE = 0.55
MIN_SNIPPET_SCORE = 0.60
#: Inside a *weak* passage (the anchors' window, not a located one) a line must
#: match this well before the print is shown for it: short lines match somewhere
#: in any ten pages of text, and a wrong P is worse than none.
MIN_WEAK_SNIPPET_SCORE = 0.72
MIN_WEAK_SNIPPET_CHARS = 15

#: Page markers the reference files carry: ``[Druck 1528 S. 20]``.
PAGE_MARK = re.compile(r"\[Druck 1528 S\. (\d+)\]")

_FOLD = {"ß": "ss", "v": "u", "w": "w", "j": "i", "y": "i", "æ": "a", "œ": "o"}


def normalize_with_map(s: str) -> tuple[str, list[int]]:
    """``(normalised, index)`` where ``index[k]`` is the offset in ``s`` of the
    character ``normalised[k]`` came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_vowel_u = False
    for i, ch in enumerate(s):
        base = unicodedata.normalize("NFKD", ch)
        base = "".join(c for c in base if not unicodedata.combining(c)).lower()
        for c in base:
            c = _FOLD.get(c, c)
            if not c.isalnum():
                prev_vowel_u = False
                continue
            if c in "eo" and prev_vowel_u:
                # ů written as uo/ue (zuo, guot, uebel): collapse the digraph
                prev_vowel_u = False
                continue
            for cc in c:
                out.append(cc)
                idx.append(i)
            prev_vowel_u = c == "u"
    return "".join(out), idx


def normalize(s: str) -> str:
    return normalize_with_map(s)[0]


def similarity(a: str, b: str) -> float:
    """Normalised similarity in [0, 1]; 1 for two empty strings."""
    na, nb = normalize(a), normalize(b)
    if not na and not nb:
        return 1.0
    return Levenshtein.normalized_similarity(na, nb)


_WORD = re.compile(r"\S+")


@dataclass
class Passage:
    start: int          # char offsets into the reference's original text
    end: int
    text: str
    score: float
    #: True when this is only the anchors' window, not a passage the page's own
    #: text located: the print there may say something else entirely.
    weak: bool = False
    pages: Optional[tuple[int, int]] = None


class Reference:
    """A text of the print, searchable by content."""

    def __init__(self, text: str, *, ngram: int = 3):
        self.text = text
        self.ngram = ngram
        self.words: list[tuple[int, int, str]] = []   # (start, end, normalised)
        for m in _WORD.finditer(text):
            n = normalize(m.group())
            if n:
                self.words.append((m.start(), m.end(), n))
        self.index: dict[tuple[str, ...], list[int]] = defaultdict(list)
        keys = [w[2] for w in self.words]
        for i in range(len(keys) - ngram + 1):
            self.index[tuple(keys[i:i + ngram])].append(i)
        #: print page -> (start, end) char span, from the page markers
        self.page_spans: dict[int, tuple[int, int]] = {}
        marks = [(m.start(), int(m.group(1))) for m in PAGE_MARK.finditer(text)]
        for k, (off, n) in enumerate(marks):
            end = marks[k + 1][0] if k + 1 < len(marks) else len(text)
            self.page_spans[n] = (off, end)

    def page_of(self, offset: int) -> Optional[int]:
        for n, (a, b) in self.page_spans.items():
            if a <= offset < b:
                return n
        return None

    def span_of_pages(self, first: int, last: int) -> Optional[tuple[int, int]]:
        spans = [self.page_spans[n] for n in range(first, last + 1) if n in self.page_spans]
        if not spans:
            return None
        return min(a for a, _ in spans), max(b for _, b in spans)

    # ── the passage a page corresponds to ────────────────────────────────────
    def locate(self, page_text: str, *, slack: float = 0.35,
               min_score: float = MIN_PASSAGE_SCORE,
               pages: Optional[tuple[int, int]] = None,
               margin: int = 2) -> Optional[Passage]:
        """The passage ``page_text`` corresponds to.

        ``pages`` is a prior — the print pages the project's anchors predict for
        this manuscript page. Trigram votes are then counted only inside that
        window plus ``margin`` pages each side, and when the page still cannot
        be located, the window itself comes back as a **weak** passage, so lines
        that do match the print there can still see it.
        """
        q = [normalize(w) for w in _WORD.findall(page_text)]
        q = [w for w in q if w]
        if len(q) < self.ngram or not self.words:
            return None
        lo_w, hi_w = 0, len(self.words)
        hint = None
        if pages:
            hint = self.span_of_pages(pages[0] - margin, pages[1] + margin)
            if hint:
                lo_w = next((i for i, w in enumerate(self.words) if w[0] >= hint[0]), 0)
                hi_w = next((i for i, w in enumerate(self.words) if w[0] >= hint[1]), len(self.words))
        votes: Counter = Counter()
        for i in range(len(q) - self.ngram + 1):
            for pos in self.index.get(tuple(q[i:i + self.ngram]), ()):
                if lo_w <= pos < hi_w:
                    votes[pos - i] += 1
        if not votes:
            return self._weak(hint, pages)
        # Bucket anchors: a page spans many words, so starts within half a page
        # of each other are the same hypothesis.
        bucket = max(5, len(q) // 2)
        buckets: Counter = Counter()
        for anchor, n in votes.items():
            buckets[anchor // bucket] += n
        best_bucket, _ = buckets.most_common(1)[0]
        anchors = [a for a in votes if a // bucket == best_bucket]
        start_w = max(0, min(anchors))
        end_w = min(len(self.words), max(anchors) + int(len(q) * (1 + slack)) + self.ngram)
        if end_w - start_w < len(q) // 2:
            end_w = min(len(self.words), start_w + int(len(q) * (1 + slack)))
        window = self.words[start_w:end_w]
        w_start, w_end = window[0][0], window[-1][1]
        wtext = self.text[w_start:w_end]
        wnorm, wmap = normalize_with_map(wtext)
        qnorm = normalize(page_text)
        aln = fuzz.partial_ratio_alignment(qnorm, wnorm)
        if aln is None or aln.score / 100.0 < min_score:
            weak = self._weak(hint, pages)
            if weak is not None and aln is not None:
                weak.score = aln.score / 100.0
            return weak
        s = w_start + wmap[min(aln.dest_start, len(wmap) - 1)]
        e = w_start + wmap[max(aln.dest_start, min(aln.dest_end - 1, len(wmap) - 1))] + 1
        return Passage(start=s, end=e, text=self.text[s:e], score=aln.score / 100.0,
                       pages=(self.page_of(s) or 0, self.page_of(max(s, e - 1)) or 0)
                       if self.page_spans else None)

    def _weak(self, hint: Optional[tuple[int, int]],
              pages: Optional[tuple[int, int]]) -> Optional[Passage]:
        if not hint:
            return None
        return Passage(start=hint[0], end=hint[1], text=self.text[hint[0]:hint[1]],
                       score=0.0, weak=True, pages=pages)

    # ── one line inside a passage ────────────────────────────────────────────
    @staticmethod
    def snippet(line_text: str, passage: Passage,
                min_score: Optional[float] = None) -> tuple[Optional[str], float]:
        qnorm = normalize(line_text)
        if min_score is None:
            min_score = MIN_WEAK_SNIPPET_SCORE if passage.weak else MIN_SNIPPET_SCORE
        if len(qnorm) < (MIN_WEAK_SNIPPET_CHARS if passage.weak else 4):
            return None, 0.0
        pnorm, pmap = normalize_with_map(passage.text)
        if not pnorm:
            return None, 0.0
        aln = fuzz.partial_ratio_alignment(qnorm, pnorm)
        if aln is None or aln.score / 100.0 < min_score:
            return None, (aln.score / 100.0 if aln else 0.0)
        s = pmap[min(aln.dest_start, len(pmap) - 1)]
        e = pmap[max(aln.dest_start, min(aln.dest_end - 1, len(pmap) - 1))] + 1
        # widen to word boundaries in the original
        while s > 0 and not passage.text[s - 1].isspace():
            s -= 1
        while e < len(passage.text) and not passage.text[e].isspace():
            e += 1
        return passage.text[s:e].strip(), aln.score / 100.0


# ── line-to-line alignment of two readings ──────────────────────────────────

def align_line_lists(ref_lines: list[str], hyp_lines: list[str], *,
                     gap: float = 0.25, min_sim: float = 0.3) -> list[Optional[int]]:
    """For each reference line the index of its hypothesis line, or None.

    Needleman–Wunsch over lines with similarity as the match score and ``gap``
    as the cost of skipping a line on either side. Pairs below ``min_sim`` are
    not reported as matches even when the path goes through them.
    """
    n, m = len(ref_lines), len(hyp_lines)
    if n == 0 or m == 0:
        return [None] * n
    sim = [[similarity(r, h) for h in hyp_lines] for r in ref_lines]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = -gap * i
    for j in range(1, m + 1):
        score[0][j] = -gap * j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            score[i][j] = max(score[i - 1][j - 1] + sim[i - 1][j - 1],
                              score[i - 1][j] - gap, score[i][j - 1] - gap)
    out: list[Optional[int]] = [None] * n
    i, j = n, m
    while i > 0 and j > 0:
        if score[i][j] == score[i - 1][j - 1] + sim[i - 1][j - 1]:
            if sim[i - 1][j - 1] >= min_sim:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif score[i][j] == score[i - 1][j] - gap:
            i -= 1
        else:
            j -= 1
    return out


def split_page_text(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
