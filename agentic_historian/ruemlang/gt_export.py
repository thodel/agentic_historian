"""gt_export.py — hand-transcribed pages of a Transkribus document, as a
`pagexml-hf` tree to train a kraken model on.

    python agentic_historian/ruemlang/gt_export.py --doc 13922499 --pages 3-60 \\
        --test-pages 8,17,26,39,48,55 --tree hertwig-gt [--dry-run]

writes, under ``VLM_TEST_ROOT/<tree>``, the Transkribus-export shape that
``pagexml-hf`` converts and every ``dh-unibe/image-text_*`` dataset was built
from::

    <tree>/<prefix>_train/p0003.jpg     <tree>/<prefix>_train/page/p0003.xml
    <tree>/<prefix>_test/p0008.jpg      <tree>/<prefix>_test/page/p0008.xml

**Two projects, and only one goes to the trainer.** The trainer's own
``partition`` splits ``_train`` for validation and model selection. ``_test`` is
never named in the request, so its pages are seen by no stage of training — they
are what ``pipeline.py eval`` measures the new model against, beside the three
gateway models, on equal terms.

**What is written is what a human wrote.** The latest transcript of each page is
taken as Transkribus has it (DONE or IN_PROGRESS — the volume's own statuses),
and every ``TextLine`` without text is removed from the copy: a line with polygon
and no transcription would teach the model to answer an empty string. The image
is the one Transkribus serves, so the coordinates hold; its size is checked
against ``imageWidth``/``imageHeight`` all the same, and a page that disagrees
is refused rather than rescaled.

**``--segmentation``** writes the same pages for training a *line segmenter*
(``ketos segtrain``) instead. There the geometry is the ground truth and the text
does not matter: a ``TextLine`` without transcription is still a line on the
page, and removing it would teach the segmenter that it is not there. So every
line is kept, a page needs three lines rather than three transcribed ones, and
the XML lies beside its image (``<project>/p0003.xml``), which is where kraken's
PAGE reader looks for ``imageFilename``. ``scripts/segtrain_kraken.sh`` trains on
the result.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

PKG = Path(__file__).resolve().parent.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from loguru import logger                                  # noqa: E402

import config                                              # noqa: E402
from ruemlang import transkribus_pages as tp               # noqa: E402

MIN_TEXT_LINES = 3


def _pages(spec: Optional[str]) -> set[int]:
    out: set[int] = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def clean_pagexml(xml: str, image_name: str, keep_empty: bool = False) -> tuple[str, dict]:
    """The PAGE XML without empty lines (all lines with ``keep_empty``),
    pointing at ``image_name``.

    Returns the XML and ``{lines, kept, chars, width, height}``. Namespace and
    everything else are kept as Transkribus wrote them.
    """
    root = ET.fromstring(xml)
    ns_uri = root.tag[1:root.tag.index("}")]
    ET.register_namespace("", ns_uri)
    ns = {"p": ns_uri}
    page = root.find("p:Page", ns)
    if page is None:
        raise ValueError("no <Page>")
    page.set("imageFilename", image_name)
    lines = kept = chars = 0
    for region in page.iter(f"{{{ns_uri}}}TextRegion"):
        for tl in list(region.findall("p:TextLine", ns)):
            lines += 1
            uni = tl.find("p:TextEquiv/p:Unicode", ns)
            text = (uni.text or "").strip() if uni is not None else ""
            if not text:
                if not keep_empty:
                    region.remove(tl)
                continue
            kept += 1
            chars += len(text)
    body = ET.tostring(root, encoding="unicode")
    stats = {"lines": lines, "kept": kept, "chars": chars,
             "width": int(page.get("imageWidth") or 0),
             "height": int(page.get("imageHeight") or 0)}
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body, stats


def export(args) -> int:
    root = Path(config.VLM_TEST_ROOT) / args.tree
    if not args.pages and not args.status:
        raise SystemExit("name the pages: --pages 3-60, --status DONE, or both")
    wanted = _pages(args.pages) if args.pages else None
    test = _pages(args.test_pages)
    if wanted is not None and test - wanted:
        raise SystemExit(f"--test-pages outside --pages: {sorted(test - wanted)}")
    statuses = {x.strip().upper() for x in (args.status or "").split(",") if x.strip()}
    ts = tp.TkSession().authenticate()
    in_doc = [p for p in tp.pages_of_document(ts, int(args.collection), int(args.doc))
              if wanted is None or p.page_nr in wanted]
    pages = [p for p in in_doc if not statuses or str(p.status or "").upper() in statuses]
    if statuses:
        print(f"--status {','.join(sorted(statuses))}: {len(pages)} of {len(in_doc)} page(s)")
    if statuses and test - {p.page_nr for p in pages}:
        raise SystemExit(f"--test-pages not {'/'.join(sorted(statuses))}: "
                         f"{sorted(test - {p.page_nr for p in pages})}")
    # Pages the status filter left out are not missing; pages the document lacks are.
    missing = (wanted - {p.page_nr for p in in_doc}) if wanted is not None else set()
    if missing:
        logger.warning(f"pages not in the document: {sorted(missing)}")
    prefix = args.prefix
    report = {"tree": str(root), "doc": int(args.doc), "train": [], "test": [], "refused": []}
    for p in pages:
        project = f"{prefix}_test" if p.page_nr in test else f"{prefix}_train"
        name = f"p{p.page_nr:04d}"
        d = root / project
        xml = tp.download_pagexml(ts, p)
        if not xml:
            report["refused"].append({"page": p.page_nr, "why": "no transcript"})
            continue
        try:
            cleaned, stats = clean_pagexml(xml, f"{name}.jpg", keep_empty=args.segmentation)
        except (ET.ParseError, ValueError) as exc:
            report["refused"].append({"page": p.page_nr, "why": f"PAGE XML: {exc}"})
            continue
        counted = stats["lines"] if args.segmentation else stats["kept"]
        if counted < MIN_TEXT_LINES:
            report["refused"].append({"page": p.page_nr, "why": f"{counted} "
                                      + ("line(s)" if args.segmentation else "transcribed line(s)")})
            continue
        entry = {"page": p.page_nr, "status": p.status, **stats}
        xml_dir = d if args.segmentation else d / "page"
        if not args.dry_run:
            xml_dir.mkdir(parents=True, exist_ok=True)
            img = d / f"{name}.jpg"
            if not img.exists():
                tmp = d / f"{name}.download"
                tp.download_image(ts, p, tmp)
                from PIL import Image
                with Image.open(tmp) as im:
                    size = im.size
                    if stats["width"] and size != (stats["width"], stats["height"]):
                        tmp.unlink()
                        report["refused"].append({
                            "page": p.page_nr,
                            "why": f"image {size} vs XML {stats['width']}x{stats['height']}"})
                        continue
                    if im.format == "JPEG":
                        tmp.replace(img)
                    else:
                        im.convert("RGB").save(img, quality=95)
                        tmp.unlink()
            (xml_dir / f"{name}.xml").write_text(cleaned, encoding="utf-8")
        report["test" if p.page_nr in test else "train"].append(entry)
        logger.info(f"[gt] {name} -> {project}: {stats['kept']}/{stats['lines']} lines, "
                    f"{stats['chars']} chars, {p.status}")
    for k in ("train", "test"):
        rows = report[k]
        lines = sum(r["lines"] for r in rows) if args.segmentation else sum(r["kept"] for r in rows)
        print(f"{prefix}_{k}: {len(rows)} page(s), {lines} line(s), "
              f"{sum(r['chars'] for r in rows)} chars")
    for r in report["refused"]:
        print(f"  refused p{r['page']}: {r['why']}")
    if args.dry_run:
        print("dry run: nothing written")
    else:
        root.mkdir(parents=True, exist_ok=True)
        (root / "export.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        if args.segmentation:
            print(f"tree: {root}  — copy it where the trainer can read it and run "
                  "scripts/segtrain_kraken.sh there (docs/HERTWIG_SEGMENTATION.md)")
        else:
            print(f"tree: {root}  — upload with pagexml-hf (private), e.g. through the "
                  f"ATR MCP's upload_hf(tree={args.tree!r}, repo_id=…)")
    return 0 if report["train"] else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--collection", default="2313234")
    ap.add_argument("--doc", required=True)
    ap.add_argument("--pages", default=None, help="pages with ground truth, e.g. 3-60")
    ap.add_argument("--status", default=None,
                    help="only pages whose current status is one of these, e.g. DONE or DONE,FINAL")
    ap.add_argument("--test-pages", default="", help="held out of training entirely")
    ap.add_argument("--tree", required=True, help="directory name under VLM_TEST_ROOT")
    ap.add_argument("--prefix", default="A_V_1443_Hertwig", help="project name prefix")
    ap.add_argument("--segmentation", action="store_true",
                    help="for ketos segtrain: keep lines without text, XML beside the image")
    ap.add_argument("--dry-run", action="store_true")
    return ap


if __name__ == "__main__":
    raise SystemExit(export(build_parser().parse_args()))
