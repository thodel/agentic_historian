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

_RUN_RE = re.compile(r"(.)\1*")


@dataclass
class PageQuality:
    """One reading's verdict, with everything it was derived from."""

    verdict: str                 # "ok" | "empty" | "short" | "repetitive"
    chars: int = 0
    lines: int = 0
    longest_char_run: int = 0
    repeat_ratio: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)


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


def classify(text: str, *, short_chars: int = SHORT_CHARS,
             max_char_run: int = MAX_CHAR_RUN,
             max_repeat_ratio: float = MAX_REPEAT_RATIO) -> PageQuality:
    """The verdict for one reading, and the measures behind it.

    Order: empty, then short, then repetitive. Shortness is checked **before**
    repetition on purpose — `repetitive` is a claim about a model that ran out of
    control on a page it was reading, and three words cannot support it. A
    fragment that also repeats is counted as the fragment it is, and the measures
    are kept either way, so nothing is lost by the choice.
    """
    stripped = text.strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    quality = PageQuality(
        verdict="ok",
        chars=len(stripped),
        lines=len(lines),
        longest_char_run=longest_char_run(stripped),
        repeat_ratio=round(repeat_ratio(lines), 4),
    )
    if not stripped:
        quality.verdict = "empty"
    elif quality.chars < short_chars:
        quality.verdict = "short"
    elif (quality.longest_char_run >= max_char_run
          or (quality.lines >= MIN_LINES_FOR_REPEAT
              and quality.repeat_ratio >= max_repeat_ratio)):
        quality.verdict = "repetitive"
    return quality
