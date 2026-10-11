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

#: A reading that argues with itself instead of reproducing the page (#619).
#: Measured 2026-10-11 on `gemini-300-strict`, where **three of the four pages
#: the report called `cut off`** carried no transcription at all:
#:
#:     - e - n - k - t !
#:     LOOK AT WORD 3:
#:     g - e - s - c - h - e - n - k - t !
#:     Wait, does it have 'sch'?
#:     ...
#:     Entry stroke, downstroke, loop up to the right... THAT'S A 'V'!
#:
#: This is the model's deliberation in the content channel. It is not `empty`,
#: not `repetitive` (no character runs, every line different) and not `short`
#: (816–928 characters), so no column moved — and the pages were only noticed at
#: all because the arguing ran into the token ceiling. A page that deliberates
#: for four hundred characters and then stops is invisible.
#:
#: The cues are English, second-person and *about* the page: a transcription of
#: this corpus reproduces German and Latin and never addresses itself. Measured
#: share of cue lines: **0.38, 0.55, 0.70** on the three leaked pages against
#: **0.00** on every clean reading of the same run. 0.2 sits far from both.
DELIBERATION_CUES = re.compile(
    r"^\s*(?:wait|hold on|hmm+|let'?s|let me|look at|actually|so,|okay|ok,)\b"
    r"|^\s*(?:line|word)\s+\d+\b"
    r"|^\s*(?:yes|no)[!.]\s*$"
    r"|\b(?:is it|could it be|what if|does it have|what about)\b",
    re.I)

#: Share of lines carrying a cue, above which the reading is deliberation rather
#: than a text, plus the two floors that keep a one-off from deciding it: a page
#: needs lines to have a share at all, and one cue line is not an argument.
DELIBERATION_SHARE = 0.2
MIN_CUE_LINES = 2
MIN_LINES_FOR_DELIBERATION = 4

#: A run of the same **non-whitespace** character. Whitespace is excluded, and
#: that exclusion has a measured cause: on 2026-10-10 a VLM reading of
#: `Basel__lassberg-letter-1743__PA 82a B 9_Seite_030` was marked `repetitive`
#: on a `longest_char_run` of 34, and the 34 were the spaces indenting a
#: right-aligned signature —
#:
#:                                       JvLaßberg.
#:
#: The engines behind the gateway emit one line per text line and no layout, so
#: this could not happen before; a model that reproduces the page's layout trips
#: it on every indented signature, address and dateline. And the verdict is not
#: cosmetic: the batch report says of a flagged page that "every average over
#: them is wrong", which about a clean page is a false accusation that outlives
#: the run.
#:
#: A run of 34 letters is filler. A run of 34 spaces is a layout.
_RUN_RE = re.compile(r"([^\s])\1*")

#: ...and a run of 80 underscores is a rule (#623). The whitespace exclusion
#: above fixed one false positive and left its sibling: on 2026-10-11 a DLA
#: Marbach insert slip, correctly transcribed and marked
#: `[fremde hand: …]`, was called `repetitive` because the slip's printed
#: separator line came out as eighty underscores.
#:
#: Excluding `_` by name would be the third patch of the same shape, and the
#: next form will use dots or dashes. What actually tells a rule from filler is
#: not the character but how much of the page it is:
#:
#:     8 000 zeros in 8 824 characters   share 0.91   filler
#:        80 underscores in 822          share 0.10   a line in a form
#:
#: But share alone would let a short burst through: twenty zeros glued to the
#: end of a sentence are 14 % of a 142-character page and are plainly a glitch.
#: What separates that from the Marbach rule is **where** the run sits. A
#: printed rule is a line of its own; filler is extruded into the text.
#:
#:     ...umzusehen.00000000000000000000      share 0.14   inside a line → filler
#:     ____________________________________   share 0.10   its own line  → a rule
#:     ...10 bis 12 + 3 000 zeros             share 0.97   inside a line → filler
#:     a line of 8 000 zeros                  share 0.99   own line, but 99 % → filler
#:
#: So a long run is a rule only when it is a line by itself *and* a small part
#: of the page; anything else is filler. Both measures are stored with every
#: reading, so moving either threshold stays arithmetic. One measured case per
#: corner, which is thin — but each corner is a real page of this corpus.
MAX_RUN_SHARE = 0.25


#: Every verdict :func:`classify` can return, named once (#620).
#:
#: It was a comment on the field below, and a comment is not something another
#: module can check itself against: #619 added `deliberation` to the classifier
#: and the MCP reply went on listing the three counts it was born with, so a
#: caller asking how many pages argue with themselves got no answer and no sign
#: that none was given. Anything that enumerates verdicts reads this.
#:
#: `ok` and `short` are not defects: nothing to re-read, nothing to flag.
VERDICTS = ("ok", "empty", "short", "repetitive", "deliberation")

#: The verdicts that mean a page's reading is not worth keeping — what
#: `BatchReport.flagged_keys` collects and what a re-read is for.
DEFECTS = ("empty", "repetitive", "deliberation")


@dataclass
class PageQuality:
    """One reading's verdict, with everything it was derived from."""

    verdict: str                 #: one of :data:`VERDICTS`
    chars: int = 0
    lines: int = 0
    longest_char_run: int = 0
    repeat_ratio: float = 0.0
    #: Mean characters per non-empty line. On its own it says nothing — a word
    #: list is as short-lined as a loop — but with `repeat_ratio` it is what
    #: tells filler from a page somebody wrote.
    mean_line_chars: float = 0.0
    #: Share of lines that talk about the page instead of reproducing it (#619).
    deliberation_ratio: float = 0.0
    #: `longest_char_run` as a share of the page: what tells a printed rule from
    #: a model that lost control (#623).
    run_share: float = 0.0
    #: Whether the longest run is a line of its own. A rule is; filler extruded
    #: into a line of prose is not.
    run_on_own_line: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def mean_line_chars(lines: list[str]) -> float:
    """Mean length of the non-empty lines, 0.0 when there are none."""
    return (sum(len(ln) for ln in lines) / len(lines)) if lines else 0.0


def run_on_own_line(lines: list[str], run: int) -> bool:
    """Whether some line is nothing but the longest run, repeated.

    The structural half of the rule test (#623): a printed separator occupies a
    line; a model's filler is extruded into one.
    """
    for line in lines:
        if len(line) == run and line and line == line[0] * run:
            return True
    return False


def longest_char_run(text: str) -> int:
    """Length of the longest run of one repeated character.

    The cheapest signal there is for the failure that produced 8 000 zeros, and
    the one hardest to trip by accident: real prose does not repeat a character
    twenty times, and a page that does is not prose.

    **Whitespace does not count** — see :data:`_RUN_RE` for the page that made
    that necessary. Prose does not repeat a letter twenty times; it indents a
    signature by thirty-four spaces all the time.
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


def deliberation_ratio(lines: list[str]) -> float:
    """Share of non-empty lines that talk about the page rather than give it.

    Counted per line rather than per occurrence: a line with four "Wait"s is one
    line of deliberation, and weighting it four times would let a single
    excitable line decide a page.
    """
    if not lines:
        return 0.0
    return sum(1 for ln in lines if DELIBERATION_CUES.search(ln)) / len(lines)


def looks_deliberated(quality: "PageQuality", *,
                      deliberation_share: float = DELIBERATION_SHARE,
                      min_cue_lines: int = MIN_CUE_LINES,
                      min_lines: int = MIN_LINES_FOR_DELIBERATION) -> bool:
    """Whether these measures describe a model arguing with itself (#619).

    Three floors rather than one threshold, for the same reason the loop test
    has three paths: a share needs lines to be a share, one cue line is not an
    argument, and the share itself is what separates a reading that quotes a
    stray English phrase from one that never transcribed anything.

    Measured on the three leaked pages of `gemini-300-strict`: 0.38, 0.55, 0.70
    against 0.00 on the clean readings of the same pages under another prompt.
    """
    if quality.lines < min_lines:
        return False
    if quality.deliberation_ratio * quality.lines < min_cue_lines:
        return False
    return quality.deliberation_ratio >= deliberation_share


def looks_repetitive(quality: "PageQuality", *,
                     max_char_run: int = MAX_CHAR_RUN,
                     max_run_share: float = MAX_RUN_SHARE,
                     max_repeat_ratio: float = MAX_REPEAT_RATIO,
                     repeat_ratio_short_lines: float = REPEAT_RATIO_SHORT_LINES,
                     short_line_chars: float = SHORT_LINE_CHARS) -> bool:
    """Whether these measures describe filler rather than a text.

    Its own function because #483 asks for the loop test to be one — separately
    testable, and calibrated against the observed cases rather than asserted.
    Three paths, because there are three observed shapes and no single threshold
    covers them:

    * **A run of one character that is not a printed rule.** The 8 000 zeros of
      2026-10-01. Real prose does not repeat a character twenty times — but a
      form rules a line with eighty underscores, so a run is spared only when it
      is a line of its own *and* under a quarter of the page (#623).
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
        rule = quality.run_on_own_line and quality.run_share < max_run_share
        if not rule:
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
             short_line_chars: float = SHORT_LINE_CHARS,
             deliberation_share: float = DELIBERATION_SHARE,
             max_run_share: float = MAX_RUN_SHARE) -> PageQuality:
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
        deliberation_ratio=round(deliberation_ratio(lines), 4),
    )
    quality.run_share = (round(quality.longest_char_run / quality.chars, 4)
                         if quality.chars else 0.0)
    quality.run_on_own_line = run_on_own_line(lines, quality.longest_char_run)
    if not stripped:
        quality.verdict = "empty"
    elif looks_deliberated(quality, deliberation_share=deliberation_share):
        # Before the loop test and before shortness, for the reason this module
        # already gives for naming a loop over a short page: `repetitive` and
        # `short` describe the shape of the text, and this names what produced
        # it. A page of "Wait, is it...?" is not a page the model padded.
        quality.verdict = "deliberation"
    elif looks_repetitive(quality, max_char_run=max_char_run,
                          max_run_share=max_run_share,
                          max_repeat_ratio=max_repeat_ratio,
                          repeat_ratio_short_lines=repeat_ratio_short_lines,
                          short_line_chars=short_line_chars):
        quality.verdict = "repetitive"
    elif quality.chars < short_chars:
        quality.verdict = "short"
    return quality
