"""
writer_check.py — is a machine reading good enough to tell whose hand it is?

**The question this answers, and why it has to be asked first.** "300 pages in
Laßberg's own hand, none of them transcribed yet" is a request that bites its
own tail: the hand is inferred from the dateline, the dateline is in the
transcription, and an untranscribed page has none. Provenance does not rescue it
— Basel holds both sides of the correspondence in one folder (`doc7151991`,
Wackernagel, 8.1 % CER against `doc4726780`, Laßberg, 34.8 %, same directory).

What is left is to run the dateline rule over a *machine* reading, which most of
the corpus already has. That is a guess about a guess, and it is exactly the kind
of claim this project has stopped making without measuring: for the pages that
have **both** a machine reading and hand-corrected text, `writer_of` can be
applied to each and the two answers compared. This module does that comparison.

**The methodological trap, which would have flattered the result.** A
ground-truth page the dateline rule cannot read is labelled ``unbestimmt`` — and
that is an *absence of evidence*, not evidence of a third hand. Counting those
pages as "the machine got it wrong" would punish a machine reading for being
measured against a non-answer; counting them as "right" when the machine also
says ``unbestimmt`` would inflate agreement with pages nobody knows anything
about. Precision and recall here are therefore computed **only over the pages
whose ground truth names a hand**, and the undecided ones are reported beside
them as the size of what the measurement cannot see.

**Precision is the number that decides, not agreement.** The plan is to select
pages *because* the machine calls them Laßberg's. What that costs is the share of
those pages whose ground truth disagrees — precision. Recall only says how much
of his hand the selection would leave on the table, which for 300 pages out of
thousands does not matter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

#: The three labels `hf_export.writer_of` can return.
PROJECTS = ("lassberg", "korrespondenten", "unbestimmt")

#: The label that means "nothing in the dateline said anything". Not a hand.
UNKNOWN = "unbestimmt"


@dataclass
class Confusion:
    """One reading's labels against the ground truth's, page by page."""

    reading: str
    #: ``(ground truth label, machine label) -> pages``
    cells: dict = field(default_factory=dict)

    def add(self, truth: str, machine: str) -> None:
        self.cells[(truth, machine)] = self.cells.get((truth, machine), 0) + 1

    @property
    def pages(self) -> int:
        return sum(self.cells.values())

    @property
    def known(self) -> int:
        """Pages whose ground truth names a hand — the only ones that can score."""
        return sum(n for (truth, _), n in self.cells.items() if truth != UNKNOWN)

    @property
    def undecided(self) -> int:
        """Pages whose ground truth says nothing. Reported, never scored."""
        return self.pages - self.known

    def precision(self, project: str) -> float:
        """Of the pages the machine calls ``project``, the share the ground truth
        agrees with — counting only pages whose ground truth names a hand.

        This is the number a selection pays for: pick 300 pages because a machine
        called them Laßberg's, and 1 − precision of them are somebody else's.
        """
        picked = sum(n for (truth, machine), n in self.cells.items()
                     if machine == project and truth != UNKNOWN)
        if not picked:
            return 0.0
        hit = self.cells.get((project, project), 0)
        return hit / picked

    def recall(self, project: str) -> float:
        """Of the pages whose ground truth is ``project``, the share the machine
        also calls ``project``."""
        truth_total = sum(n for (truth, _), n in self.cells.items()
                          if truth == project)
        if not truth_total:
            return 0.0
        return self.cells.get((project, project), 0) / truth_total

    def picked(self, project: str) -> int:
        """How many pages the machine labels ``project``, undecided ones included
        — the size of the pool a selection would draw from."""
        return sum(n for (_, machine), n in self.cells.items()
                   if machine == project)

    @property
    def agreement(self) -> float:
        """Both labels equal, over the pages whose ground truth names a hand."""
        if not self.known:
            return 0.0
        same = sum(n for (truth, machine), n in self.cells.items()
                   if truth == machine and truth != UNKNOWN)
        return same / self.known


def compare(scored: Sequence, readings: dict) -> list[Confusion]:
    """One :class:`Confusion` per reading, over the located pages.

    ``readings`` is `gt_score.readings_from_run_dirs`' mapping — the texts are
    already in memory during a scoring run, so this costs no I/O.

    Only **located** pages: a page whose identity is not settled has no machine
    reading to compare against, and pairing it with the best guess would compare
    this letter's ground truth with another letter's reading.
    """
    from hf_export import writer_of

    out: dict[str, Confusion] = {}
    for item in scored:
        key = getattr(item, "located", "")
        if not key:
            continue
        truth = writer_of(item.gt.text).project
        for label, pages in sorted(readings.items()):
            text = pages.get(key)
            if text is None:
                continue
            conf = out.setdefault(label, Confusion(label))
            conf.add(truth, writer_of(text).project)
    return [out[k] for k in sorted(out)]


def format_comparison(confusions: Iterable[Confusion]) -> str:
    """The table a selection decision is made from."""
    rows = list(confusions)
    if not rows:
        return ("writer agreement: no located page has both a machine reading "
                "and a ground-truth label to compare.")
    lines = [
        "## Is a machine reading enough to tell whose hand it is?",
        "",
        "`writer_of` on each reading against `writer_of` on the hand-corrected "
        "text, over the located pages. **Precision and recall count only the "
        "pages whose ground truth names a hand** — a ground-truth page the rule "
        "cannot read is an absence of evidence, not a third hand, and scoring "
        "against it would measure the machine on a non-answer.",
        "",
        "| reading | scorable | agree | lassberg: precision | recall | pages it calls lassberg |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for c in sorted(rows, key=lambda c: -c.precision("lassberg")):
        lines.append(f"| `{c.reading}` | {c.known} | {c.agreement:.0%} | "
                     f"{c.precision('lassberg'):.0%} | "
                     f"{c.recall('lassberg'):.0%} | {c.picked('lassberg')} |")
    first = rows[0]
    lines += ["", f"{first.undecided} of {first.pages} located page(s) have no "
                  f"ground-truth hand at all and are left out of every rate above.",
              "", "Confusion for the most precise reading:", ""]
    best = max(rows, key=lambda c: c.precision("lassberg"))
    lines += [f"`{best.reading}`", "",
              "| ground truth ↓ / machine → | " + " | ".join(PROJECTS) + " |",
              "|---|" + "---:|" * len(PROJECTS)]
    for truth in PROJECTS:
        cells = " | ".join(str(best.cells.get((truth, m), 0)) for m in PROJECTS)
        lines.append(f"| {truth} | {cells} |")
    return "\n".join(lines)
