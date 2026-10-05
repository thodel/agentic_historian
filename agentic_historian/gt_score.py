"""
gt_score.py — score a run's readings against real ground truth.

Everything this project has measured about the Laßberg corpus so far has been
*relative*: pairwise disagreement between readings, which says who differs and
never who is right (#326). `eval/linebench.py` added absolute numbers but against
the Swiss federal minutes — the right century and script, the wrong corpus, and
its own docstring says so.

A page of this corpus, corrected by hand in Transkribus to `status="FINAL"`, is a
different thing entirely. With one of those, the question #416 could not answer —
is one of these readings stronger, or are they a field of equals? — stops being a
judgement about length and becomes arithmetic.

**The direction of the metric matters here and it is the opposite of
`compare_runs`.** There, neither side is truth, so the measure has to be symmetric
or the table depends on column order. Here one side *is* truth, so CER divides by
it: the ground truth is the reference, the reading is the hypothesis, and an
insertion costs what an insertion costs.

**Matching.** Transkribus ground truth carries Transkribus identities — a docId, a
pageId, an `imageFilename` of `61849396.tif` — and shares no identifier with our
page keys. So the page is found by content: the reading closest to the ground
truth. That is sound exactly as long as it is checked, which is why every match
reports its runner-up. A best match at 20 % against a second-best at 80 % is
certain; two at 70 % mean the page is not in this run and scoring it would invent
a number.
"""

from __future__ import annotations

import xml.etree.ElementTree as et
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

_PAGE_NS = {"p": "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"}

#: Transkribus statuses that mean a human has been over the page, and the single
#: definition of it for this project — `transkribus.GT_STATUSES` is this tuple.
#:
#: **DONE counts.** Transkribus' ladder runs NEW → IN_PROGRESS → DONE → FINAL → GT,
#: and this project's collection never used the GT tag at all: the work was marked
#: DONE or FINAL and that is where it stopped. Requiring FINAL would have thrown
#: away the larger half of the available ground truth and told the operator, in a
#: warning, that their own corrected pages were "not ground truth yet".
#:
#: Anything below DONE does not count. NEW and IN_PROGRESS are a model's output,
#: and scoring our readings against those would be scoring one model by another
#: (#326, one layer down) — a number that looks like accuracy and measures
#: agreement between two machines.
CORRECTED_STATUSES = ("DONE", "FINAL", "GT")


class GroundTruthError(RuntimeError):
    """A ground-truth file that cannot be scored, in the words a CLI should print."""


@dataclass
class PageGT:
    """One hand-corrected page, with where it came from."""

    text: str
    source: Path
    doc_id: str = ""
    page_id: str = ""
    status: str = ""
    image: str = ""
    lines: int = 0


@dataclass
class Match:
    """Which page of a reading a ground-truth page was taken to be."""

    reading: str
    key: str = ""
    cer: float = 0.0
    runner_up_key: str = ""
    runner_up_cer: float = 0.0

    @property
    def confident(self) -> bool:
        """True when the second-best match is clearly worse.

        Not a threshold anybody tuned: it is the difference between "this is the
        page" and "this is the least bad of 6719". Half again as good is a wide
        margin for a measure that runs 0 to 1.
        """
        return bool(self.key) and (
            not self.runner_up_key or self.cer * 1.5 < self.runner_up_cer)


@dataclass
class Unusable:
    """A ground-truth file that could not be scored, and why.

    Collected rather than raised. One empty file used to end the whole scoring
    run — 15 pages measured and thrown away because the 16th had no transcribed
    text (2026-10-02, doc14662279_page3). That is the same shape as the share walk
    a single unreadable folder used to kill: the cost of one bad input must be that
    input, not the job.
    """

    source: Path
    reason: str


@dataclass
class Scored:
    gt: PageGT
    matches: list[Match] = field(default_factory=list)

    @property
    def agreed_key(self) -> str:
        """The key every reading matched, or "" when they disagree.

        Readings are matched independently on purpose. If two of them place the
        same ground-truth page on different pages of the corpus, the match is not
        to be trusted — and that is worth knowing rather than averaging over.
        """
        keys = {m.key for m in self.matches if m.key}
        return keys.pop() if len(keys) == 1 else ""

    @property
    def located(self) -> str:
        """The key, but only when we actually know which page this is.

        `agreed_key` answers a different question: whether the readings *agree*.
        With one reading they agree trivially, and with two bad ones they can
        agree on the wrong page — on 2026-10-02 a single reading agreed with
        itself at 106.9 % against a runner-up at 107.7 %, which is not a located
        page, it is a coin landing on its edge.

        So agreement **and** at least one confident match. One is enough: if any
        reading places the page clearly, the page is that one, and another
        reading being doubtful at the same key says something about the reading
        rather than about the identification.

        This is the property `score-gt --keys-out` writes from, because a wrong
        key there does not produce a bad number — it sends eight models to read a
        different page and then scores their readings of it against this page's
        ground truth.
        """
        agreed = self.agreed_key
        if not agreed:
            return ""
        return agreed if any(m.confident for m in self.matches) else ""


def read_pagexml(path: Path) -> PageGT:
    """The page's text from a PAGE XML file, with its Transkribus identity.

    The region-level ``TextEquiv`` already holds the whole page, newline-joined,
    which is the form our readings are in. Where a file has none, the line-level
    ones are joined in reading order instead — that is the same text, assembled
    rather than stored.
    """
    path = Path(path)
    try:
        root = et.parse(path).getroot()
    except et.ParseError as exc:
        raise GroundTruthError(f"{path.name}: not well-formed XML: {exc}") from exc

    meta = root.find(".//p:TranskribusMetadata", _PAGE_NS)
    page = root.find(".//p:Page", _PAGE_NS)
    lines = root.findall(".//p:TextLine", _PAGE_NS)

    chunks: list[str] = []
    for region in root.findall(".//p:TextRegion", _PAGE_NS):
        # The region's own TextEquiv is the last child; a line's is nested deeper.
        direct = [te for te in region.findall("p:TextEquiv/p:Unicode", _PAGE_NS)]
        if direct and (direct[0].text or "").strip():
            chunks.append(direct[0].text)
            continue
        for te in region.findall("p:TextLine/p:TextEquiv/p:Unicode", _PAGE_NS):
            if (te.text or "").strip():
                chunks.append(te.text)

    text = "\n".join(c.strip("\r\n") for c in chunks if c).strip()
    if not text:
        raise GroundTruthError(f"{path.name}: no transcribed text in this file")

    return PageGT(
        text=text,
        source=path,
        doc_id=(meta.get("docId") if meta is not None else "") or "",
        page_id=(meta.get("pageId") if meta is not None else "") or "",
        status=(meta.get("status") if meta is not None else "") or "",
        image=(page.get("imageFilename") if page is not None else "") or "",
        lines=len(lines),
    )


def match_in_reading(gt: PageGT, label: str, pages: dict[str, str]) -> Match:
    """Find the page of one reading that this ground truth describes.

    Scored with the same CER the result is reported in, so the page chosen is the
    page that scores best rather than one picked by a cheaper proxy and then
    scored by something else.
    """
    from eval.metrics import cer

    if not pages:
        return Match(reading=label)
    scores = sorted((cer(gt.text, text), key) for key, text in pages.items())
    best_cer, best_key = scores[0]
    match = Match(reading=label, key=best_key, cer=best_cer)
    if len(scores) > 1:
        match.runner_up_cer, match.runner_up_key = scores[1]
    return match


def score(gt_files: Iterable[Path], readings: dict[str, dict[str, str]],
          unusable: Optional[list] = None) -> list[Scored]:
    """Every usable ground-truth page against every reading.

    A file that cannot be read is appended to ``unusable`` and the rest are scored.
    Raising instead cost fifteen measured pages to one empty file.
    """
    if not readings:
        raise GroundTruthError("no readings to score — give at least one run dir")
    out: list[Scored] = []
    for path in gt_files:
        try:
            gt = read_pagexml(Path(path))
        except GroundTruthError as exc:
            logger.warning(f"[gt] skipped: {exc}")
            if unusable is not None:
                unusable.append(Unusable(source=Path(path), reason=str(exc)))
            continue
        if gt.status and gt.status.upper() not in CORRECTED_STATUSES:
            logger.warning(
                f"[gt] {gt.source.name}: status is {gt.status!r}, below DONE — "
                f"this is a model's output, not ground truth, and scoring against "
                f"it measures agreement between two machines")
            if unusable is not None:
                unusable.append(Unusable(
                    source=Path(path),
                    reason=f"status {gt.status!r} is below DONE"))
            continue
        out.append(Scored(gt=gt, matches=[
            match_in_reading(gt, label, pages) for label, pages in readings.items()
        ]))
    return out


@dataclass
class ReadingScore:
    """One reading's error rate over the pages where the page is known."""

    reading: str
    pages: int
    median: float
    p90: float
    best: float
    worst: float

    @property
    def row(self) -> str:
        return (f"| `{self.reading}` | {self.pages} | {self.median:.1%} | "
                f"{self.p90:.1%} | {self.best:.1%} | {self.worst:.1%} |")


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of a sorted copy. Empty gives 0.0."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def score_by_reading(scored: list[Scored]) -> list[ReadingScore]:
    """Each reading's error rate across the corpus, best median first.

    **The aggregate the per-page sections cannot give you.** Eight engines over
    241 pages is 1928 numbers, and the question #416 asks — is one of these
    readings stronger, or are they a field of equals? — is answered by their
    distributions and not by any single page. Reading it off by hand from 241
    sections is the kind of arithmetic nobody should do twice.

    **Only pages this reading *located* are counted** (`Scored.located`: the
    readings agree and at least one of them is confident). A page whose identity
    is unsettled has a CER that describes nothing — it is the distance to
    whichever page happened to be least unlike it — and averaging those in would
    move every engine's number by an unknown amount in an unknown direction.
    `pages` therefore says how many each figure rests on, and the counts can
    differ between readings.

    Median and p90 rather than a corpus-level rate, for the reason
    `docs/EVALUATION_HARNESS.md` gives: two prompts for one vision model differed
    by 17 points corpus-wide and by 0.3 in the median, and the gap was two
    collapsed pages. Best and worst are there to be looked at, not averaged:
    this corpus mixes hands that read at 6 % with hands that read at 45 %.
    """
    by_reading: dict[str, list[float]] = {}
    for item in scored:
        if not item.located:
            continue
        for m in item.matches:
            if m.key:
                by_reading.setdefault(m.reading, []).append(m.cer)
    out = [
        ReadingScore(reading=reading, pages=len(cers),
                     median=_quantile(cers, 0.5), p90=_quantile(cers, 0.9),
                     best=min(cers), worst=max(cers))
        for reading, cers in by_reading.items() if cers
    ]
    return sorted(out, key=lambda r: r.median)


def duplicate_pages(scored: list[Scored]) -> dict[str, list[Scored]]:
    """The corpus pages that more than one ground-truth file describes, by key.

    Two exports of the same Transkribus document, or two hand-corrected files that
    locate the same scan, arrive here as separate files. They are **not**
    duplicates to drop: each carries its own text and status, and which one to
    believe is a question for a person. But an average over files weights such a
    page twice, so the report says how many files describe how many pages instead
    of printing one number that is quietly both.

    Keyed on the located page, because that is the thing being double-counted. A
    file that locates nothing names no page and cannot collide with one.
    """
    by_key: dict[str, list[Scored]] = {}
    for item in scored:
        if item.located:
            by_key.setdefault(item.located, []).append(item)
    return {key: items for key, items in by_key.items() if len(items) > 1}


def format_summary(scored: list[Scored], unusable: Optional[list] = None) -> str:
    """Everything but the per-page sections: counts and the comparison table.

    Split out because the two have different readers. 552 pages of sections are
    the record — worth keeping in a file, unreadable in a terminal and beyond
    what a log tail or an MCP reply can carry. The summary is the answer, and it
    is short enough to be the last thing a job prints, which is the part a tail
    actually shows.
    """
    return "\n".join(_summary_lines(scored, unusable))


def _summary_lines(scored: list[Scored],
                   unusable: Optional[list] = None) -> list[str]:
    lines = ["# Readings against ground truth", ""]
    counts: dict[str, int] = {}
    for item in scored:
        counts[item.gt.status or "unknown"] = counts.get(
            item.gt.status or "unknown", 0) + 1
    if unusable:
        lines += [f"**{len(unusable)} file(s) could not be scored** and were "
                  f"skipped, not counted:", ""]
        for u in unusable[:10]:
            lines.append(f"- `{u.source.name}` — {u.reason}")
        if len(unusable) > 10:
            lines.append(f"- … {len(unusable) - 10} more")
        lines.append("")
    dupes = duplicate_pages(scored)
    if counts:
        mix = ", ".join(f"{n}× {st}" for st, n in sorted(counts.items()))
        lines += [f"{len(scored)} ground-truth page(s): {mix}.", ""]
        if dupes:
            over = sum(len(items) - 1 for items in dupes.values())
            lines += [
                f"**{len(dupes)} corpus page(s) are described by more than one "
                f"ground-truth file**, so those {len(dupes) + over} files cover "
                f"{len(dupes)} page(s): the mix above counts files. Each file keeps "
                f"its own section below; an average over them would weight such a "
                f"page twice. `--keys-out` already writes each page once.", ""]
            for key, items in sorted(dupes.items())[:10]:
                names = ", ".join(f"`{i.gt.source.name}`" for i in items)
                lines.append(f"- `{key}` — {names}")
            if len(dupes) > 10:
                lines.append(f"- … {len(dupes) - 10} more")
            lines.append("")
        ranked = score_by_reading(scored)
        if ranked:
            located = sum(1 for s in scored if s.located)
            lines += [
                "## How the readings compare", "",
                f"Over the {located} page(s) whose identity is settled. A page "
                f"the readings could not agree on, or that none of them placed "
                f"confidently, is left out of every row: its CER is the distance "
                f"to whichever corpus page happened to be least unlike it, which "
                f"is not a reading error. The counts can therefore differ.", "",
                "| reading | pages | median CER | p90 | best | worst |",
                "|---|---:|---:|---:|---:|---:|",
            ]
            lines += [r.row for r in ranked]
            lines += [
                "",
                "**Sorted by median, and the median is the number to compare.** A "
                "corpus-level rate would be dominated by the longest pages, and "
                "this corpus mixes hands: the best and worst columns are four to "
                "five times apart within a single engine, because Laßberg's own "
                "hand reads far worse than his correspondents'. An engine that "
                "happens to have located more of the easier pages would win a "
                "comparison that ignored that, which is why the stratification "
                "question in the published report is not a footnote.", "",
                "Two unrelated German pages of this corpus score about 68 % "
                "against each other. A median anywhere near that figure is the "
                "floor — it means the engine is producing German-shaped text "
                "that does not correspond to the page.", "",
            ]
        lines += ["DONE and FINAL both count — a page marked DONE has been "
                  "corrected by a human, and this collection never used the GT "
                  "tag. Whether the two are equally reliable is worth watching: "
                  "if the CER splits along that line, the status is telling you "
                  "something about the correction and not about the model.", ""]
    return lines


def format_scores(scored: list[Scored], unusable: Optional[list] = None) -> str:
    """The numbers, with the match's own reliability beside them."""
    lines = _summary_lines(scored, unusable)
    for item in scored:
        gt = item.gt
        head = f"## {gt.source.name}"
        if gt.doc_id:
            head += f" — Transkribus doc {gt.doc_id}, page {gt.page_id}"
        lines += [head, "",
                  f"- {gt.lines} line(s), {len(gt.text)} characters, "
                  f"status {gt.status or 'unknown'}",
                  f"- first line: {gt.text.splitlines()[0][:70]!r}", ""]
        agreed = item.agreed_key
        if agreed:
            lines.append(f"- matched to `{agreed}` by every reading")
        else:
            lines.append("- **the readings matched different pages** — the match "
                         "is not reliable and these numbers describe nothing")
        lines += ["", "| reading | CER | matched page | runner-up CER | match |",
                  "|---|---:|---|---:|---|"]
        for m in sorted(item.matches, key=lambda m: m.cer):
            verdict = "clear" if m.confident else "**doubtful**"
            lines.append(f"| `{m.reading}` | {m.cer:.1%} | `{m.key or '—'}` | "
                         f"{m.runner_up_cer:.1%} | {verdict} |")
        lines.append("")
    lines += [
        "CER is against the hand-corrected text, so this *is* quality — the one "
        "table in this project that is. The ground truth is the reference and the "
        "reading the hypothesis, which is why the number is asymmetric here and "
        "deliberately symmetric in `compare-runs`.",
        "",
        "**One page is indicative, not decisive.** #416 asks whether the readings "
        "are a field of equals, and a single page cannot answer it for 6719. What "
        "it can do is rule out the case where one reading is far worse — and that "
        "is the case in which fusion was measured to lose.",
        "",
        "The ground truth hyphenates across lines (`anti¬` / `cipirt`); `¬` is not "
        "ASCII punctuation and survives normalisation, so a reading that joins the "
        "word instead pays about one character per hyphen. It is small, it is the "
        "same for every reading, and it is not a reading error.",
    ]
    return "\n".join(lines)


def readings_from_run_dirs(run_dirs: Iterable[Path]) -> dict[str, dict[str, str]]:
    """``{label: {key: text}}`` for every model under every run directory.

    Shares `compare_runs`' loader so a page means the same thing to both: the
    comparison and the scoring cannot end up reading different files.
    """
    from compare_runs import discover_readings

    return {r.label: r.pages for r in discover_readings(run_dirs)}


def score_run_dirs(gt_files: Iterable[Path], run_dirs: Iterable[Path],
                   ) -> tuple[list[Scored], list, str]:
    """Load, score and render. The one call a CLI or MCP tool needs.

    Returns the scored pages, the ones that could not be scored, and the report.
    The second of those is why it is a triple: a run that quietly dropped files
    would overstate how much ground truth it had.
    """
    files = [Path(f) for f in gt_files]
    if not files:
        raise GroundTruthError("no ground-truth files given")
    unusable: list[Unusable] = []
    scored = score(files, readings_from_run_dirs(run_dirs), unusable=unusable)
    if not scored:
        raise GroundTruthError(
            f"none of the {len(files)} ground-truth file(s) could be scored — "
            + "; ".join(f"{u.source.name}: {u.reason}" for u in unusable[:3]))
    return scored, unusable, format_scores(scored, unusable)


def expand_gt_paths(paths: Iterable[str]) -> list[Path]:
    """Accept files and directories; a directory contributes its ``*.xml``.

    A harvest from `download_latest_pagexml.py` arrives as a directory of them,
    and making the caller expand it is making the caller do shell quoting over
    filenames that contain spaces.
    """
    out: list[Path] = []
    for raw in paths:
        # `Path("")` is `Path(".")`, which is a directory, so an unset
        # `$GT_ROOT` turns into a walk of the whole working directory. That is
        # not theoretical: on 2026-10-02 `--gt "$GT_ROOT"` with the variable
        # unset in an interactive shell offered
        # `.venv/.../setuptools/command/launcher manifest.xml` as ground truth
        # and only failed later, on the run directory. Same trap as #503, where
        # the Transkribus dry-run inventoried the checkout and proposed
        # `astronaut.png` as a Laßberg letter.
        if not str(raw).strip():
            raise GroundTruthError(
                "empty ground-truth path — `$GT_ROOT` is probably unset in this "
                "shell. It lives in `.env`, which only Python reads; pass "
                "`--gt` with a real path or leave it out to use GT_ROOT from "
                "the config")
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(p.rglob("*.xml")))
        elif p.exists():
            out.append(p)
        else:
            raise GroundTruthError(f"no such ground truth: {p}")
    if not out:
        raise GroundTruthError("no .xml ground-truth files found")
    return out
