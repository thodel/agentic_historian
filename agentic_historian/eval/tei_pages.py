#!/usr/bin/env python3
"""tei_pages.py — a frozen page set from a TEI edition and its IIIF images (#903).

    git clone --depth 1 https://github.com/Briefverkehr-der-Stadt-St-Gallen/sg-missiven-data
    python -m eval.tei_pages sg-missiven-data --out sets/missiven-1450-1550 \\
        --years 1450 1550 --letters 40 --dry-run
    python -m eval.tei_pages sg-missiven-data --out sets/missiven-1450-1550 \\
        --years 1450 1550 --letters 40

Written for the St. Gallen missives (CC-BY-SA-4.0), the medieval set of the
third-reading measurement: letters dated 1450–1550, which none of the medieval
epoch models (``qwen3vl-medieval-german-v3``, ``kraken-medieval-german-v2``)
was trained on.

**What it writes**, under ``--out``:

* ``pages/<letter>/<facs>`` — the page image from the IIIF server, and
  ``pages/<letter>/<stem>.xml`` — a PAGE file with the page's lines as text.
* ``docs.csv`` — ``page,doc,year``: the selection, the documents the bootstrap
  resamples, and the dates ``third_reading``'s ``years`` filter reads.
* ``selection.json`` — the edition's commit, the date range, which letters and
  why, the image size asked for, and when.

**No coordinates.** An edition says which lines are on a page, not where. The
PAGE files carry text only, so ``third_reading`` measures this set at page
level and says it has no line level — a reference without geometry cannot be
cropped, and kraken cannot be handed lines it has no position for.

**The text is the training pipeline's.** Pages are split at ``<pb facs>``, lines
at ``<lb/>``; ``note``, ``teiHeader``, ``fw``, ``figDesc`` and ``app`` are
commentary about the page and are skipped, their tails kept. This is
``atr_training.tei.page_texts`` in training-atr-models, which built
``dh-unibe/image-text_sg-missiven`` — so a reference here is the text that
dataset holds for the same page.

**The selection.** Letters whose ``correspAction type="sent"`` date falls in
``--years`` (closed), in date order, ``--letters`` of them evenly spaced over
that order — a sample over the whole period rather than the first decade of
it. Every page of a chosen letter is taken. Deterministic: the same edition
commit and arguments give the same set.

**Resumable.** An image already on disk is not fetched again; a failed fetch is
reported and the page left out of ``docs.csv``, so the set is never silently
short of an image it claims to have.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

DEFAULT_IIIF = "https://media.sources-online.org/cantaloupe/iiif/2"
DEFAULT_PREFIX = "sg-missiven!"
#: The long edge ``dh-unibe/image-text_sg-missiven`` was built at: the
#: originals are ~3000 px, and a page model's budget (2048 visual tokens)
#: carries less detail than this already.
DEFAULT_SIZE = "1500,"
#: The training dataset built from the same edition (training-atr-models
#: ``tei_edition_to_hf.py``). A model trained on it has read these letters;
#: ``third_reading`` compares it with each model's ``training_datasets``.
DEFAULT_DATASET = "dh-unibe/image-text_sg-missiven"
IIIF = "{base}/{prefix}{facs}/full/{size}/0/default.jpg"

#: Subtrees whose text is about the page, not on it (atr_training.tei).
SKIP_SUBTREES = frozenset({"note", "teiHeader", "fw", "figDesc", "app"})
_WS = re.compile(r"\s+")


class TeiError(ValueError):
    """A TEI document that cannot be read as pages of transcribed text."""


# ── reading the edition ──────────────────────────────────────────────────────

def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _walk(elem: ET.Element) -> Iterator[tuple[str, Optional[str]]]:
    tag = local_name(elem.tag)
    if tag == "pb":
        yield "pb", elem.get("facs")
    elif tag == "lb":
        yield "lb", None
    if tag in SKIP_SUBTREES:
        return                     # no text, no children; the parent emits the tail
    if elem.text:
        yield "text", elem.text
    for child in elem:
        yield from _walk(child)
        if child.tail:
            yield "text", child.tail


@dataclass(frozen=True)
class TeiPage:
    image: str
    lines: tuple[str, ...]


def page_texts(tei_xml: str) -> list[TeiPage]:
    """Every page of the edition: the image it names and its lines, in order."""
    try:
        root = ET.fromstring(tei_xml)
    except ET.ParseError as exc:
        raise TeiError(f"not parseable as XML: {exc}") from exc
    body = next((e for e in root.iter() if local_name(e.tag) == "body"), None)
    if body is None:
        raise TeiError("no <body> element — is this a TEI document?")
    pages: list[TeiPage] = []
    image: Optional[str] = None
    lines: list[str] = []
    buffer: list[str] = []

    def close_line():
        text = _WS.sub(" ", "".join(buffer)).strip()
        buffer.clear()
        if text:
            lines.append(text)

    def close_page():
        close_line()
        if image and lines:
            pages.append(TeiPage(image=image, lines=tuple(lines)))
        lines.clear()

    for kind, value in _walk(body):
        if kind == "pb":
            close_page()
            image = value
        elif kind == "lb":
            close_line()
        elif value:
            buffer.append(value)
    close_page()
    return pages


@dataclass
class Letter:
    id: str                       # the file's stem, e.g. stasg_missiv_00220
    path: Path
    date: str                     # as the edition has it, e.g. 1450-03-02
    year: int
    pages: list[TeiPage] = field(default_factory=list)


_YEAR = re.compile(r"(\d{4})")


def sent_date(tei_xml: str) -> Optional[str]:
    """The date of ``correspAction type="sent"``: ``@when``, else
    ``@notBefore``, else the element's text. None when the letter has none."""
    root = ET.fromstring(tei_xml)
    for action in root.iter():
        if local_name(action.tag) != "correspAction" or action.get("type") != "sent":
            continue
        for d in action.iter():
            if local_name(d.tag) == "date":
                value = d.get("when") or d.get("notBefore") or (d.text or "").strip()
                if value and _YEAR.search(value):
                    return value
    return None


def read_letters(edition: Path) -> Iterator[Letter]:
    """Every letter of the edition that has a sent date, in file order."""
    for path in sorted((edition / "data").glob("*.xml")):
        text = path.read_text(encoding="utf-8")
        try:
            date = sent_date(text)
        except ET.ParseError:
            continue
        if not date:
            continue
        yield Letter(path.stem, path, date, int(_YEAR.search(date).group(1)))


def select(letters: list[Letter], years: tuple[int, int], n: Optional[int]) -> list[Letter]:
    """The letters in ``years`` (closed), in date order, ``n`` of them evenly
    spaced over that order; all of them without ``n``."""
    inside = sorted((lt for lt in letters if years[0] <= lt.year <= years[1]),
                    key=lambda lt: (lt.date, lt.id))
    if not n or n >= len(inside):
        return inside
    if n == 1:
        return [inside[len(inside) // 2]]
    step = (len(inside) - 1) / (n - 1)
    return [inside[round(i * step)] for i in range(n)]


# ── writing the set ──────────────────────────────────────────────────────────

def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))


def to_pagexml(page: TeiPage) -> str:
    """PAGE with the page's lines as text and no geometry: the edition has none."""
    body = "\n".join(
        f'      <TextLine id="l{i}"><TextEquiv><Unicode>{_escape(line)}</Unicode></TextEquiv></TextLine>'
        for i, line in enumerate(page.lines, start=1))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<PcGts xmlns="http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15">\n'
            f'  <Page imageFilename="{_escape(page.image)}" imageWidth="0" imageHeight="0">\n'
            '    <TextRegion id="r1">\n'
            f"{body}\n"
            "    </TextRegion>\n"
            "  </Page>\n"
            "</PcGts>\n")


def iiif_url(facs: str, base: str = DEFAULT_IIIF, prefix: str = DEFAULT_PREFIX,
             size: str = DEFAULT_SIZE) -> str:
    return IIIF.format(base=base.rstrip("/"), prefix=prefix, size=size,
                       facs=urllib.parse.quote(facs))


def http_fetch(url: str, tries: int = 4, timeout: float = 60.0) -> bytes:
    last: Optional[Exception] = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agentic-historian-eval"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read()
            if not data:
                raise ValueError("empty response")
            return data
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{url}: {last}")


def _commit(edition: Path) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(edition), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def build(edition: Path, out: Path, years: tuple[int, int], letters: Optional[int],
          *, size: str = DEFAULT_SIZE, base: str = DEFAULT_IIIF, prefix: str = DEFAULT_PREFIX,
          fetch: Callable[[str], bytes] = http_fetch, dry_run: bool = False,
          log: Callable[[str], None] = print,
          source_datasets: tuple[str, ...] = (DEFAULT_DATASET,)) -> dict:
    every = list(read_letters(edition))
    chosen = select(every, years, letters)
    for lt in chosen:
        lt.pages = page_texts(lt.path.read_text(encoding="utf-8"))
    n_pages = sum(len(lt.pages) for lt in chosen)
    in_range = sum(1 for lt in every if years[0] <= lt.year <= years[1])
    log(f"{len(every)} dated letters; {in_range} in {years[0]}–{years[1]}; "
        f"{len(chosen)} chosen, {n_pages} pages")
    if chosen:
        log(f"dates {chosen[0].date} … {chosen[-1].date}")
    if dry_run or not chosen:
        return {"letters": len(chosen), "pages": n_pages, "written": 0, "failed": []}

    pages_dir = out / "pages"
    rows, failed = [], []
    for lt in chosen:
        d = pages_dir / lt.id
        d.mkdir(parents=True, exist_ok=True)
        for page in lt.pages:
            img = d / Path(page.image).name
            if not img.is_file():
                try:
                    data = fetch(iiif_url(page.image, base, prefix, size))
                except Exception as exc:  # noqa: BLE001 — named, the page left out
                    failed.append({"letter": lt.id, "image": page.image, "error": str(exc)})
                    log(f"  {lt.id} {page.image}: {exc}")
                    continue
                tmp = img.with_name(img.name + ".part")
                tmp.write_bytes(data)
                tmp.replace(img)
            xml = d / (Path(page.image).stem + ".xml")
            xml.write_text(to_pagexml(page), encoding="utf-8")
            rows.append({"page": xml.relative_to(pages_dir).as_posix(), "doc": lt.id, "year": lt.year})
    with open(out / "docs.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["page", "doc", "year"])
        w.writeheader()
        w.writerows(rows)
    record = {
        "edition": str(edition), "commit": _commit(edition), "years": list(years),
        "letters_asked": letters, "letters_in_range": in_range,
        "letters": [{"id": lt.id, "date": lt.date, "pages": len(lt.pages)} for lt in chosen],
        "pages_written": len(rows), "failed": failed,
        "iiif": {"base": base, "prefix": prefix, "size": size},
        "source_datasets": list(source_datasets),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licence": "TEI: CC-BY-SA-4.0 (the edition); the images carry no statement — "
                   "this set is for measuring, not for publishing.",
    }
    (out / "selection.json").write_text(json.dumps(record, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    log(f"{len(rows)} pages written to {pages_dir}" + (f", {len(failed)} failed" if failed else ""))
    return {"letters": len(chosen), "pages": n_pages, "written": len(rows), "failed": failed}


def build_parser() -> argparse.ArgumentParser:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(description=doc.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=doc.split("\n\n", 1)[1])
    ap.add_argument("edition", type=Path, help="a clone of the TEI edition (with data/*.xml)")
    ap.add_argument("--out", type=Path, required=True, help="the set's directory")
    ap.add_argument("--years", type=int, nargs=2, metavar=("FROM", "TO"), required=True)
    ap.add_argument("--letters", type=int, default=None,
                    help="letters to take, evenly spaced over the date order (default: all)")
    ap.add_argument("--size", default=DEFAULT_SIZE, help=f"IIIF size (default {DEFAULT_SIZE!r})")
    ap.add_argument("--iiif", default=DEFAULT_IIIF)
    ap.add_argument("--prefix", default=DEFAULT_PREFIX)
    ap.add_argument("--dry-run", action="store_true", help="say what would be taken; fetch nothing")
    ap.add_argument("--source-dataset", action="append", default=None,
                    help=f"training dataset built from this edition, repeatable (default {DEFAULT_DATASET}); "
                         "recorded so third_reading can refuse a model trained on it")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.years[0] > args.years[1]:
        print("tei_pages: --years FROM must not be after TO", file=sys.stderr)
        return 2
    if not (args.edition / "data").is_dir():
        print(f"tei_pages: {args.edition}/data is not a directory — pass a clone of the edition",
              file=sys.stderr)
        return 2
    res = build(args.edition, args.out, tuple(args.years), args.letters, size=args.size,
                base=args.iiif, prefix=args.prefix, dry_run=args.dry_run,
                source_datasets=tuple(args.source_dataset or (DEFAULT_DATASET,)))
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
