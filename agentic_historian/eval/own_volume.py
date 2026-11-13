"""Bring one of our own volumes into an e-codices bench (#844, the counter-test).

The second reader (#859) dated e-codices volumes far better than our model —
on descriptions published since 2008, which it may have read in training. The
counter-test is a volume no catalogue describes: one of ours, with a reference
a historian writes. ``eval.ecodices_bench`` already runs and scores such a
volume (``reference.json`` + ``pages/``, #855); this puts one there:

    python -m eval.own_volume runs/own-2026-11 a-v-1443 \\
        --collection 2313234 --doc 13922499 --pages 12 \\
        --date-from 1528 --material papier --script "Kanzleikursive" --hands 1

* **Pages** — ``--pages`` images at even intervals over the document's *written*
  pages (lines or words in the current transcript; every page if none has
  any), from Transkribus, or from a local folder with ``--from-dir``. Scaled to
  at most 1600 px on the long side, as the bench fetches e-codices pages, so
  the two kinds of volume are read at the same resolution. A page already
  there is not fetched again.
* **Reference** — what the historian says, nothing guessed: ``--date-from`` is
  required, everything else is judged only when given. Written as
  ``reference.json`` in the shape ``ecodices_bench.parse_reference`` reads.

Then ``ecodices_bench run``/``score`` on the bench directory, as for e-codices.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

MAX_SIDE = 1600
MATERIAL_WORDS = ("papier", "pergament", "chart", "perg", "mixed", "gemischt")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def reference(date_from: int, date_to: Optional[int] = None, material: Optional[str] = None,
              script: Optional[str] = None, hands: Optional[int] = None,
              note: Optional[str] = None) -> dict:
    """The ``reference.json`` content; refuses what ``parse_reference`` would misread."""
    if not 300 <= int(date_from) <= 2100:
        raise ValueError(f"date_from {date_from} is not a year")
    if date_to is not None and int(date_to) < int(date_from):
        raise ValueError(f"date_to {date_to} lies before date_from {date_from}")
    if material is not None and material.strip().lower() not in MATERIAL_WORDS:
        raise ValueError(f"material {material!r}: one of {', '.join(MATERIAL_WORDS)}")
    if hands is not None and int(hands) < 1:
        raise ValueError("hands must be at least 1")
    ref: dict = {"date_from": int(date_from), "date_to": int(date_to or date_from)}
    if material:
        ref["material"] = material.strip().lower()
    if script:
        ref["script"] = script.strip()
    if hands is not None:
        ref["hands"] = int(hands)
    if note:
        ref["note"] = note
    return ref


def written(pages: Sequence) -> list:
    """The pages with text in their current transcript — or all, if none has any."""
    w = [p for p in pages if (p.nr_lines or 0) > 0 or (p.nr_words or 0) > 0]
    return w or list(pages)


def spread(items: Sequence, k: int) -> list:
    import page_sample

    return [items[i] for i in page_sample.spread(len(items), k)]


def shrink(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """JPEG of ``data`` with its long side at most ``max_side`` px."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        if max(im.size) > max_side:
            im.thumbnail((max_side, max_side))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=90)
        return out.getvalue()


def add(bench_dir: Path, vol_id: str, ref: dict, *, images: Sequence[tuple[str, Callable[[], bytes]]],
        source: dict) -> Path:
    """Write ``<bench>/<vol_id>/`` with ``reference.json``, ``source.json`` and ``pages/``.

    ``images`` are ``(name, fetch)`` pairs in page order; ``fetch`` is called
    only for a page not yet on disk.
    """
    vdir = Path(bench_dir) / vol_id
    if (vdir / "tei.xml").exists():
        raise ValueError(f"{vdir} is an e-codices volume (tei.xml); choose another id")
    pages = vdir / "pages"
    pages.mkdir(parents=True, exist_ok=True)
    for name, fetch in images:
        dest = pages / f"{name}.jpg"
        if dest.exists() and dest.stat().st_size > 0:
            continue
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(shrink(fetch()))
        tmp.replace(dest)
    (vdir / "reference.json").write_text(json.dumps(ref, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    (vdir / "source.json").write_text(json.dumps(source, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")
    return vdir


def _transkribus_images(collection: int, doc: int, k: int) -> tuple[list, dict]:
    from ruemlang import transkribus_pages as tp

    ts = tp.TkSession().authenticate()
    pages = spread(written(tp.pages_of_document(ts, collection, doc)), k)

    def fetch(p) -> Callable[[], bytes]:
        def go() -> bytes:
            r = ts.get(p.image_url)
            if r.status_code != 200:
                raise RuntimeError(f"{p.key}: image {r.status_code}")
            return r.content
        return go

    title = pages[0].doc_title if pages else ""
    return ([(f"p{p.page_nr:04d}", fetch(p)) for p in pages],
            {"transkribus": {"collection": collection, "doc": doc, "title": title,
                             "pages": [p.page_nr for p in pages]}})


def _dir_images(folder: Path, k: int) -> tuple[list, dict]:
    files = sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    chosen = spread(files, k)
    return ([(p.stem, p.read_bytes) for p in chosen],
            {"folder": str(folder), "pages": [p.name for p in chosen]})


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bench_dir", type=Path)
    ap.add_argument("vol_id", help="directory name in the bench, e.g. a-v-1443")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--doc", type=int, help="Transkribus document id")
    src.add_argument("--from-dir", type=Path, help="a folder of page images instead")
    ap.add_argument("--collection", type=int, default=2313234)
    ap.add_argument("--pages", type=int, default=12,
                    help="images at even intervals over the written pages (default 12)")
    ap.add_argument("--date-from", type=int, required=True)
    ap.add_argument("--date-to", type=int)
    ap.add_argument("--material", help="papier | pergament | gemischt (or perg/chart/mixed)")
    ap.add_argument("--script", help="as a historian names it; compared by family")
    ap.add_argument("--hands", type=int)
    ap.add_argument("--note", help="where the reference comes from")
    args = ap.parse_args(argv)

    try:
        ref = reference(args.date_from, args.date_to, args.material, args.script, args.hands,
                        args.note)
    except ValueError as exc:
        ap.error(str(exc))
    if args.from_dir:
        images, source = _dir_images(args.from_dir, args.pages)
    else:
        images, source = _transkribus_images(args.collection, args.doc, args.pages)
    if not images:
        print("no pages found", file=sys.stderr)
        return 1
    vdir = add(args.bench_dir, args.vol_id, ref, images=images, source=source)
    print(f"{vdir}: {len(images)} page(s), reference {json.dumps(ref, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
