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
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger

#: Places Laßberg wrote from. Eppishausen is his house in Thurgau until 1838,
#: Meersburg the castle after it; a letter headed with either is in his hand.
#: Heimenstein and Dagebertsburg are his own names for the same two houses and
#: appear in the datelines of letters he wrote.
LASSBERG_PLACES = ("eppishausen", "eppishaus", "meersburg", "heimenstein",
                   "dagebertsburg", "waldklause")

#: Places his correspondents wrote from. Not an exhaustive list of the
#: correspondence — only the places that appear in a dateline often enough to be
#: worth a rule, and none of which Laßberg wrote from.
CORRESPONDENT_PLACES = ("basel", "zürich", "zurich", "frauenfeld", "st. gallen",
                        "sanct gallen", "schafhausen", "schaffhausen", "paris",
                        "constanz", "konstanz", "lostorf", "arau", "aarau",
                        "weimar", "göttingen", "goettingen", "stuttgart",
                        "ueberlingen", "überlingen")

#: How many lines of a page the dateline may hide in. A letter's place and date
#: are on the first line or two; past that, a place name is as likely to be
#: something the letter talks about as where it was written.
DATELINE_LINES = 2

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Writer:
    """Which hand a page is in, and what said so."""

    project: str                 # "lassberg" | "korrespondenten" | "unbestimmt"
    evidence: str = ""           # the place matched, or "" when nothing did

    @property
    def certain(self) -> bool:
        return self.project != "unbestimmt"


def writer_of(text: str, *, lines: int = DATELINE_LINES) -> Writer:
    """The hand, inferred from the dateline.

    Laßberg's own letters are headed with the house he wrote from; his
    correspondents' with a city. Both sides of a correspondence sit in the same
    archive folder, so the dateline is the only signal in the data — and it is a
    signal about two lines of text, not a record. A page with no recognisable
    place is ``unbestimmt``, which is the honest answer and not a third hand.
    """
    head = "\n".join(text.strip().splitlines()[:lines]).lower()
    for place in LASSBERG_PLACES:
        if place in head:
            return Writer("lassberg", place)
    for place in CORRESPONDENT_PLACES:
        if place in head:
            return Writer("korrespondenten", place)
    return Writer("unbestimmt")


@dataclass
class Entry:
    """One page of the export: an image, its PAGE XML, and the project it joins."""

    key: str                     # the corpus page key
    image: Path                  # the working copy to copy in
    xml_source: Path             # the ground-truth file
    project: str
    evidence: str = ""
    stem: str = ""               # filesystem-safe name shared by jpg and xml

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


def page_index(source_root: Path) -> dict[str, Path]:
    """``{page key: image path}`` for a corpus directory.

    Built by walking the directory and asking the **runner's own** discovery for
    each page's key, rather than by unfolding ``__`` back into separators. The
    fold is lossy — a folder whose own name contains a double underscore would
    unfold into the wrong path — and more importantly a reconstruction here could
    drift from the runner's rule, which would silently pair a ground-truth page
    with a different page's image.
    """
    import atr_batch as batch

    listed = batch.discover_pages(Path(source_root))
    out: dict[str, Path] = {}
    for ref in listed:
        if ref.pdf_page is None:          # a PDF page has no standalone image here
            out[ref.key] = ref.path
    return out


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
         require_geometry: bool = True) -> Plan:
    """What to export, and what to leave out.

    Only **located** pages: a page whose identity is not settled has no image to
    pair with, and pairing it with the best guess would put a different letter's
    scan beside this letter's transcription.

    A page whose XML geometry disagrees with its image is rejected, not rescaled.
    See the module docstring: rescaling is a guess about which of two scans is
    authoritative, and the failure it would paper over is invisible downstream.
    """
    out = Plan()
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
        who = writer_of(item.gt.text)
        seen.add(key)
        out.entries.append(Entry(key=key, image=image, xml_source=item.gt.source,
                                 project=who.project, evidence=who.evidence))
    return out


def format_plan(p: Plan) -> str:
    """The plan a human reads before anything is uploaded."""
    lines = [f"pages to export: {len(p.entries)}", ""]
    for project, n in sorted(p.by_project.items()):
        lines.append(f"  {project:18} {n:>4}")
    unbestimmt = p.by_project.get("unbestimmt", 0)
    if unbestimmt:
        lines += ["", f"**{unbestimmt} page(s) have no recognisable dateline.** The "
                      f"project split is inferred from the first two lines; these "
                      f"are not a third hand, they are pages the rule could not "
                      f"read. Check them before training on the split."]
    if p.rejected:
        lines += ["", f"left out: {len(p.rejected)}"]
        for reason, n in sorted(p.reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:18} {n:>4}")
        lines += ["", "first few:"]
        for r in p.rejected[:5]:
            lines.append(f"  - {r.key}: {r.reason}")
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
