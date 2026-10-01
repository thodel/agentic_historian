"""
page_quality.py — what kind of reading a page came back with (#483).

The runner's report says 9 % of pages came back empty, in prose, for a human. It
says nothing about the other way a reading fails: the model loses control and
pads the page. Measured on the Laßberg corpus, 2026-10-01:

    Briefe UB Freiburg__lassberg-letter-0083__002
      trocr-kurrent:  656 characters
      qwen3.5-4b:   8 824 characters, some 8 000 of them "000000000000…"

`truncated_by_model` was false — no token ceiling, no error, a 200 and two files
on disk. By every signal the runner has, that page was read. It is the same class
of failure as an empty page and it has no column.

This matters beyond the report. Those pages carry seven thousand characters of
padding each, so they move every corpus average that includes them. The reading
comparison put qwen3.5-4b at 1183 characters a page against trocr's 973 and that
gap, which reads as "the VLM reads more", is accounted for by about twenty-one
such pages out of 778.

**The verdict describes, it does not judge.** Whether an `empty` page is a blank
verso or a segmenter that found no lines is settled by looking at the image, not
by this module. The measures each verdict was derived from are kept beside it, so
changing a threshold later is arithmetic rather than a reason to read a corpus
again.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

#: Under this many characters a reading is `short`: too little to say anything
#: about, including whether the model lost control. A page of this corpus runs
#: ~1100 characters, an address panel a few hundred, a stray page number under
#: fifty.
SHORT_CHARS = 100

#: A run of one character this long is not German. The longest legitimate runs are
#: doubled letters and the zeros of a round number — "1000" is three. Measured
#: loops run into the thousands, so anything in between is caught with room to
#: spare on both sides.
MAX_CHAR_RUN = 20

#: Share of non-empty lines that merely repeat an earlier line, above which the
#: page is a repetition rather than a text. Checked only from `MIN_LINES_FOR_REPEAT`
#: lines up: three lines, two of them the same, is a short page, not a loop.
MAX_REPEAT_RATIO = 0.5
MIN_LINES_FOR_REPEAT = 6

#: The second loop, and the one #483 names in its own text:
#:
#:     die / der / 1869 / der / der / der / 1865 / de / 1850
#:
#: Not padding — filler, produced when the segmenter finds lines where there are
#: none and the model obliges. It reads as a *short* page under the thresholds
#: above, because a loop of three-character lines cannot reach `SHORT_CHARS`
#: until some thirty of them, and by then the run of one character is still 1
#: and the ratio is still under a half. So the whole failure mode fell through:
#: the module caught the page padded with 8 000 zeros and missed this one.
#:
#: What separates it from prose is not repetition alone — a word list repeats
#: nothing and is equally short — but repetition *in lines that are too short to
#: be lines of text*. Measured over the observed cases: loop pages run 3.0–3.2
#: characters a line against 57–58 for a read letter page, and repeat 0.44–0.85
#: of their lines against 0.0. Both margins are wide, and a letter page whose
#: segmenter duplicated two lines (ratio 0.11, mean 51) clears both.
#:
#: 0.3 is "two of six", "three of nine" — the smallest repetition that is still
#: a repetition at this line length, and it is what #483's own nine-line example
#: measures (0.33). `repeat_ratio` counts only second-and-later occurrences, so
#: a line appearing four times in nine contributes three, not four; the
#: threshold is set against that definition rather than against a tidier one,
#: because the ratio is already written into every page's JSON and redefining a
#: stored field silently reinterprets every reading taken so far.
REPEAT_RATIO_SHORT_LINES = 0.3
SHORT_LINE_CHARS = 12.0

_RUN_RE = re.compile(r"(.)\1*")


@dataclass
class PageQuality:
    """One reading's verdict, with everything it was derived from."""

    verdict: str                 # "ok" | "empty" | "short" | "repetitive"
    chars: int = 0
    lines: int = 0
    longest_char_run: int = 0
    repeat_ratio: float = 0.0
    #: Mean characters per non-empty line. On its own it says nothing — a word
    #: list is as short-lined as a loop — but with `repeat_ratio` it is what
    #: tells filler from a page somebody wrote.
    mean_line_chars: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


def mean_line_chars(lines: list[str]) -> float:
    """Mean length of the non-empty lines, 0.0 when there are none."""
    return (sum(len(ln) for ln in lines) / len(lines)) if lines else 0.0


def longest_char_run(text: str) -> int:
    """Length of the longest run of one repeated character.

    The cheapest signal there is for the failure that produced 8 000 zeros, and
    the one hardest to trip by accident: real prose does not repeat a character
    twenty times, and a page that does is not prose.
    """
    return max((len(m.group(0)) for m in _RUN_RE.finditer(text)), default=0)


def repeat_ratio(lines: list[str]) -> float:
    """Share of lines that merely repeat a line already seen.

    Counted over non-empty lines after stripping, because the engines disagree
    about trailing whitespace on every page and that is not a repetition.
    """
    if not lines:
        return 0.0
    seen: set[str] = set()
    repeats = 0
    for line in lines:
        if line in seen:
            repeats += 1
        else:
            seen.add(line)
    return repeats / len(lines)


def looks_repetitive(quality: "PageQuality", *,
                     max_char_run: int = MAX_CHAR_RUN,
                     max_repeat_ratio: float = MAX_REPEAT_RATIO,
                     repeat_ratio_short_lines: float = REPEAT_RATIO_SHORT_LINES,
                     short_line_chars: float = SHORT_LINE_CHARS) -> bool:
    """Whether these measures describe filler rather than a text.

    Its own function because #483 asks for the loop test to be one — separately
    testable, and calibrated against the observed cases rather than asserted.
    Three paths, because there are three observed shapes and no single threshold
    covers them:

    * **A run of one character.** The 8 000 zeros of 2026-10-01. Real prose does
      not repeat a character twenty times.
    * **Repeated short lines.** The `der / der / 1869 / der` filler, where the
      segmenter found lines that are not there. Repetition *and* lines too short
      to be lines of text: either alone has a legitimate counterpart — a word
      list is short-lined and repeats nothing, a refrain repeats and is not
      short.
    * **Repeated full lines.** A whole phrase restated down the page, which the
      ratio catches on its own without needing the lengths.

    Below `MIN_LINES_FOR_REPEAT` lines no ratio is evidence: a two-line address
    panel whose lines happen to match is a short page, not a model out of
    control.
    """
    if quality.longest_char_run >= max_char_run:
        return True
    if quality.lines < MIN_LINES_FOR_REPEAT:
        return False
    if quality.repeat_ratio >= max_repeat_ratio:
        return True
    return (quality.repeat_ratio >= repeat_ratio_short_lines
            and quality.mean_line_chars <= short_line_chars)


def classify(text: str, *, short_chars: int = SHORT_CHARS,
             max_char_run: int = MAX_CHAR_RUN,
             max_repeat_ratio: float = MAX_REPEAT_RATIO,
             repeat_ratio_short_lines: float = REPEAT_RATIO_SHORT_LINES,
             short_line_chars: float = SHORT_LINE_CHARS) -> PageQuality:
    """The verdict for one reading, and the measures behind it.

    Order: empty, then repetitive, then short. Shortness used to be checked
    first, with the argument that "`repetitive` is a claim about a model that
    ran out of control, and three words cannot support it". The argument holds
    for three words and is carried by `MIN_LINES_FOR_REPEAT` instead: under six
    lines nothing is called a loop. But applied by *character* count it also hid
    the whole short-line failure mode, because that filler is both repetitive
    and under `SHORT_CHARS` — #483's own example reads `short` under the old
    order, and so does a twenty-line page of it.

    So the line count decides whether a loop can be claimed at all, and once it
    can, the loop is named rather than the shortness: `short` says "too little to
    judge", which is wrong about a page whose mechanism we have just identified.
    The measures are kept either way, so nothing is lost to the choice.
    """
    stripped = text.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    quality = PageQuality(
        verdict="ok",
        chars=len(stripped),
        lines=len(lines),
        longest_char_run=longest_char_run(stripped),
        repeat_ratio=round(repeat_ratio(lines), 4),
        mean_line_chars=round(mean_line_chars(lines), 2),
    )
    if not stripped:
        quality.verdict = "empty"
    elif looks_repetitive(quality, max_char_run=max_char_run,
                          max_repeat_ratio=max_repeat_ratio,
                          repeat_ratio_short_lines=repeat_ratio_short_lines,
                          short_line_chars=short_line_chars):
        quality.verdict = "repetitive"
    elif quality.chars < short_chars:
        quality.verdict = "short"
    return quality
