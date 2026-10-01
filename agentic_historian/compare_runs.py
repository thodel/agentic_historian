"""
compare_runs.py — put two or more batch readings of the same pages side by side.

The batch runner writes one directory per model per run and never compares them.
Everything this project owns for comparing candidates — `fusion.fuse`,
`path_compare`, `ensemble` — hangs off the **single-page** pipeline, keyed on a
`RunState`. After a corpus run there are two directories on disk and nothing that
reads both.

**What this measures, and what it cannot.** Pairwise disagreement, not quality.
Without ground truth there is no way to say which reading is right (#326), and a
reading that hallucinates fluently disagrees with the others exactly as much as
one that reads badly. What the number *does* settle is the precondition for
fusion: #416 measured that majority voting **loses** to the best single engine
where one candidate dominates a field of weaker ones (3.38 % against 4.82 %), and
wins only where the candidates are comparable and their errors uncorrelated. This
report says which of those two situations the readings are in. It does not say
who is right.

Pages where one reading is empty and another is not are counted separately rather
than folded into the distribution. Their disagreement is 100 % by construction,
which says something categorically different from 100 % between two garbled
texts — one of the two is simply missing, and averaging the two kinds together
hides both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Iterable, Optional

from loguru import logger

#: Above this pairwise CER `fusion.fuse` refuses to blend and returns the
#: highest-confidence candidate verbatim (#300). Reported here so the share of
#: pages fusion would decline is visible before anyone spends GPU hours on a
#: third reading.
from config import ENSEMBLE_NO_MERGE_CER as _NO_MERGE_DEFAULT  # noqa: E402


@dataclass
class Reading:
    """One model's output for one run — the unit being compared."""

    run: str
    model: str
    pages: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.run}/{self.model}"

    def chars(self, keys: Iterable[str]) -> float:
        """Mean characters per page over ``keys``, 0.0 when none are present.

        Taken over the shared keys rather than over everything the reading holds:
        two runs of different coverage would otherwise be compared on different
        material, and the number would describe the corpus, not the models.
        """
        present = [len(self.pages[k]) for k in keys if k in self.pages]
        return sum(present) / len(present) if present else 0.0


@dataclass
class PairStat:
    a: str
    b: str
    pages: int = 0
    median_disagreement: float = 0.0
    p90_disagreement: float = 0.0
    above_no_merge: int = 0
    worst: list[tuple[str, float]] = field(default_factory=list)


#: A page shorter than this on any side is counted but kept out of the
#: distribution. Two fragments — ``den |`` against ``der 1850``, five characters
#: and eight — disagree by 75 % and say nothing about whether the engines can read
#: this hand; left in, they move the median more than the letters do. A real page
#: of this corpus runs ~1100 characters, an address panel a few hundred, a stray
#: page number under fifty.
SUBSTANTIAL_CHARS = 100


@dataclass
class Comparison:
    readings: list[Reading] = field(default_factory=list)
    common: list[str] = field(default_factory=list)
    compared: list[str] = field(default_factory=list)
    short: list[str] = field(default_factory=list)
    repetitive: dict[str, list[str]] = field(default_factory=dict)
    one_sided_empty: dict[str, list[str]] = field(default_factory=dict)
    all_empty: list[str] = field(default_factory=list)
    pairs: list[PairStat] = field(default_factory=list)
    no_merge_cer: float = _NO_MERGE_DEFAULT
    min_chars: int = SUBSTANTIAL_CHARS


def load_reading(run_dir: Path, model: str) -> Reading:
    """Read every ``<key>.txt`` under ``run_dir/model``.

    The ``.txt`` is the transcription and the ``.json`` beside it the record; only
    the text is needed here, and reading the JSON for 6719 pages to get a field
    this function does not use would double the work.
    """
    pages: dict[str, str] = {}
    for txt in sorted((run_dir / model).glob("*.txt")):
        try:
            pages[txt.stem] = txt.read_text(encoding="utf-8")
        except OSError as exc:                       # noqa: PERF203 — report, continue
            logger.warning(f"[compare] unreadable {txt}: {exc}")
    return Reading(run=run_dir.name, model=model, pages=pages)


def discover_readings(run_dirs: Iterable[Path]) -> list[Reading]:
    """Every (run, model) pair under the given run directories, in a stable order.

    A run directory holds one subdirectory per model, so "two runs of one model"
    and "one run of two models" are the same shape to this function — which is
    what lets a comparison span both without a second code path.
    """
    out: list[Reading] = []
    for run_dir in run_dirs:
        run_dir = Path(run_dir)
        if not run_dir.is_dir():
            raise NotADirectoryError(f"not a run directory: {run_dir}")
        models = sorted(c.name for c in run_dir.iterdir()
                        if c.is_dir() and any(c.glob("*.txt")))
        if not models:
            logger.warning(f"[compare] no model output under {run_dir}")
        for model in models:
            out.append(load_reading(run_dir, model))
    return out


def disagreement(a: str, b: str) -> float:
    """Symmetric character disagreement between two readings, in [0, 1].

    ``eval.metrics.cer`` divides by the *reference* length, which is right when
    one side is ground truth and indefensible here: "trocr against the VLM" and
    "the VLM against trocr" would differ, and a comparison table whose numbers
    depend on column order is not a measurement. Dividing by the longer of the
    two is symmetric and still reduces to CER when the lengths match.

    Built from ``cer`` in both directions rather than from a second
    normalisation of its own: the edit distance is the same either way, so the
    two results differ only in their denominator, and the **smaller** of them is
    the distance over the longer text — symmetric, bounded by 1, and normalised
    by whatever ``cer`` normalises by, which is the only definition this project
    should have. Case, whitespace and punctuation are folded, because the engines
    disagree constantly about where a line ends and whether a comma has a space
    in front of it, and none of that is a disagreement about what the page says.

    Punctuation is removed here rather than left to ``cer``, which strips it
    *after* collapsing whitespace and so leaves a space behind: trocr writes
    "Exemplar ," where the VLM writes "Exemplar,", and that one space survives as
    an edit. Measured at 3.1 % on a sentence the two readings agree on word for
    word — a floor under every number in the report, from a spacing convention.
    The steps are the project's own, only in the order a symmetric pairwise
    measure needs them.
    """
    import string

    from eval.metrics import cer

    drop = str.maketrans("", "", string.punctuation)
    na, nb = a.translate(drop), b.translate(drop)
    if not na.strip() and not nb.strip():
        return 0.0
    return min(cer(na, nb, ignore_punctuation=False),
               cer(nb, na, ignore_punctuation=False))


def fusion_pairwise_cer(texts: list[str]) -> float:
    """The disagreement measure ``fusion`` itself applies its no-merge band to.

    Imported from `fusion` on purpose, private name and all: this report says what
    share of pages fusion would decline to blend, and computing that with slightly
    different arithmetic than fusion uses would make the answer wrong in exactly
    the way nobody would check.
    """
    from fusion import _max_pairwise_cer_of

    return _max_pairwise_cer_of([(f"c{i}", t) for i, t in enumerate(texts)])


def compare(readings: list[Reading], *,
            no_merge_cer: Optional[float] = None,
            worst: int = 10,
            min_chars: int = SUBSTANTIAL_CHARS) -> Comparison:
    """Pairwise disagreement over the pages every reading has.

    Only the intersection is compared. Two runs of different coverage — 899 pages
    against 6719, after the trocr run stopped early — have no common ground
    outside it, and quietly comparing a page one side never read would turn
    missing coverage into measured disagreement.
    """
    out = Comparison(readings=readings,
                     no_merge_cer=_NO_MERGE_DEFAULT if no_merge_cer is None
                     else no_merge_cer,
                     min_chars=min_chars)
    if len(readings) < 2:
        raise ValueError("comparing needs at least two readings")

    common = set(readings[0].pages)
    for r in readings[1:]:
        common &= set(r.pages)
    out.common = sorted(common)

    # One definition of "empty", "short" and "repetitive" for the whole project:
    # page_quality.classify. A second set of thresholds here would drift from the
    # runner's report, and then the same corpus would have two empty counts.
    from page_quality import classify

    for key in out.common:
        texts = [r.pages[key] for r in readings]
        graded = [(r.label, classify(t, short_chars=min_chars))
                  for r, t in zip(readings, texts)]
        verdicts = {label: q.verdict for label, q in graded}
        if all(v == "empty" for v in verdicts.values()):
            out.all_empty.append(key)
        elif any(v == "empty" for v in verdicts.values()):
            for label, v in verdicts.items():
                if v == "empty":
                    out.one_sided_empty.setdefault(label, []).append(key)
        elif any(v == "repetitive" for v in verdicts.values()):
            # Recorded against the reading that lost control, not merely counted:
            # which model pads a page is the diagnostically useful half, and a
            # page carrying 8 000 characters of one digit would otherwise sit in
            # the distribution and move every average that includes it.
            for label, v in verdicts.items():
                if v == "repetitive":
                    out.repetitive.setdefault(label, []).append(key)
        elif any(v == "short" for v in verdicts.values()):
            out.short.append(key)
        else:
            out.compared.append(key)

    for i, a in enumerate(readings):
        for b in readings[i + 1:]:
            scores = [(k, disagreement(a.pages[k], b.pages[k]))
                      for k in out.compared]
            fusion_cer = [fusion_pairwise_cer([a.pages[k], b.pages[k]])
                          for k in out.compared]
            stat = PairStat(a=a.label, b=b.label, pages=len(scores))
            if scores:
                values = sorted(s for _, s in scores)
                stat.median_disagreement = median(values)
                stat.p90_disagreement = values[min(len(values) - 1,
                                                   int(0.9 * len(values)))]
                stat.above_no_merge = sum(1 for c in fusion_cer
                                          if c > out.no_merge_cer)
                stat.worst = sorted(scores, key=lambda s: -s[1])[:worst]
            out.pairs.append(stat)
    return out


def format_report(c: Comparison) -> str:
    """The comparison as Markdown, saying plainly what it does not settle."""
    lines = ["# Reading comparison", ""]
    lines.append("| reading | pages | in common | mean chars/page (common) |")
    lines.append("|---|---:|---:|---:|")
    for r in c.readings:
        lines.append(f"| `{r.label}` | {len(r.pages)} | {len(c.common)} | "
                     f"{r.chars(c.compared):.0f} |")
    lines += ["", f"- pages every reading has: **{len(c.common)}**",
              f"- of those, compared (text on every side, "
              f"≥{c.min_chars} chars): **{len(c.compared)}**",
              f"- too short to compare (under {c.min_chars} chars somewhere): "
              f"{len(c.short)}",
              f"- empty in every reading: {len(c.all_empty)}"]
    for label, keys in sorted(c.repetitive.items()):
        lines.append(f"- **repetitive** (the model lost control and padded the "
                     f"page) in `{label}`: {len(keys)}")
    for label, keys in sorted(c.one_sided_empty.items()):
        lines.append(f"- empty **only** in `{label}`: {len(keys)}")
    lines.append("")

    lines.append("| pair | pages | median disagreement | 90th pct | "
                 f"above no-merge ({c.no_merge_cer:.0%}) |")
    lines.append("|---|---:|---:|---:|---:|")
    for p in c.pairs:
        share = f"{p.above_no_merge} ({p.above_no_merge / p.pages:.0%})" if p.pages else "—"
        lines.append(f"| `{p.a}` vs `{p.b}` | {p.pages} | "
                     f"{p.median_disagreement:.1%} | {p.p90_disagreement:.1%} | {share} |")
    lines.append("")

    lines += [
        "**This is disagreement, not quality.** Without ground truth neither "
        "column says which reading is right, and a model that hallucinates "
        "fluently disagrees exactly as much as one that reads badly (#326).",
        "",
        "What it does settle is whether fusion is worth trying. #416 measured "
        "majority voting **losing** to the best single engine where one candidate "
        "dominates weaker ones (3.38 % against 4.82 %), and winning only where the "
        "candidates are comparable and their errors uncorrelated. A low median "
        "with similar characters per page is the second case; a wide gap in "
        "characters per page, or many pages empty on one side only, is the first.",
        "",
    ]

    for p in c.pairs:
        if not p.worst:
            continue
        lines.append(f"## Widest disagreement — `{p.a}` vs `{p.b}`")
        lines.append("")
        lines.append("Look at these images before reading anything into the "
                     "numbers above: a page both readings garble differently is "
                     "where a third reading would earn its cost.")
        lines.append("")
        for key, score in p.worst:
            lines.append(f"- `{key}` — {score:.1%}")
        lines.append("")
    return "\n".join(lines)


def compare_run_dirs(run_dirs: Iterable[Path], *,
                     no_merge_cer: Optional[float] = None,
                     worst: int = 10,
                     min_chars: int = SUBSTANTIAL_CHARS) -> tuple[Comparison, str]:
    """Load, compare and render. The one call a CLI or MCP tool needs."""
    readings = discover_readings(run_dirs)
    try:
        import rapidfuzz                             # noqa: F401
    except ImportError:
        logger.warning(
            "[compare] rapidfuzz is not installed — the pure-Python edit distance "
            "is quadratic in page length and this will take hours on a corpus. "
            "It is in requirements.txt; install it rather than waiting."
        )
    c = compare(readings, no_merge_cer=no_merge_cer, worst=worst,
                min_chars=min_chars)
    return c, format_report(c)
