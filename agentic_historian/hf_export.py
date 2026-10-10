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

import os
import re
import shutil
from dataclasses import dataclass, field
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

    def __post_init__(self) -> None:
        if not self.stem:
            self.stem = _UNSAFE.sub("_", self.key).strip("_") or "page"


@dataclass
class Rejected:
    """A page left out, and why. Counted, never silent."""

    key: str
    reason: str


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
            head = r.reason.split(":", 1)[0]
            out[head] = out.get(head, 0) + 1
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
                out.rejected.append(Rejected(
                    name, f"geometry: XML says {claimed[0]}x{claimed[1]}, the image "
                          f"is {actual[0]}x{actual[1]} — the line polygons do not "
                          f"describe this image"))
                continue
        # The dateline is read either way: when the register also covers this
        # letter the two can be compared, and a disagreement means one of them is
        # wrong about a specific letter.
        who = writer_of(item.gt.text)
        recorded = register.get(letter_of(key).name) if register else None
        entry = Entry(key=key, image=image, xml_source=item.gt.source,
                      project=who.project, evidence=who.evidence,
                      head=opening(item.gt.text, lines=survey_lines))
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


def format_plan(p: Plan) -> str:
    """The plan a human reads before anything is uploaded."""
    lines = [f"pages to export: {len(p.entries)}", ""]
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
        lines += ["", "first few:"]
        for r in p.rejected[:5]:
            lines.append(f"  - {r.key}: {r.reason}")
    return "\n".join(lines)


def writers_table(p: Plan) -> str:
    """Every page's hand, where it came from, and which letter decided it.

    Written out as a TSV because the plan's counts cannot be audited: "154 pages
    inherited a hand" is a number to believe or not, and the only way to disagree
    with it usefully is to read the pages it was wrong about. One row per
    exported page, sorted by letter so a letter's pages stand together.
    """
    rows = ["key\tletter\tbasis\tproject\tsource\tevidence\thead"]
    for e in sorted(p.entries, key=lambda e: (e.group, e.key)):
        source = ("register" if e.from_register else
                  "inherited" if e.inherited else
                  "dateline" if e.evidence else "none")
        rows.append("\t".join([e.key, e.group or "—", e.basis or "—",
                                e.project, source, e.evidence or "—",
                                e.head or "—"]))
    return "\n".join(rows) + "\n"


#: The dataset the Laßberg pages go to. Named for the collection and the century,
#: like the fourteen `dh-unibe/image-text_*` datasets `pagexml-hf` already built.
DEFAULT_REPO_ID = "dh-unibe/image-text_lassberg-correspondence_xix"

#: What `pagexml-hf` should do with the XML. `raw_xml` keeps the PAGE document in
#: a column beside the image, which is what the trainer's `hf_source.py` reads.
UPLOAD_MODE = "raw_xml"

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

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {"tree": str(self.tree), "repo_id": self.repo_id,
                "private": self.private, "pages": self.pages,
                "projects": dict(self.projects), "tool": self.tool,
                "token": self.token, "problems": list(self.problems),
                "ok": self.ok}


def upload_argv(tree: Path, repo_id: str, *, private: bool = True,
                mode: str = UPLOAD_MODE) -> list[str]:
    """The `pagexml-hf` command line for one upload.

    `--private` by default and the flag is passed explicitly rather than relied
    on: a default that lives in somebody else's tool is a default that can change
    between versions, and the difference between the two states here is whether a
    collection of unpublished archival images is on the open web.
    """
    argv = ["pagexml-hf", str(tree), "--repo-id", repo_id, "--mode", mode]
    if private:
        argv.append("--private")
    return argv


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
    pages, projects = 0, {}
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
    return UploadPlan(tree=tree, repo_id=repo_id, private=private, pages=pages,
                      projects=projects, tool=tool, token=token,
                      problems=problems)


def format_upload(plan: UploadPlan) -> str:
    """The plan, as a person should read it before a few gigabytes move."""
    lines = [f"tree      : {plan.tree}",
             f"pages     : {plan.pages}",
             "projects  : " + (", ".join(f"{k} {v}" for k, v in
                                          sorted(plan.projects.items())) or "—"),
             f"repo      : {plan.repo_id}",
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
    for e in p.entries:
        project = out_dir / e.project
        (project / "page").mkdir(parents=True, exist_ok=True)
        dest_img = project / f"{e.stem}{e.image.suffix.lower()}"
        shutil.copy2(e.image, dest_img)
        shutil.copy2(e.xml_source, project / "page" / f"{e.stem}.xml")
        written += 1
    logger.info(f"[hf] wrote {written} page(s) to {out_dir}")
    return {"out_dir": str(out_dir), "pages": written,
            "projects": p.by_project, "left_out": len(p.rejected)}
