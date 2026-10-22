"""
run_export.py — a batch run's machine readings, in the shape `pagexml-hf` reads.

The sibling of :mod:`hf_export`, and deliberately a different thing. That module
exports **hand-corrected** pages: a page image beside the Transkribus PAGE XML
that a human checked, line polygons and all. This one exports what a recogniser
*said* about pages nobody has transcribed — 281 of the 300 pages of the Laßberg
corpus read by a commercial model in October 2026 — and the difference is not a
detail of the format. It is the only thing a reader of the dataset needs to know.

**Why it goes through `pagexml-hf` at all.** A chat completion has no polygons:
the model is handed an image and returns text, so there is no geometry to export
and no PAGE document to copy. The obvious move is to write a small converter —
image, text, two columns, done — and that is exactly the move this repository
refuses, for the reason :mod:`hf_export` gives at length: `serving-atr-inference`
already carries a list of column aliases because a second converter happened once.
So the readings are given the shape the existing converter takes as input, and the
converter still owns the parquet layout.

That shape is a PAGE document per page, and the question is what to put in it.
`pagexml-hf`'s parser reads a page's text as ``"\\n".join`` over its regions'
``full_text``, and a region's ``full_text`` is its **own** ``TextEquiv/Unicode``
when it has one, falling back to its lines only when it does not. So one
``TextRegion`` holding the whole page text, with a ``Coords`` rectangle around the
page and **no ``TextLine`` elements at all**, is a complete input for
``--mode text``: the ``text`` column comes out as the model wrote it.

Leaving the lines out is a decision, not an economy. Line boxes could be invented
— divide the page into as many horizontal strips as the model returned lines — and
every one of them would be a guess no measurement stands behind. Worse, they would
be *indistinguishable* from the real thing downstream: `--mode line` would crop 31
plausible strips, pair each with a plausible line of text, and hand a trainer a
dataset whose every target is misaligned by an unknown amount.

What the absence actually does, measured against `pagexml-hf` 0.9.6+fork.1 on
2026-10-22 rather than reasoned about: ``--mode text`` returns the page text
exactly as the model wrote it, ``--mode region`` returns one row per page whose
``region_coords`` is the page rectangle — true, if not useful — and ``--mode
line`` **fails**, with a ``ZeroDivisionError`` computing statistics over zero
lines. A crash is a blunter answer than "nothing", and it is the right one: the
question a line-level export asks cannot be answered from this data at all.

**The tree carries its own mode.** ``build`` writes a manifest
(:data:`hf_export.MANIFEST`) naming the export mode beside the pages, and
``upload-hf`` reads it. A tree of raw text uploaded in ``raw_xml`` mode would put
these synthesised documents in an ``xml_content`` column, where they would sit
beside fourteen datasets of real Transkribus output and look like more of the same.
The mode is a property of the data, so it is stored with the data rather than
retyped on a command line months later.

**What is labelled, and where.** ``project_name`` is the **model**, not the hand:
the hand is the axis :mod:`hf_export` infers from datelines, and inferring it here
would mean reading a dateline out of an unverified transcription and inheriting it
along a letter — an inference on top of an inference. The archive and the letter
are in ``filename``, which is the page key. And every document says in its own
first comment what read it, under which settings, on which day, with which verdict
— the same trick the rescale note uses in :func:`hf_export.rescale_xml`, for the
same reason: a tree is images and XML, and nothing a note *beside* them would reach.

**What is left out by default.** :data:`SKIP_VERDICTS` — `page_quality`'s
``empty``, ``repetitive`` and ``deliberation``. An empty answer is not a
transcription of a blank page (the gateway cross-check of 2026-10-19 found one of
19 "empty" pages to be a written page with 24 readable lines), a repetition loop is
the model's defect rendered as text, and a deliberation is the model arguing with
itself in the field where the transcription belongs. Any of the three as a training
target teaches the defect. They are counted, named and refused rather than dropped
quietly, and ``--skip-verdicts ''`` keeps them for someone who wants exactly them.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence
from xml.sax.saxutils import escape

from loguru import logger

import config
import hf_export as hf

#: The PAGE schema every Transkribus export in this project declares, and the one
#: `pagexml-hf` was written against. The parser takes the namespace from the
#: document's own root, so this is a convention rather than a requirement — which
#: is the reason to follow it rather than invent one.
PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"

#: Readings refused by default, by `page_quality` verdict. See the module
#: docstring: each of the three is a defect of the reading, not a reading.
SKIP_VERDICTS = ("empty", "repetitive", "deliberation")

#: The dataset these pages go to. Three things have to be legible in the name
#: itself, because a name is what survives into somebody else's notebook:
#: the collection, the century, and that **nobody has checked this text**.
#: "machine-read" was rejected as one letter away from "machine-readable".
DEFAULT_REPO_ID = "dh-unibe/image-text_lassberg-correspondence_xix_uncorrected-atr"

#: `pagexml-hf`'s exporter for image + page text. The mode this tree is for.
UPLOAD_MODE = "text"

#: Characters XML 1.0 cannot carry at all. A model's output is text from a chat
#: API, not a file this project wrote, so it is not assumed to be clean: a stray
#: 0x0c in one page would make the document unparseable and take the page out of
#: the dataset with a lxml error pointing at a line number.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class RunPage:
    """One page as a run left it on disk: the text, and what produced it."""

    key: str
    model: str
    text: str
    meta: dict = field(default_factory=dict)

    @property
    def verdict(self) -> str:
        return str((self.meta.get("quality") or {}).get("verdict") or "")

    @property
    def truncated(self) -> bool:
        return bool(self.meta.get("truncated"))

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def lines(self) -> int:
        return len([ln for ln in self.text.splitlines() if ln.strip()])


@dataclass
class RunEntry:
    """One page of the export: an image, a synthesised PAGE document, a project."""

    page: RunPage
    image: Path
    project: str
    size: tuple[int, int]
    stem: str = ""

    def __post_init__(self) -> None:
        if not self.stem:
            self.stem = hf._UNSAFE.sub("_", self.page.key).strip("_") or "page"

    @property
    def key(self) -> str:
        return self.page.key


@dataclass
class RunRejected:
    """A page left out, and why. ``category`` groups; ``reason`` is for a human."""

    key: str
    category: str
    reason: str


@dataclass
class RunPlan:
    """What an export of a run would write, before anything is written."""

    entries: list[RunEntry] = field(default_factory=list)
    rejected: list[RunRejected] = field(default_factory=list)
    run: str = ""
    models: list[str] = field(default_factory=list)
    #: Every reading the run held, by verdict — including the refused ones, so
    #: the shape of what was thrown away is visible beside what was kept.
    seen: dict[str, int] = field(default_factory=dict)

    @property
    def by_project(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.project] = out.get(e.project, 0) + 1
        return out

    @property
    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejected:
            out[r.category] = out.get(r.category, 0) + 1
        return out

    def as_dict(self) -> dict:
        return {"run": self.run, "models": list(self.models),
                "pages": len(self.entries), "left_out": len(self.rejected),
                "projects": self.by_project, "refused": self.by_category,
                "verdicts": dict(self.seen)}


# ── reading a run off disk ───────────────────────────────────────────────────

def model_dirs(run_dir: Path, model: Optional[str] = None) -> list[Path]:
    """The model directories of a run, in order. One run holds several.

    A run directory is model-major — ``<run>/<model>/<key>.json`` — because the
    gateway's models are lazy on one GPU and the runner iterates that way. So an
    export of "the run" is an export of one or more models, and which it was
    belongs in the result rather than in the caller's memory.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise NotADirectoryError(f"no run directory at {run_dir}")
    found = [d for d in sorted(run_dir.iterdir())
             if d.is_dir() and any(d.glob("*.json"))]
    if model:
        found = [d for d in found if d.name == model]
        if not found:
            raise FileNotFoundError(
                f"{run_dir.name} holds no model directory {model!r} — it holds "
                + (", ".join(d.name for d in sorted(run_dir.iterdir())
                             if d.is_dir()) or "none"))
    return found


def read_run(run_dir: Path, model: Optional[str] = None) -> list[RunPage]:
    """Every page a run read, with its provenance. The JSON is the record.

    The ``.txt`` beside it holds the same string, but only the JSON says which
    model, which prompt digest, which day and which verdict — and a dataset whose
    rows cannot name what produced them is the thing this module exists to avoid.
    A page whose JSON cannot be parsed is **skipped loudly**: an interrupted run
    is exactly what leaves a half-written file behind, and guessing at its text
    from the ``.txt`` would put an orphaned reading in the dataset with no
    provenance at all.
    """
    pages: list[RunPage] = []
    for d in model_dirs(Path(run_dir), model):
        for jf in sorted(d.glob("*.json")):
            try:
                meta = json.loads(jf.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning(f"[run-hf] {d.name}/{jf.name}: unreadable ({exc}) "
                               f"— page skipped")
                continue
            if not isinstance(meta, dict):
                logger.warning(f"[run-hf] {d.name}/{jf.name}: not a record "
                               f"— page skipped")
                continue
            pages.append(RunPage(key=jf.stem, model=str(meta.get("model") or d.name),
                                 text=str(meta.get("text") or ""), meta=meta))
    logger.info(f"[run-hf] {len(pages)} reading(s) in {Path(run_dir).name}")
    return pages


# ── the synthesised PAGE document ────────────────────────────────────────────

def clean_text(text: str) -> str:
    """The model's text as XML can carry it: one kind of newline, no controls."""
    return _CONTROL.sub("", str(text).replace("\r\n", "\n").replace("\r", "\n")).strip()


def provenance_note(page: RunPage, *, image: Path) -> str:
    """What read this page, as the comment the document opens with.

    Everything here is already in the run's JSON, which stays on the host the run
    happened on. The dataset is images and XML; a reader six months from now has
    the XML. ``--`` is folded, because XML comments cannot contain it and a prompt
    digest or a model name with two hyphens would otherwise produce a document no
    parser accepts.
    """
    src = page.meta.get("source") or {}
    sha = str(src.get("sha256") or "")
    rows = [
        "agentic_historian: MACHINE READING, NOT GROUND TRUTH.",
        "No line geometry: a chat completion returns text, so this document has",
        "one region covering the page and no TextLine elements. The text is as",
        "the model returned it, unverified and uncorrected by any human.",
        "",
        f"model     {page.model}"
        + (f" (engine {page.meta.get('engine')})" if page.meta.get("engine") else ""),
        f"run       {page.meta.get('run') or '?'}",
        f"read      {page.meta.get('recognised_at') or '?'}"
        + (f", gateway {page.meta.get('gateway_version')}"
           if page.meta.get("gateway_version") else ""),
        f"settings  {page.meta.get('settings') or 'not declared'}",
        f"quality   {page.verdict or '?'}, {page.chars} chars, {page.lines} lines"
        + (", TRUNCATED at the output ceiling" if page.truncated else ""),
        f"image     {image.name}"
        + (f", sha256 {sha[:12]} of the {src.get('sha256_of')}" if sha
           else ", not hashed"),
    ]
    note = "\n     ".join(rows)
    return note.replace("--", "- -").replace(">", "&gt;")


def page_xml(page: RunPage, *, image: Path, size: tuple[int, int]) -> str:
    """One PAGE document for one machine-read page.

    One ``TextRegion`` over the whole page, its text in the region's own
    ``TextEquiv`` — which is where `pagexml-hf`'s parser looks first — and no
    ``TextLine`` elements, because there is no line geometry and inventing some
    would be indistinguishable downstream from having measured it.
    """
    width, height = int(size[0]), int(size[1])
    rect = f"0,0 {width},0 {width},{height} 0,{height}"
    created = page.meta.get("recognised_at") or ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<!-- {provenance_note(page, image=image)} -->\n"
        f'<PcGts xmlns="{PAGE_NS}">\n'
        "  <Metadata>\n"
        f"    <Creator>{escape(str(page.model))}</Creator>\n"
        f"    <Created>{escape(created)}</Created>\n"
        f"    <LastChange>{escape(created)}</LastChange>\n"
        "    <Comments>machine reading, uncorrected; no line geometry</Comments>\n"
        "  </Metadata>\n"
        f'  <Page imageFilename="{escape(image.name, {chr(34): "&quot;"})}" '
        f'imageWidth="{width}" imageHeight="{height}">\n'
        '    <TextRegion id="r1" type="paragraph">\n'
        f'      <Coords points="{rect}"/>\n'
        f"      <TextEquiv><Unicode>{escape(clean_text(page.text))}"
        "</Unicode></TextEquiv>\n"
        "    </TextRegion>\n"
        "  </Page>\n"
        "</PcGts>\n")


# ── the plan ─────────────────────────────────────────────────────────────────

def project_of(model: str) -> str:
    """The directory a model's pages go to, which `pagexml-hf` reads as the
    project. The model, made filesystem-safe — see the module docstring for why
    it is not the hand."""
    return hf._UNSAFE.sub("-", str(model)).strip("-").lower() or "model"


def plan(pages: Sequence[RunPage], index: dict[str, Path], *,
         skip_verdicts: Iterable[str] = SKIP_VERDICTS,
         skip_truncated: bool = True, run: str = "") -> RunPlan:
    """What an export would write, and what it would refuse.

    Four refusals, each counted: no image for the key, an empty text, a verdict
    in ``skip_verdicts``, and a reading cut off at the model's output ceiling. The
    last one is its own category rather than part of the verdict: a truncated page
    comes back as an ordinary success — a 200, a text, a verdict of ``ok`` — and
    stops mid-sentence, so it is a reading that looks complete and is not.
    """
    skip = {str(v).strip().lower() for v in skip_verdicts if str(v).strip()}
    out = RunPlan(run=run)
    for page in sorted(pages, key=lambda p: (p.model, p.key)):
        if page.model not in out.models:
            out.models.append(page.model)
        verdict = page.verdict or "?"
        out.seen[verdict] = out.seen.get(verdict, 0) + 1
        text = clean_text(page.text)
        if not text:
            out.rejected.append(RunRejected(
                page.key, "empty", "the model returned no text for this page"))
            continue
        if verdict.lower() in skip:
            out.rejected.append(RunRejected(
                page.key, verdict.lower(),
                f"page_quality calls this reading {verdict!r} — a defect of the "
                f"reading, not a reading ({page.chars} chars, {page.lines} lines)"))
            continue
        if skip_truncated and page.truncated:
            out.rejected.append(RunRejected(
                page.key, "truncated",
                f"cut off at the model's output ceiling after {page.chars} chars "
                f"— a success that stops mid-sentence"))
            continue
        image = index.get(page.key)
        if image is None:
            out.rejected.append(RunRejected(
                page.key, "no-image",
                "no image under this key in the page cache or its cold tier"))
            continue
        try:
            size = hf.image_size(image)
        except Exception as exc:  # noqa: BLE001 — an unreadable image is that page's
            out.rejected.append(RunRejected(
                page.key, "bad-image", f"{image.name}: {type(exc).__name__}: {exc}"))
            continue
        out.entries.append(RunEntry(page=page, image=image,
                                    project=project_of(page.model), size=size))
    return out


def format_plan(p: RunPlan) -> str:
    """The plan as a person should read it before a tree is written."""
    rows = [f"run       : {p.run or '?'}",
            f"models    : " + (", ".join(p.models) or "—"),
            f"pages     : {len(p.entries)}",
            "projects  : " + (", ".join(f"{k} {v}" for k, v in
                                        sorted(p.by_project.items())) or "—"),
            "verdicts  : " + (", ".join(f"{k} {v}" for k, v in
                                        sorted(p.seen.items())) or "—"),
            f"left out  : {len(p.rejected)}"]
    if p.rejected:
        rows += ["  " + f"{k}: {v}" for k, v in sorted(p.by_category.items())]
        rows += ["", "refused pages:"]
        rows += [f"  {r.key}  [{r.category}] {r.reason}" for r in p.rejected]
    return "\n".join(rows)


def rejected_table(p: RunPlan) -> str:
    """Every refusal, as TSV. The counts above are a summary; this is the list."""
    rows = ["key\tcategory\treason"]
    rows += [f"{r.key}\t{r.category}\t{r.reason}" for r in p.rejected]
    return "\n".join(rows) + "\n"


# ── writing it ───────────────────────────────────────────────────────────────

def build(p: RunPlan, out_dir: Path) -> dict:
    """Write the export tree, and the manifest that says what it is."""
    out_dir = Path(out_dir)
    written = 0
    for e in p.entries:
        project = out_dir / e.project
        (project / "page").mkdir(parents=True, exist_ok=True)
        dest_img = project / f"{e.stem}{e.image.suffix.lower()}"
        shutil.copy2(e.image, dest_img)
        (project / "page" / f"{e.stem}.xml").write_text(
            page_xml(e.page, image=dest_img, size=e.size), encoding="utf-8")
        written += 1
    hf.write_manifest(out_dir, mode=UPLOAD_MODE, kind="run",
                      run=p.run, models=list(p.models), pages=written,
                      projects=p.by_project, left_out=len(p.rejected),
                      note="machine readings, uncorrected; no line geometry")
    logger.info(f"[run-hf] wrote {written} page(s) to {out_dir}")
    return {"out_dir": str(out_dir), "pages": written,
            "projects": p.by_project, "left_out": len(p.rejected),
            "mode": UPLOAD_MODE}


def default_source() -> Path:
    """Where the working copies of the pages are — the run's own page cache."""
    return hf.default_source()


def default_archive() -> Optional[str]:
    return config.ATR_PAGE_CACHE_ARCHIVE
