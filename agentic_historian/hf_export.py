"""
hf_export.py — the located ground-truth pages, in the shape `pagexml-hf` reads.

**Why this does not convert anything.** The Flow Project's `pagexml-hf` already
turns Transkribus PAGE XML into a parquet dataset on the hub, and every one of the
fourteen `dh-unibe/image-text_*` datasets was built with it. Writing a second
converter would mean a second column layout for the trainer to tolerate, and
`serving-atr-inference`'s `hf_source.py` already carries a list of aliases because
that happened once (``xml_content``/``xml``, ``project_name``/``project``). So this
module produces only what that converter takes as input — a Transkribus-export tree:

    <export>/<project>/<page>.jpg
    <export>/<project>/page/<page>.xml

and the upload is `pagexml-hf <export> --repo-id … --private`.

**The check this module exists for.** The PAGE XML's line polygons are in the
pixel coordinates of *Transkribus's* copy of the scan. The JPEG beside it is this
pipeline's working copy of the *share's* scan. If those two differ in size — a
different derivative, a different scan, a rescaled export — then every polygon is
wrong, and a line-level dataset built from it would crop the wrong strip of every
page and call it ground truth. That failure is invisible downstream: the crops are
plausible images and the text is real text, so the only symptom is a model that
trains badly for no stated reason.

``PcGts/Page`` records ``imageWidth`` and ``imageHeight``, so the check is a
subtraction, and :func:`plan` refuses a page whose geometry disagrees rather than
rescaling it. Rescaling would be a guess about which of two scans is authoritative,
and this module does not have the standing to make it.

**The project split is a heuristic, and labelled as one.** `project` becomes a
directory in the dataset and is the axis any later stratification uses, which is
why it has to be the *hand* and not the archive: measured on this corpus, one
engine reads Laßberg's correspondents at 6–20 % CER and Laßberg himself at 27–45 %,
and Basel holds both sides of the correspondence in one folder. The hand is not
recorded anywhere in the data, so it is inferred from the dateline — which is a
guess about the first two lines of a letter, right often and not always. A page
whose dateline says nothing goes to ``unbestimmt`` rather than to a coin flip, and
:func:`format_plan` prints the three counts so the size of the guess is visible
before anything is uploaded.

**The hand belongs to the letter, not to the page.** The first dry run with
images, 2026-10-05, labelled 57 of 211 pages and left 154 ``unbestimmt`` — and
the reason is not unreadable datelines. A dateline stands on a letter's *first*
page; every continuation page is a page the rule cannot possibly read, because
there is nothing there to read. So the writer is inherited along the letter:
:func:`letter_of` says which pages belong to one letter, :func:`inherit` lets the
letter's dated page decide, and the undated pages of that letter take its hand.

The grouping key is the **letter folder** (``lassberg-letter-NNNN``), not the
shelfmark in the filename. That was measured on the 241 pages of
``atr_gt_candidates`` before it was chosen, because the filenames carry a
shelfmark and it looked like the better key:

===========================  =======================  ===================
grouping                     pages covered            letters per group
===========================  =======================  ===================
``lassberg-letter-NNNN``     216 / 241                1
filename shelfmark           241 / 241                up to **36**
===========================  =======================  ===================

``Basel__lassberg-letter-*__PA 82a B 9_Seite_NNN`` is 115 pages under **one**
shelfmark spanning 36 letters, and ``Staatsarchiv Thurgau``'s ``…__75-1_00NNN``
spans 21. These shelfmarks name an archival *bundle*, not a letter — and Basel is
exactly the folder that holds both sides of the correspondence, so inheriting
along it would hand 36 letters a single hand. The shelfmark is unusable as the
decisive key; the 25 pages with no letter folder fall back to their own folder,
and the two that are loose in an archive root inherit nothing.

A letter whose dated pages *disagree* inherits nothing either, and is named in the
plan. Two hands under one letter folder means either the folder holds a letter and
its reply or the dateline rule misfired, and both are things to look at rather than
to average.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger

import config

#: Places Laßberg wrote from. Eppishausen is his house in Thurgau until 1838,
#: Meersburg the castle after it; a letter headed with either is in his hand.
#: Heimenstein and Dagebertsburg are his own names for the same two houses and
#: appear in the datelines of letters he wrote.
#: ``eppish`` rather than the three spellings it replaces: the survey of
#: 2026-10-08 printed ``Eppishausen``, ``Eppishaus`` and ``Eppish.`` as datelines
#: of the same house, and the prefix is exactly their common part — it cannot
#: match anything else in this corpus, and it does not match ``Villa Epponis``,
#: Laßberg's latinised name for it, which appears in a letter *to* him.
LASSBERG_PLACES = ("eppish", "meersburg", "heimenstein",
                   "dagebertsburg", "waldklause")

#: Places his correspondents wrote from. Not an exhaustive list of the
#: correspondence — only the places that appear in a dateline often enough to be
#: worth a rule, and none of which Laßberg wrote from.
CORRESPONDENT_PLACES = ("basel", "zürich", "zurich", "frauenfeld", "st. gallen",
                        "sanct gallen", "schafhausen", "schaffhausen", "paris",
                        "constanz", "konstanz", "lostorf", "arau", "aarau",
                        "weimar", "göttingen", "goettingen", "stuttgart",
                        "ueberlingen", "überlingen",
                        # From the survey of 2026-10-08, which is the only reason
                        # these two are here rather than nineteen more guesses:
                        # "Copia . / Berlin 3. Februar. 1853." and
                        # "105 / Würzburg den 17. Febr. 1842 ."
                        "berlin", "würzburg", "wuerzburg")

#: Laßberg's own shorthand for Eppishausen in a dateline. The survey of
#: 2026-10-08 found it five times among 55 undated letters — his hand, with a
#: date, in legible script, and the rule saw an ``E.`` that no list holds:
#:
#:     196. / E. am 15 Julij 1831.
#:     E. am 4. Juny 1830. / Ich sende Inen, mein vererter Herr und nachbar!
#:
#: Anchored at the start of a line and followed by ``am``/``den`` and a digit,
#: because a bare ``E.`` is an initial, a note reference or a line number. Only
#: ``E. am`` was observed; ``den`` is the same idiom and costs nothing. There is
#: no ``M.`` for Meersburg here — nobody has seen one, and inventing it is the
#: guessing this survey exists to replace.
LASSBERG_SHORTHAND = re.compile(r"^\s*e\.\s*(?:am|den)\s*\d", re.I | re.M)

#: How many lines of a page the dateline may hide in. A letter's place and date
#: are on the first line or two; past that, a place name is as likely to be
#: something the letter talks about as where it was written.
DATELINE_LINES = 2

#: A line that is only archival numbering: ``1256``, ``No 85``, ``No. 85.``,
#: ``209.``, ``13-30.``, ``2f``, ``5-24``. The survey of 2026-10-08 found one at
#: the head of 14 of 55 undated letters and in *both* lines the rule reads in ten
#: of them — and the next survey, four lines wide, showed what stood below:
#:
#:     lassberg-letter-1009: 1256 / No 85 / Constanz am 7 July 1825.
#:     lassberg-letter-1015: 1264 / 163. / No. 85. / Constanz am 30 July 1825.
#:     lassberg-letter-1486: 282. / 198 / Eppish. am 1.ten 8br. 1830.
#:
#: Eight real datelines, in places the list already held, behind a foliation the
#: window was spending itself on. So the window starts after them — all of them,
#: because 1015 has three.
_FOLIATION = re.compile(
    r"^[\s.,;:/()-]*(?:n[or]\.?\s*)?\d+(?:\s*[-–/]\s*\d+)*\s*[a-z]?[\s.,;:/()-]*$",
    re.I)

#: The longest a line may be and still be read as a dateline.
#:
#: Skipping the foliation reveals the line below, and once in the measured corpus
#: that line is prose that happens to name a place:
#:
#:     Weimar__FA Hodel 236: 284. / Lieber Leonhard! / Im Jare des heiles 1473.
#:       als Konstanz noch keine offizin hatte, wurde dahier ein buch gedruckt…
#:
#: "as Constance had no printing office yet" is not where the letter was written.
#: A dateline is a short line standing on its own: every one the survey printed
#: measures 18 to 44 characters, and that prose line measures 121. The cap sits
#: between, nowhere near either.
DATELINE_CHARS = 80

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Writer:
    """Which hand a page is in, and what said so."""

    project: str                 # "lassberg" | "korrespondenten" | "unbestimmt"
    evidence: str = ""           # the place matched, or "" when nothing did

    @property
    def certain(self) -> bool:
        return self.project != "unbestimmt"


def dateline_lines(text: str, *, lines: int = DATELINE_LINES,
                   chars: int = DATELINE_CHARS) -> list[str]:
    """The lines a dateline could be on: the first few, past any foliation.

    Two corrections the surveys of 2026-10-07/08 produced, both measured rather
    than reasoned about. A leading archival number is skipped, because it was
    spending the window in ten of 55 undated letters and eight real datelines
    stood right below it. And a line longer than ``chars`` is left out, because
    the line a skip reveals is sometimes prose that names a place — see
    :data:`DATELINE_CHARS`.

    Deliberately *not* "read four lines". The same survey showed why: a
    correspondent's letter whose third line reads "wartet die mitkommende
    Lieferung des Morgenblattes auf Gelegenheit nach **Eppishausen** befördert zu
    werden" would become Laßberg's own hand. Eppishausen is where that letter was
    going, not where it was written, and a wider window cannot tell the
    difference. Skipping a foliation can: a page whose first line is prose is not
    skipped at all.
    """
    rest = [l for l in text.strip().splitlines()]
    while rest and (not rest[0].strip() or _FOLIATION.match(rest[0])):
        rest.pop(0)
    return [l for l in rest[:lines] if len(l.strip()) <= chars]


def writer_of(text: str, *, lines: int = DATELINE_LINES) -> Writer:
    """The hand, inferred from the dateline.

    Laßberg's own letters are headed with the house he wrote from; his
    correspondents' with a city. Both sides of a correspondence sit in the same
    archive folder, so the dateline is the only signal in the data — and it is a
    signal about two lines of text, not a record. A page with no recognisable
    place is ``unbestimmt``, which is the honest answer and not a third hand.
    """
    head = "\n".join(dateline_lines(text, lines=lines)).lower()
    for place in LASSBERG_PLACES:
        if place in head:
            return Writer("lassberg", place)
    # Before the cities, not after: a page headed "E. am 23. August 1831." that
    # goes on to discuss Basel is his, and the first match would otherwise win.
    if LASSBERG_SHORTHAND.search(head):
        return Writer("lassberg", "eppishausen (E.)")
    for place in CORRESPONDENT_PLACES:
        if place in head:
            return Writer("korrespondenten", place)
    return Writer("unbestimmt")


#: A letter's own folder in the share: ``lassberg-letter-1741``,
#: ``lassberg-letter-0000-1808-08-31``. The corpus uses it in every archive that
#: sorted its scans by letter, which is seven of the nine holding ground truth.
_LETTER_SEG = re.compile(r"lassberg-letter-[0-9][\w.-]*")


@dataclass(frozen=True)
class Group:
    """Which letter a page belongs to, and how that was decided."""

    name: str = ""
    basis: str = ""              # "letter" | "folder" | ""

    def __bool__(self) -> bool:
        return bool(self.name)


def letter_of(key: str) -> Group:
    """The letter a page key belongs to.

    The page key is a path with its separators folded to ``__``, so the folders
    are still in it. Three cases, in order:

    * a ``lassberg-letter-NNNN`` segment — the letter, named by the corpus
      itself. 216 of the 241 candidate pages, and no letter id appears under two
      archives, so the segment alone identifies it.
    * no such segment, but the page sits in a folder below its archive — that
      folder. ``blb lassberg__K 2911,104__page_0001`` is a shelfmark folder whose
      pages are one piece; ``Weimar__FA Hodel 236__Weimar__GSA_96_1743_Seite3``
      likewise. 23 pages.
    * loose in the archive root — nothing. ``Winterthur__101-MsBRH_466-56-071``
      and ``…-072`` are two shelfmarks side by side, and grouping on the archive
      would make the whole of Winterthur one letter. 2 pages, which keep whatever
      their own dateline says.

    Deliberately **not** the shelfmark in the filename: ``PA 82a B 9`` covers 115
    Basel pages across 36 letters, and Basel holds both sides of the
    correspondence. See the module docstring for the measurement.
    """
    segs = key.split("__")
    for seg in segs:
        if _LETTER_SEG.fullmatch(seg):
            return Group(seg, "letter")
    if len(segs) >= 3:
        return Group("__".join(segs[:-1]), "folder")
    return Group()


@dataclass
class Hands:
    """What inheriting the writer along each letter did. Counted, not asserted."""

    from_register: int = 0       # pages a recorded sender decided
    decided: int = 0             # pages whose own dateline named a place
    inherited: int = 0           # undated pages that took their letter's hand
    alone: int = 0               # undated pages with no dated page to inherit from
    letters: int = 0             # groups a dated page decided
    #: Letters whose dated pages disagree: ``(group, {project: pages})``. Nothing
    #: inherits in them, because two hands under one letter folder is either a
    #: folder holding a reply or a misfire of the dateline rule.
    conflicts: list = field(default_factory=list)
    #: Letters decided only by a page that is not their first. A dateline stands
    #: on a first page, so this is the shape a misfire has — a continuation page
    #: that happens to mention a city in its opening lines — and inheritance
    #: spreads it over the whole letter. Inherited anyway, and named here.
    late: list = field(default_factory=list)
    #: Letters where the register and the page's own dateline disagree:
    #: ``(letter, register project, dateline project, the place that was read)``.
    #: The register wins — it is a record and the dateline is an inference — but
    #: one of the two is then wrong about a specific letter, and that is worth
    #: seeing rather than resolving in silence.
    disputed: list = field(default_factory=list)


def inherit(entries: Sequence["Entry"]) -> Hands:
    """Give every undated page of a letter the hand its dated page names.

    Mutates the entries in place and returns what it did. A page that read its
    own dateline keeps it; only ``unbestimmt`` pages inherit, and only from a
    letter whose dated pages agree.
    """
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        g = letter_of(e.key)
        e.group, e.basis = g.name, g.basis
        if g:
            groups.setdefault(g.name, []).append(e)

    hands = Hands(decided=sum(1 for e in entries if e.project != "unbestimmt"))
    for name, pages in sorted(groups.items()):
        dated = [e for e in pages if e.project != "unbestimmt"]
        said = {e.project for e in dated}
        if not said:
            continue
        if len(said) > 1:
            counts: dict[str, int] = {}
            for e in dated:
                counts[e.project] = counts.get(e.project, 0) + 1
            hands.conflicts.append((name, counts))
            continue
        hands.letters += 1
        first = min(pages, key=lambda e: e.key)
        if first.project == "unbestimmt":
            hands.late.append(name)
        decider = min(dated, key=lambda e: e.key)
        for e in pages:
            if e.project == "unbestimmt":
                e.project = decider.project
                e.evidence = f"inherited from {name} ({decider.evidence})"
                e.inherited = True
                hands.inherited += 1
    hands.alone = sum(1 for e in entries if e.project == "unbestimmt")
    return hands


#: How much of a page's opening the survey shows. Long enough for a dateline with
#: a place, a date and a salutation; short enough that fifty of them are readable.
HEAD_CHARS = 220

#: How many lines the **survey** shows — deliberately more than
#: :data:`DATELINE_LINES`, because the survey exists to see what the rule cannot.
#:
#: The survey of 2026-10-08 found an archival foliation occupying the first line
#: of 14 of 55 undated letters, and in ten of them *both* lines the rule reads:
#:
#:     lassberg-letter-1009: 1256 / No 85
#:     lassberg-letter-3111: 84. / 171
#:
#: What stands below those numbers decides whether the rule's window is the
#: problem, and widening the window on a hunch is how the nineteen places were
#: chosen. So the survey widens and the rule does not: a place on the third line
#: still decides nothing, it only becomes visible.
SURVEY_LINES = 6

#: How many letters :func:`format_plan` shows openings for. The rest are counted.
#: A survey the length of the corpus is one nobody reads, and the point of this
#: one is to be read once and then be unnecessary.
SURVEY_SHOWN = 60


def opening(text: str, *, lines: int = DATELINE_LINES,
            chars: int = HEAD_CHARS) -> str:
    """The lines :func:`writer_of` reads, on one line, short enough to print.

    Tabs and newlines out, because this ends up in a TSV column, and a letter
    whose dateline contains a tab would silently shift every column after it.
    """
    head = " / ".join(l.strip() for l in text.strip().splitlines()[:lines]
                      if l.strip())
    head = head.replace("\t", " ").replace("\r", " ")
    return head[:chars] + ("…" if len(head) > chars else "")


def dateline_survey(p: "Plan") -> list[tuple[str, str]]:
    """``(letter, opening)`` for every letter nothing dated — its first page.

    The measurement the place lists need. :func:`writer_of` knows nineteen places
    for a correspondence across half of Europe, and on 2026-10-07 a hundred of 211
    pages sat in letters where no page named one of them. Whether that is because
    the datelines are unreadable or because the list is short is not something to
    reason about: it is the openings of those letters, side by side, and this
    produces them.

    One row per letter, because the dateline is on the first page and a letter's
    other pages add nothing. Pages with no letter at all come last, each its own
    row — they are their own first page.
    """
    by_letter: dict[str, list[Entry]] = {}
    loose: list[Entry] = []
    for e in p.entries:
        if e.project != "unbestimmt":
            continue
        (by_letter.setdefault(e.group, []) if e.group else loose).append(e)
    rows = [(letter, min(pages, key=lambda e: e.key).head)
            for letter, pages in sorted(by_letter.items())]
    rows += [(e.key, e.head) for e in sorted(loose, key=lambda e: e.key)]
    return rows


@dataclass
class Entry:
    """One page of the export: an image, its PAGE XML, and the project it joins."""

    key: str                     # the corpus page key
    image: Path                  # the working copy to copy in
    xml_source: Path             # the ground-truth file
    project: str
    evidence: str = ""
    stem: str = ""               # filesystem-safe name shared by jpg and xml
    head: str = ""               # the lines writer_of read, for the survey
    group: str = ""              # the letter this page belongs to
    basis: str = ""              # how that letter was identified
    inherited: bool = False      # the hand came from the letter, not this page
    from_register: bool = False  # the hand is recorded, not inferred
    #: The factor the XML's coordinates are divided by on the way out, when the
    #: ground truth describes the same scan at another resolution. ``None`` for
    #: a page whose XML already describes its image, which is 241 of the 254
    #: the run of 2026-10-14 exported. (The sixteen rescalable *files* describe
    #: thirteen pages; three are second copies, refused as duplicates.)
    scale: Optional[float] = None
    claimed: Optional[tuple[int, int]] = None   # the size the XML records
    actual: Optional[tuple[int, int]] = None    # the size of the image

    def __post_init__(self) -> None:
        if not self.stem:
            self.stem = _UNSAFE.sub("_", self.key).strip("_") or "page"


@dataclass
class Rejected:
    """A page left out, and why. Counted, never silent.

    ``reason`` is the sentence a human reads. The two sizes are carried
    **beside** it rather than read back out of it: grouping 43 rejections by
    their signature means comparing numbers, and a report that parses its own
    prose is one rename away from counting nothing (#624).
    """

    key: str
    reason: str
    #: What the XML claims and what the image is, for a geometry rejection.
    claimed: Optional[tuple[int, int]] = None
    actual: Optional[tuple[int, int]] = None

    @property
    def category(self) -> str:
        """The reason's first word — what ``Plan.reasons`` counts by."""
        return self.reason.split(":", 1)[0]

    @property
    def signature(self) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
        """The (claimed, actual) pair, when both are known."""
        if self.claimed is None or self.actual is None:
            return None
        return self.claimed, self.actual


@dataclass
class Plan:
    entries: list[Entry] = field(default_factory=list)
    rejected: list[Rejected] = field(default_factory=list)
    hands: Hands = field(default_factory=Hands)
    #: How many lines of each page the survey captured. Carried rather than
    #: counted back out of the text: a letter whose own line contains " / " would
    #: make the count wrong, and so would truncation dropping a separator.
    survey_lines: int = SURVEY_LINES

    @property
    def by_project(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.project] = out.get(e.project, 0) + 1
        return out

    @property
    def reasons(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejected:
            head = r.category
            out[head] = out.get(head, 0) + 1
        return out

    @property
    def rescaled(self) -> list["Entry"]:
        """The exported pages whose coordinates were divided by a factor.

        A property rather than a counter kept during the walk: the entries are
        the record, and a count maintained beside them is a second truth to
        keep in step (#626).
        """
        return [e for e in self.entries if e.scale is not None]

    @property
    def signatures(self) -> dict[tuple[tuple[int, int], tuple[int, int]], int]:
        """How many pages each (claimed, actual) size pair accounts for.

        The count of geometry rejections says how many pages are out; the
        signature says **why**, and the three causes are told apart by numbers
        alone. A swap is a rotated scan, a few pixels is one renderer against
        another, and a different aspect ratio is a different derivative of the
        scan — only the last is a page this export can do nothing about.
        """
        out: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
        for r in self.rejected:
            sig = r.signature
            if sig is not None:
                out[sig] = out.get(sig, 0) + 1
        return out


def default_source() -> Path:
    """Where the page cache is, when nobody says.

    The same fallback `mcp_atr.jobs.cache_dir_for` applies to a ``dav:`` source:
    ``ATR_PAGE_CACHE`` when it is set, and ``DATA_DIR/page_cache`` when it is not.
    A test holds the two together, because two answers to "where is the cache"
    is how a run reads one directory and an export looks in another.

    This exists because `--source` was required while `--run-dir` and `--gt`
    resolved their own defaults, so the runbook said `--source "$ATR_PAGE_CACHE"`
    — and on 2026-10-03 that shell variable was set but **not exported**. bash
    put it in the argv, `os.environ` never saw it, `config.ATR_PAGE_CACHE` was
    None, and the path it named was a directory that existed and held other
    images. The export indexed 357 pages under keys that could not match, and
    reported "no image" with no way to tell that from an empty cache. The third
    diagnosis in a row that an argument default would have prevented.
    """
    return Path(config.page_cache_dir())


def page_index(source_root: Path,
               archive: Optional[Path] = None) -> dict[str, Path]:
    """``{page key: image path}`` for a corpus directory, and its cold tier.

    Built by walking the directory and asking the **runner's own** key rule for
    each image, rather than by unfolding ``__`` back into separators. The fold is
    lossy — a folder whose own name contains a double underscore would unfold
    into the wrong path — and more importantly a reconstruction here could drift
    from the runner's rule, which would silently pair a ground-truth page with a
    different page's image.

    **``archive`` is not optional in practice, and assuming otherwise cost a
    run.** The page cache is a cache, not a store: a daily job moves anything
    older than two days to the research share with its paths preserved (#487),
    which is what `images.cold_fetch` looks in before going to the wire. This
    function walked only the hot directory, so on 2026-10-02 an export of 276
    pages found **none** of them — the corpus run that made those working copies
    was twelve days old, so every one had been evicted. "Where the pages are" was
    a question with two answers and the code knew one.

    Hot wins on a collision: the same key in both tiers means an entry was brought
    back and the archived copy is the older generation of the two.

    Only standalone images, and deliberately *not* through
    :func:`atr_batch.discover_pages`. That is where this started: discovery
    expands every PDF in the corpus into its page count, and this function
    discarded those pages anyway, because a page inside a PDF has no file of its
    own for the export to copy. Paying for the expansion was merely wasteful
    until a host without `pypdfium2` turned it into a `ModuleNotFoundError` four
    frames below anything that mentions PDFs, which ended the export on the first
    PDF in the cache.
    """
    import atr_batch as batch

    exts = {e.lower() for e in batch.IMAGE_EXTS}

    def walk(root: Path) -> dict[str, Path]:
        found: dict[str, Path] = {}
        if not root.is_dir():
            return found
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in exts:
                found[batch.page_key(path.relative_to(root))] = path
        return found

    cold = walk(Path(archive)) if archive else {}
    hot = walk(Path(source_root))
    if cold:
        logger.info(f"[hf] {len(hot)} page(s) in the cache, {len(cold)} in the "
                    f"cold tier ({len(set(cold) - set(hot))} only there)")
    return {**cold, **hot}


def image_size(path: Path) -> tuple[int, int]:
    """``(width, height)`` of an image file."""
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def xml_size(xml_text: str) -> Optional[tuple[int, int]]:
    """``(imageWidth, imageHeight)`` the PAGE XML claims, or None when it says
    nothing. Absent is not "matches" — :func:`plan` treats it as unverifiable."""
    m = re.search(r'imageWidth="(\d+)"[^>]*imageHeight="(\d+)"', xml_text)
    if not m:
        m = re.search(r'imageHeight="(\d+)"[^>]*imageWidth="(\d+)"', xml_text)
        if not m:
            return None
        return int(m.group(2)), int(m.group(1))
    return int(m.group(1)), int(m.group(2))


def plan(scored: Sequence, index: dict[str, Path], *,
         require_geometry: bool = True,
         survey_lines: int = SURVEY_LINES,
         register=None) -> Plan:
    """What to export, and what to leave out.

    Only **located** pages: a page whose identity is not settled has no image to
    pair with, and pairing it with the best guess would put a different letter's
    scan beside this letter's transcription.

    A page whose XML geometry disagrees with its image is rejected, not rescaled.
    See the module docstring: rescaling is a guess about which of two scans is
    authoritative, and the failure it would paper over is invisible downstream.
    """
    out = Plan(survey_lines=survey_lines)
    seen: set[str] = set()
    for item in scored:
        key = getattr(item, "located", "")
        name = item.gt.source.name
        if not key:
            out.rejected.append(Rejected(name, "not located: no page matched "
                                               "confidently enough to pair an image"))
            continue
        if key in seen:
            # Transkribus holds the same page under several doc ids; exporting it
            # twice would hand the trainer one page with twice the weight.
            out.rejected.append(Rejected(name, f"duplicate: {key} already exported"))
            continue
        image = index.get(key)
        if image is None:
            out.rejected.append(Rejected(name, f"no image: {key} is not under the source"))
            continue
        xml_text = item.gt.source.read_text(encoding="utf-8", errors="replace")
        claimed = xml_size(xml_text)
        # Set here and not inside the branch: with the check off, or with no
        # geometry to read, there is no factor and no sizes to record — and a
        # name that exists only on some paths is how a later `if scale` reads a
        # previous page's value.
        scale: Optional[float] = None
        actual: Optional[tuple[int, int]] = None
        if require_geometry:
            if claimed is None:
                out.rejected.append(Rejected(name, "no geometry: the XML records no "
                                                   "imageWidth/imageHeight to check"))
                continue
            try:
                actual = image_size(image)
            except Exception as exc:          # noqa: BLE001 — one page, not the job
                out.rejected.append(Rejected(name, f"unreadable image: {exc}"))
                continue
            if actual != claimed:
                # Two sizes that one factor relates on both axes are the same
                # scan at two resolutions, and the polygons survive division by
                # it. Measured: the sixteen pages this admits agree to 0.11 %
                # or better, the nearest rejected pair is 0.78 % out. Any other
                # disagreement is two crops of the page, and no arithmetic
                # relates those — that page stays out.
                factor = uniform_scale(claimed, actual)
                if factor is None:
                    out.rejected.append(Rejected(
                        name, f"geometry: XML says {claimed[0]}x{claimed[1]}, the "
                              f"image is {actual[0]}x{actual[1]} — the line "
                              f"polygons do not describe this image",
                        claimed=claimed, actual=actual))
                    continue
                scale = factor
        # The dateline is read either way: when the register also covers this
        # letter the two can be compared, and a disagreement means one of them is
        # wrong about a specific letter.
        who = writer_of(item.gt.text)
        recorded = register.get(letter_of(key).name) if register else None
        entry = Entry(key=key, image=image, xml_source=item.gt.source,
                      project=who.project, evidence=who.evidence,
                      head=opening(item.gt.text, lines=survey_lines),
                      scale=scale, claimed=claimed, actual=actual)
        if recorded is not None:
            if who.certain and who.project != recorded.project:
                out.hands.disputed.append((recorded.id, recorded.project,
                                           who.project, who.evidence))
            entry.project = recorded.project
            entry.evidence = recorded.evidence
            entry.from_register = True
        seen.add(key)
        out.entries.append(entry)
    # The hand belongs to the letter: the dateline is on a first page, so every
    # continuation page is one the rule cannot read. Done over the whole plan
    # rather than per page, because a page's letter is only visible here.
    disputed = out.hands.disputed
    out.hands = inherit(out.entries)
    out.hands.disputed = disputed
    out.hands.from_register = sum(1 for e in out.entries if e.from_register)
    return out


#: How many pixels two sizes may differ by and still count as the same page
#: rendered twice. One renderer rounding a page's width where another truncates
#: it is a sub-pixel disagreement; it is named here so that reading it as
#: "a different scan" is not possible.
ROUNDING_PX = 2

#: How far two axes' scale factors may differ and still count as one uniform
#: rescale (0.5 %). Below it the polygons are recoverable by multiplication;
#: above it the two images are not the same crop.
UNIFORM_TOLERANCE = 0.005

#: Signatures printed in the plan. The rest are in the rejection table — the cap
#: belongs where the report is rendered, never where the rows are collected (#617).
SIGNATURES_SHOWN = 8

#: Rejected pages named in the plan, for the same reason.
REJECTED_SHOWN = 5

#: Rescaled pages named in the plan. Sixteen on this corpus, so the cap is above
#: the whole set deliberately: a page whose coordinates this pipeline rewrote is
#: the one a reader should be able to go and look at.
RESCALED_SHOWN = 20


def uniform_scale(claimed: tuple[int, int],
                  actual: tuple[int, int]) -> Optional[float]:
    """The one factor between two sizes, when both axes agree on it.

    ``None`` when they do not — then the two images are different crops of the
    page and no multiplication relates them. The *one* place this is decided:
    :func:`describe_signature` names it and :func:`plan` acts on it, and two
    functions answering "is this a rescale" differently is how a page gets
    exported with polygons nothing checked (#637).

    Measured on this corpus, the sixteen rescaled pages agree to 0.11 % or
    better, and the nearest rejected pair is 0.78 % out — so the boundary is not
    in a crowded place. It is not one pipeline either: the factors are 1.58,
    1.62, 1.99, 2.10, 2.26, 2.63 and 3.31, which is what nine archives
    delivering their own derivatives looks like.
    """
    cw, ch = claimed
    aw, ah = actual
    if aw <= 0 or ah <= 0:
        return None
    sw, sh = cw / aw, ch / ah
    if abs(sw - sh) > UNIFORM_TOLERANCE * max(sw, sh):
        return None
    return (sw + sh) / 2


def describe_signature(claimed: tuple[int, int], actual: tuple[int, int]) -> str:
    """What a (claimed, actual) size pair says about the two images.

    Four shapes, told apart by arithmetic rather than by looking at the pages:
    a swap is a rotated scan, a couple of pixels is one renderer against
    another, one scale factor on both axes is a derivative whose polygons would
    survive multiplication, and anything else is a different crop of the page —
    the only one of the four that nothing here can repair.
    """
    cw, ch = claimed
    aw, ah = actual
    if (cw, ch) == (ah, aw):
        return "rotated: the same pixels, width and height exchanged"
    if max(abs(cw - aw), abs(ch - ah)) <= ROUNDING_PX:
        return (f"rounding: {abs(cw - aw)}x{abs(ch - ah)} px apart — one "
                f"renderer against another, not a different scan")
    factor = uniform_scale(claimed, actual)
    if factor is not None:
        return (f"scaled: one factor on both axes ({factor:.3f}) — the polygons "
                f"describe this image after division by it")
    sw, sh = cw / aw, ch / ah
    return (f"different crop: {sw:.3f} wide against {sh:.3f} high, "
            f"aspect {cw / ch:.4f} against {aw / ah:.4f} — two derivatives of "
            f"the page, not one rescaled")


#: Every attribute of a PAGE document that holds pixel coordinates. `Coords` is
#: the region and line outline, `Baseline` the line's reading order — both are
#: `points="x,y x,y …"`, and a rescale that moved one and not the other would
#: crop correctly and sort the lines wrongly, which no column would show.
_POINTS = re.compile(r'(points=")([^"]*)(")')

#: What a rescaled page says about itself, inside the document it rewrites.
#: The export tree is images and XML and nothing else, so a note beside it in a
#: TSV does not reach whoever reads the dataset — and `pagexml-hf` carries the
#: XML into a column verbatim, so a comment here travels with the page.
_SCALE_NOTE = ("<!-- agentic_historian: line coordinates divided by {factor:.6f}; "
               "the ground truth describes {cw}x{ch}, this image is {aw}x{ah}. "
               "imageWidth/imageHeight rewritten to the image. The pixels are "
               "the same scan at two resolutions: both axes agreed on the "
               "factor to within {off:.3f} %. -->")


def rescale_points(points: str, factor: float) -> str:
    """One ``points`` attribute, divided by ``factor`` and rounded to pixels.

    The ground truth's coordinates are in Transkribus's copy of the scan, which
    this factor says is ``factor`` times the size of the image beside it. A
    malformed pair is left alone rather than guessed at: a coordinate this
    cannot parse is one it must not invent.
    """
    out = []
    for pair in points.split():
        x, _, y = pair.partition(",")
        try:
            out.append(f"{round(int(x) / factor)},{round(int(y) / factor)}")
        except ValueError:
            out.append(pair)
    return " ".join(out)


def rescale_xml(xml_text: str, factor: float,
                claimed: tuple[int, int], actual: tuple[int, int]) -> str:
    """The PAGE document with its coordinates in the image's pixels.

    **This rewrites somebody else's data, so it says so in the data.** The
    module refuses to rescale a page whose two sizes disagree about the crop,
    because picking one of two scans is a judgement it has no standing to make.
    A factor both axes agree on to a tenth of a percent is not that judgement:
    it is the same scan at two resolutions, and the measurement is in the
    comment this leaves behind.

    ``imageWidth``/``imageHeight`` are rewritten too. Leaving them would hand
    `pagexml-hf` a document that still contradicts its image — the check this
    export exists for, failing one step further down where nothing runs it.
    """
    cw, ch = claimed
    aw, ah = actual
    off = abs((cw / aw) - (ch / ah)) / max(cw / aw, ch / ah) * 100
    out = _POINTS.sub(lambda m: m.group(1) + rescale_points(m.group(2), factor)
                      + m.group(3), xml_text)
    out = re.sub(r'imageWidth="\d+"', f'imageWidth="{aw}"', out)
    out = re.sub(r'imageHeight="\d+"', f'imageHeight="{ah}"', out)
    note = _SCALE_NOTE.format(factor=factor, cw=cw, ch=ch, aw=aw, ah=ah, off=off)
    # After the declaration, which has to stay first, and before the root.
    m = re.match(r"\s*<\?xml[^>]*\?>", out)
    return (out[:m.end()] + "\n" + note + out[m.end():]) if m else note + "\n" + out


def rejected_table(p: Plan) -> str:
    """Every rejected page, one per row, with its reason and both sizes.

    The plan prints counts and a handful of names, which is a complaint rather
    than a task: diagnosing 43 geometry rejections needs the 43 keys, and the
    next step needs them in a file (#616). Collected in full here, capped only
    where it is displayed.
    """
    rows = ["key\tcategory\tclaimed\tactual\tshape\treason"]
    for r in p.rejected:
        sig = r.signature
        claimed = f"{r.claimed[0]}x{r.claimed[1]}" if r.claimed else ""
        actual = f"{r.actual[0]}x{r.actual[1]}" if r.actual else ""
        shape = describe_signature(*sig).split(":", 1)[0] if sig else ""
        rows.append(f"{r.key}\t{r.category}\t{claimed}\t{actual}\t{shape}\t{r.reason}")
    return "\n".join(rows) + "\n"


def format_plan(p: Plan) -> str:
    """The plan a human reads before anything is uploaded."""
    lines = [f"pages to export: {len(p.entries)}", ""]
    if p.rescaled:
        lines += [f"**{len(p.rescaled)} of them carry rescaled coordinates.** The "
                  f"ground truth describes the same scan at another resolution, "
                  f"one factor on both axes, so the polygons are divided by it "
                  f"and the page says so in its own first comment. Every other "
                  f"size disagreement is still refused:"]
        for e in sorted(p.rescaled, key=lambda e: e.key)[:RESCALED_SHOWN]:
            lines.append(f"  {e.claimed[0]}x{e.claimed[1]} → "
                         f"{e.actual[0]}x{e.actual[1]}  ÷{e.scale:.4f}  {e.key}")
        if len(p.rescaled) > RESCALED_SHOWN:
            lines.append(f"  … and {len(p.rescaled) - RESCALED_SHOWN} more; the "
                         f"factor of every page is in the writers table")
        lines.append("")
    for project, n in sorted(p.by_project.items()):
        lines.append(f"  {project:18} {n:>4}")
    h = p.hands
    lines += ["", "the hand, page by page:",
              f"  {'from the register':26} {h.from_register:>4}"
              f"   (a recorded sender, not an inference)",
              f"  {'from its own dateline':26} {h.decided - h.from_register:>4}",
              f"  {'inherited from its letter':26} {h.inherited:>4}"
              f"   (from {h.letters} letter(s))",
              f"  {'no hand at all':26} {h.alone:>4}"]
    if h.conflicts:
        lines += ["", f"{len(h.conflicts)} letter(s) whose dated pages disagree — "
                      f"nothing inherited in them. Either the folder holds a "
                      f"letter and its reply, or the dateline rule misfired; both "
                      f"are worth looking at:"]
        for name, counts in h.conflicts[:10]:
            lines.append("  - " + name + ": "
                         + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if h.disputed:
        lines += ["", f"**{len(h.disputed)} letter(s) where the register and the "
                      f"dateline disagree.** The register wins — it is a record "
                      f"and the dateline is an inference — but one of the two is "
                      f"wrong about this letter:"]
        for name, recorded, read, place in h.disputed[:10]:
            lines.append(f"  - {name}: register {recorded}, dateline {read} "
                         f"(read {place!r})")
    if h.late:
        lines += ["", f"{len(h.late)} letter(s) were decided by a page that is not "
                      f"their first. A dateline stands on a first page, so this is "
                      f"the shape a misfire has — and the hand was spread over the "
                      f"whole letter: "
                      + ", ".join(h.late[:8])
                      + (" …" if len(h.late) > 8 else "")]
    survey = dateline_survey(p)
    if survey:
        lines += ["", f"{len(survey)} letter(s) nothing dated. Their first "
                      f"{p.survey_lines} line(s), of which the rule reads "
                      f"{DATELINE_LINES} — so the place list can be measured "
                      f"against the text rather than guessed at:"]
        for name, head in survey[:SURVEY_SHOWN]:
            lines.append(f"  {name}: {head or '(no text in the first lines)'}")
        if len(survey) > SURVEY_SHOWN:
            lines.append(f"  … and {len(survey) - SURVEY_SHOWN} more; the rest are "
                         f"in the writers table's `head` column")
    unbestimmt = p.by_project.get("unbestimmt", 0)
    if unbestimmt:
        lines += ["", f"**{unbestimmt} page(s) have no recognisable dateline and no "
                      f"dated page in their letter.** The project split is inferred "
                      f"from the first two lines of a letter's first page; these are "
                      f"not a third hand, they are pages nothing said anything "
                      f"about. Check them before training on the split."]
    if p.rejected:
        lines += ["", f"left out: {len(p.rejected)}"]
        for reason, n in sorted(p.reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:18} {n:>4}")
        sigs = p.signatures
        if sigs:
            lines += ["", f"the geometry rejections are {len(sigs)} distinct size "
                          f"pair(s), and the pair says which of them is repairable:"]
            for (claimed, actual), n in sorted(sigs.items(), key=lambda kv: -kv[1])[:SIGNATURES_SHOWN]:
                lines.append(f"  {n:>4} page(s)  XML {claimed[0]}x{claimed[1]} → "
                             f"image {actual[0]}x{actual[1]}")
                lines.append(f"        {describe_signature(claimed, actual)}")
            if len(sigs) > SIGNATURES_SHOWN:
                lines.append(f"  … and {len(sigs) - SIGNATURES_SHOWN} more pair(s); "
                             f"all of them are in the rejection table")
        lines += ["", "first few:"]
        for r in p.rejected[:REJECTED_SHOWN]:
            lines.append(f"  - {r.key}: {r.reason}")
        if len(p.rejected) > REJECTED_SHOWN:
            lines.append(f"  … and {len(p.rejected) - REJECTED_SHOWN} more. A count "
                         f"is a complaint and only the keys are a task: "
                         f"--rejected-out writes all {len(p.rejected)}")
    return "\n".join(lines)


def writers_table(p: Plan) -> str:
    """Every page's hand, where it came from, and which letter decided it.

    Written out as a TSV because the plan's counts cannot be audited: "154 pages
    inherited a hand" is a number to believe or not, and the only way to disagree
    with it usefully is to read the pages it was wrong about. One row per
    exported page, sorted by letter so a letter's pages stand together.
    """
    rows = ["key\tletter\tbasis\tproject\tsource\tevidence\tscale\thead"]
    for e in sorted(p.entries, key=lambda e: (e.group, e.key)):
        source = ("register" if e.from_register else
                  "inherited" if e.inherited else
                  "dateline" if e.evidence else "none")
        rows.append("\t".join([e.key, e.group or "—", e.basis or "—",
                                e.project, source, e.evidence or "—",
                                f"{e.scale:.6f}" if e.scale else "—",
                                e.head or "—"]))
    return "\n".join(rows) + "\n"


#: The dataset the Laßberg pages go to. Named for the collection and the century,
#: like the fourteen `dh-unibe/image-text_*` datasets `pagexml-hf` already built.
DEFAULT_REPO_ID = "dh-unibe/image-text_lassberg-correspondence_xix"

#: What `pagexml-hf` should do with the XML. `raw_xml` keeps the PAGE document in
#: a column beside the image, which is what the trainer's `hf_source.py` reads.
UPLOAD_MODE = "raw_xml"

#: What a written tree carries to say what it is. A tree is a directory of images
#: and XML, and two things about it cannot be read off the files: which
#: `pagexml-hf` mode it was assembled for, and whether its text was corrected by a
#: human. :mod:`run_export` writes trees for ``--mode text`` whose documents are
#: synthesised around uncorrected machine readings; uploaded in this module's mode
#: they would land in an ``xml_content`` column beside fourteen datasets of real
#: Transkribus output and look like more of the same. The mode is a property of
#: the data, so it travels with the data rather than being retyped on a command
#: line months later.
MANIFEST = "export.json"


def write_manifest(out_dir: Path, *, mode: str, kind: str, **extra) -> Path:
    """Write the tree's manifest. Called by every ``build``, never by a caller."""
    path = Path(out_dir) / MANIFEST
    path.write_text(json.dumps({"mode": mode, "kind": kind,
                                "written_at": datetime.now(timezone.utc)
                                .isoformat(timespec="seconds"), **extra},
                               ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def read_manifest(tree: Path) -> dict:
    """A tree's manifest, or ``{}``. A tree written before manifests existed, or
    by hand, has none — which is not an error and must not read as one."""
    path = Path(tree).expanduser() / MANIFEST
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def record_upload(tree: Path, repo_id: str, *, private: bool) -> None:
    """Note in the tree's manifest that it went to the hub (#711).

    A split, an undo or a rebuild after this point changes what a published
    revision means; `tree_split` reads this record and refuses unless asked.
    A tree without a manifest gets none — nothing was promised about it.
    """
    path = Path(tree) / MANIFEST
    manifest = read_manifest(tree)
    if not manifest:
        return
    manifest.setdefault("uploads", []).append({
        "repo_id": repo_id, "private": private,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def tree_mode(tree: Path, default: str = UPLOAD_MODE) -> str:
    """The `pagexml-hf` mode this tree was assembled for.

    The manifest when there is one, this module's mode when there is not: every
    tree written before 2026-10-22 is ground truth in ``raw_xml``, so the default
    keeps those uploads identical.
    """
    said = str(read_manifest(tree).get("mode") or "").strip()
    return said or default

#: A hub repository id: owner, slash, name. Checked here because `pagexml-hf`
#: would otherwise create `thodel/--private` or fail three minutes into an upload.
_REPO_ID_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")


@dataclass(frozen=True)
class UploadPlan:
    """Everything that has to be true before an upload starts, and whether it is."""

    tree: Path
    repo_id: str
    private: bool
    pages: int
    projects: dict
    tool: Optional[str]
    token: bool
    problems: list
    #: The `pagexml-hf` mode, from the tree's own manifest. Not a caller's
    #: choice: see :data:`MANIFEST`.
    mode: str = UPLOAD_MODE
    #: What kind of text is in the tree — "ground-truth" or "run". Printed, and
    #: the one line of the plan that says whether a human has read this.
    kind: str = ""
    #: Distinct objects per project (#710): the file-name stem before its
    #: ``_NNNN`` page suffix — ``K2911-47_0001`` → ``K2911-47``. A project whose
    #: stems carry no such suffix is absent: there the object is not in the name,
    #: and a count of stems would be a count of pages under another heading.
    objects: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"tree": str(self.tree), "repo_id": self.repo_id,
                "private": self.private, "pages": self.pages,
                "projects": dict(self.projects), "tool": self.tool,
                "token": self.token, "problems": list(self.problems),
                "mode": self.mode, "kind": self.kind, "ok": self.ok}


# ── the dataset card (#691) ──────────────────────────────────────────────────
#
# `pagexml-hf` writes a README on upload whose YAML front matter carries the
# measured `dataset_info` — rows, bytes, features. The card in `docs/` is the
# text a person wrote; its front matter must be the hub's, verbatim, or the
# sizes are typed by hand and wrong from the first re-upload. Twice this was a
# terminal command and a splice done by eye.

CARD_PREFIX = "hf-dataset-card"
_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_PLACEHOLDER_RE = re.compile(r"\A\s*<!--\s*FRONTMATTER.*?-->\s*", re.S)


def card_dir() -> Path:
    """Where the cards live: ``docs/`` beside the package."""
    return Path(config.REPO_ROOT) / "docs"


def resolve_card(name: str) -> Path:
    """A card by **name** under ``docs/``: ``hf-dataset-card*.md``, no path.

    Named rather than pathed for the reason every other handover here is: a
    tool that uploads a named card cannot be told to upload a file.
    """
    name = (name or "").strip()
    if ("/" in name or "\\" in name or not name.startswith(CARD_PREFIX)
            or not name.endswith(".md") or ".." in name):
        raise ValueError(
            f"a card is named, not pathed: {name!r} — one of the "
            f"{CARD_PREFIX}*.md files under docs/")
    path = card_dir() / name
    if not path.is_file():
        have = ", ".join(sorted(p.name for p in card_dir().glob(f"{CARD_PREFIX}*.md"))) or "none"
        raise FileNotFoundError(f"no card {name} under {card_dir()} (have: {have})")
    return path


def split_frontmatter(text: str) -> tuple[Optional[str], str]:
    """``(front matter without its fences, body)``.

    A card may start with the YAML block, with the placeholder comment a card
    carries before its first upload (``<!-- FRONTMATTER … -->``), or with
    neither. The body is what follows, with no leading blank lines.
    """
    m = _FRONTMATTER_RE.match(text)
    if m:
        return m.group(1), text[m.end():].lstrip("\r\n")
    p = _PLACEHOLDER_RE.match(text)
    if p:
        return None, text[p.end():]
    return None, text


def splice_frontmatter(card: str, hub_readme: str) -> str:
    """The card with the hub README's front matter in place of its own.

    The hub's block is the measured one; the card's text is the written one.
    A hub README without front matter is a dataset nobody has uploaded yet,
    and a card spliced from it would carry no sizes — refused.
    """
    fm, _ = split_frontmatter(hub_readme)
    if fm is None:
        raise ValueError("the hub README has no YAML front matter — upload the "
                         "dataset first; its sizes are measured there")
    _, body = split_frontmatter(card)
    return f"---\n{fm}\n---\n\n{body}"


def hub_token() -> str:
    return config.HF_TOKEN or os.environ.get("HUGGINGFACE_HUB_TOKEN", "")


def hub_readme(repo_id: str, token: Optional[str] = None) -> Optional[str]:
    """The README on the hub, or None when the repository has none (or is
    not reachable with this token). Never prints the token."""
    import requests

    url = f"https://huggingface.co/datasets/{repo_id}/resolve/main/README.md"
    headers = {"Authorization": f"Bearer {token or hub_token()}"} if (token or hub_token()) else {}
    try:
        r = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException:
        return None
    return r.text if r.status_code == 200 else None


def hub_state(repo_id: str, token: Optional[str] = None) -> dict:
    """What the hub holds for a dataset: rows, bytes, columns, private.

    Read from the API's ``cardData.dataset_info`` — the block `pagexml-hf`
    wrote — so the numbers are the hub's own. ``{}`` with an ``error`` when the
    hub does not answer; never an exception, because this is the sentence
    printed *after* an upload succeeded.
    """
    import requests

    url = f"https://huggingface.co/api/datasets/{repo_id}"
    tok = token or hub_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return {"error": f"hub answered {r.status_code}"}
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    info = (data.get("cardData") or {}).get("dataset_info") or {}
    if isinstance(info, list):
        info = info[0] if info else {}
    splits = info.get("splits") or []
    return {
        "repo_id": repo_id,
        "private": bool(data.get("private")),
        "rows": sum(int(s.get("num_examples") or 0) for s in splits) or None,
        "bytes": int(info.get("dataset_size") or 0) or None,
        "columns": [f.get("name") for f in (info.get("features") or [])] or None,
        "last_modified": data.get("lastModified"),
        # The commit a training request pins (#715): `revision: null` reads
        # whatever `main` is on the day the trainer fetches.
        "sha": data.get("sha"),
    }


def hub_projects(repo_id: str, token: Optional[str] = None) -> dict:
    """The projects a dataset holds on the hub: ``{"projects": [...]}`` from the
    ``data/<split>/<project>/`` directories, ``{"projects": []}`` for a dataset
    that does not exist yet, ``{"error": …}`` when the hub does not answer.

    Needed because `pagexml-hf` **replaces** a dataset rather than adding to it:
    on 2026-10-24 uploading the Schlussredaktion tree to
    ``image-text_bern-disputation-1528`` removed the Hertwig projects, and the
    first sign was a training request refused with "project not found".
    """
    import requests

    url = f"https://huggingface.co/api/datasets/{repo_id}/tree/main/data"
    tok = token or hub_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    try:
        r = requests.get(url, headers=headers, params={"recursive": "true"}, timeout=30)
        if r.status_code == 404:
            return {"projects": []}
        if r.status_code != 200:
            return {"error": f"hub answered {r.status_code}"}
        entries = r.json()
    except (requests.RequestException, ValueError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    found = set()
    for e in entries if isinstance(entries, list) else []:
        parts = str(e.get("path", "")).split("/")
        if e.get("type") == "directory" and len(parts) == 3 and parts[0] == "data":
            found.add(parts[2])
    return {"projects": sorted(found)}


def projects_lost(hub: list, tree: dict) -> list:
    """Projects on the hub that an upload of ``tree`` would remove."""
    return sorted(set(hub) - set(tree))


def format_hub_state(state: dict) -> str:
    if not state or state.get("error"):
        return f"hub       : not confirmed ({(state or {}).get('error', 'no answer')})"
    size = state.get("bytes")
    gb = f"{size / 1e9:.2f} GB" if size else "size unknown"
    return (f"hub       : {state['repo_id']} — {state.get('rows') or '?'} row(s), "
            f"{gb}, columns {', '.join(state.get('columns') or ['?'])}, "
            f"{'private' if state.get('private') else 'PUBLIC'}"
            + (f"\nrevision  : {state['sha']}" if state.get("sha") else ""))


def upload_card_argv(repo_id: str, card: Path, hf_cli: Optional[str] = None) -> list[str]:
    """The `hf` command line that uploads a card as the dataset's README.
    No token in it: `hf` reads HF_TOKEN from the environment."""
    return [hf_cli or config.HF_CLI, "upload", repo_id, str(card), "README.md",
            "--repo-type", "dataset"]


def upload_card(repo_id: str, card: Path, token: Optional[str] = None) -> str:
    """Put ``card`` on the hub as README.md. Returns how it was done.

    `huggingface_hub` when it is importable from this venv, the `hf` command
    line otherwise (`HF_CLI`, on tei the pagexml-hf venv's). Either way the
    token travels in the environment or an API call, never in argv.
    """
    import shutil
    import subprocess

    tok = token or hub_token()
    try:
        from huggingface_hub import HfApi
    except ImportError:
        HfApi = None  # noqa: N806
    if HfApi is not None:
        HfApi(token=tok or None).upload_file(
            path_or_fileobj=str(card), path_in_repo="README.md",
            repo_id=repo_id, repo_type="dataset",
            commit_message=f"dataset card from {card.name}")
        return "huggingface_hub"
    cli = shutil.which(config.HF_CLI) or (config.HF_CLI if Path(config.HF_CLI).is_file() else None)
    if not cli:
        raise RuntimeError(
            f"neither `huggingface_hub` is importable nor `{config.HF_CLI}` is "
            f"found — set HF_CLI to the hf command (on tei "
            f"/opt/pagexml-hf/bin/hf)")
    env = dict(os.environ)
    if tok:
        env["HF_TOKEN"] = tok
    result = subprocess.run(upload_card_argv(repo_id, card, cli), env=env)
    if result.returncode != 0:
        raise RuntimeError(f"{cli} exited {result.returncode}")
    return cli


def upload_argv(tree: Path, repo_id: str, *, private: bool = True,
                mode: Optional[str] = None) -> list[str]:
    """The `pagexml-hf` command line for one upload.

    `--private` by default and the flag is passed explicitly rather than relied
    on: a default that lives in somebody else's tool is a default that can change
    between versions, and the difference between the two states here is whether a
    collection of unpublished archival images is on the open web.

    ``mode`` defaults to **the tree's own**, not to this module's: a tree of
    machine readings assembled for ``--mode text`` is uploadable by the same
    command and the same MCP tool, which take no mode, because the answer is in
    the directory they are pointed at.
    """
    argv = ["pagexml-hf", str(tree), "--repo-id", repo_id,
            "--mode", mode or tree_mode(tree)]
    if private:
        argv.append("--private")
    return argv


_PAGE_SUFFIX = re.compile(r"^(?P<obj>.+)_\d{3,5}$")


def count_objects(stems) -> Optional[int]:
    """How many objects the page stems of one project belong to, or None.

    The object is the stem before a ``_NNNN`` page suffix (3–5 digits, the
    convention of `oai_gt` and `transkribus_gt_export`). One stem
    without it and the answer is None, not a guess: a mixed project has no
    object count that means anything.
    """
    seen, any_stem = set(), False
    for stem in stems:
        any_stem = True
        m = _PAGE_SUFFIX.match(stem)
        if not m:
            return None
        seen.add(m.group("obj"))
    return len(seen) if any_stem else None


def inspect_upload(tree: Path, repo_id: str = DEFAULT_REPO_ID, *,
                   private: bool = True) -> UploadPlan:
    """Look at everything an upload needs, and report what is missing.

    Every one of these has a failure mode that otherwise surfaces minutes in, from
    inside a tool this repository does not own: an empty directory uploads an
    empty dataset, a repo id of the wrong shape creates a repository nobody meant,
    a missing `pagexml-hf` is a `FileNotFoundError` from `subprocess`, and a
    missing token is a 401 after the first file.

    The token is reported as present or absent and never printed.
    """
    tree = Path(tree).expanduser()
    problems: list[str] = []
    pages, projects, objects = 0, {}, {}
    if not tree.is_dir():
        problems.append(f"no export tree at {tree} — run `export-hf --out` first")
    else:
        import atr_batch as batch

        exts = {e.lower() for e in batch.IMAGE_EXTS}
        for project in sorted(d for d in tree.iterdir() if d.is_dir()):
            xmls = list((project / "page").glob("*.xml"))
            images = [f for f in project.iterdir()
                      if f.is_file() and f.suffix.lower() in exts]
            projects[project.name] = len(xmls)
            pages += len(xmls)
            n_objects = count_objects(x.stem for x in xmls)
            if n_objects is not None:
                objects[project.name] = n_objects
            if len(images) != len(xmls):
                problems.append(
                    f"{project.name}: {len(images)} image(s) against {len(xmls)} "
                    f"XML file(s) — `pagexml-hf` pairs them by stem and would "
                    f"silently drop the odd ones")
        if not pages:
            problems.append(f"{tree} holds no PAGE XML — nothing to upload")
    if not _REPO_ID_RE.match(repo_id or ""):
        problems.append(f"repo id {repo_id!r} is not owner/name")
    tool = shutil.which("pagexml-hf")
    if not tool:
        problems.append(
            "`pagexml-hf` is not on PATH — it owns the parquet layout and this "
            "repository deliberately does not reimplement it "
            "(github.com/The-Flow-Project/pagexml-hf)")
    token = bool(config.HF_TOKEN or os.environ.get("HUGGINGFACE_HUB_TOKEN"))
    if not token:
        problems.append("no Hugging Face token — set HF_TOKEN in the environment "
                        "the upload runs in")
    manifest = read_manifest(tree)
    return UploadPlan(tree=tree, repo_id=repo_id, private=private, pages=pages,
                      projects=projects, tool=tool, token=token,
                      problems=problems, mode=tree_mode(tree),
                      kind=str(manifest.get("kind") or ""), objects=objects)


def format_upload(plan: UploadPlan) -> str:
    """The plan, as a person should read it before a few gigabytes move."""
    lines = [f"tree      : {plan.tree}",
             f"pages     : {plan.pages}",
             "projects  : " + (", ".join(f"{k} {v}" for k, v in
                                          sorted(plan.projects.items())) or "—"),
             *(["objects   : " + ", ".join(f"{k} {v}" for k, v in
                                           sorted(plan.objects.items()))]
               if plan.objects else []),
             f"repo      : {plan.repo_id}",
             f"mode      : {plan.mode}"
             + (f"   (from {MANIFEST})" if plan.kind else
                f"   (no {MANIFEST} in the tree — the default)"),
             f"text      : " + {"run": "MACHINE READING, uncorrected",
                                "ground-truth": "hand-corrected"}
             .get(plan.kind, "unstated"),
             f"visibility: {'private' if plan.private else '** PUBLIC **'}",
             f"pagexml-hf: {plan.tool or 'not found'}",
             f"token     : {'present' if plan.token else 'missing'}"]
    if plan.problems:
        lines += ["", f"{len(plan.problems)} problem(s):"]
        lines += [f"  - {p}" for p in plan.problems]
    return "\n".join(lines)


def build(p: Plan, out_dir: Path) -> dict:
    """Write the export tree. Returns what was written."""
    out_dir = Path(out_dir)
    written = 0
    rescaled = 0
    for e in p.entries:
        project = out_dir / e.project
        (project / "page").mkdir(parents=True, exist_ok=True)
        dest_img = project / f"{e.stem}{e.image.suffix.lower()}"
        shutil.copy2(e.image, dest_img)
        dest_xml = project / "page" / f"{e.stem}.xml"
        if e.scale is not None and e.claimed and e.actual:
            # The one page in the tree whose XML is not a copy. It says so in
            # its own first comment, because the tree is images and XML and
            # nothing a note beside it would reach.
            dest_xml.write_text(
                rescale_xml(e.xml_source.read_text(encoding="utf-8",
                                                   errors="replace"),
                            e.scale, e.claimed, e.actual), encoding="utf-8")
            rescaled += 1
        else:
            shutil.copy2(e.xml_source, dest_xml)
        written += 1
    write_manifest(out_dir, mode=UPLOAD_MODE, kind="ground-truth",
                   pages=written, rescaled=rescaled, projects=p.by_project,
                   left_out=len(p.rejected),
                   note="hand-corrected Transkribus PAGE XML")
    logger.info(f"[hf] wrote {written} page(s) to {out_dir}"
                + (f", {rescaled} with rescaled coordinates" if rescaled else ""))
    return {"out_dir": str(out_dir), "pages": written, "rescaled": rescaled,
            "projects": p.by_project, "left_out": len(p.rejected),
            "mode": UPLOAD_MODE}
