#!/usr/bin/env python3
"""Build the `pagexml-hf` tree for `lassberg-blb-gt` from the BLB's Laßberg set.

The Badische Landesbibliothek publishes its Laßberg autographs — images,
metadata and ALTO full text — under Public Domain. The full text is
character-identical to this project's hand-corrected Transkribus pages where
both exist (0.0 % CER on five of six pages, 2.8 % on the sixth, 2026-10-23), so
it is ground truth, not a reading. This script turns the set into the tree the
first dataset was built from — one image plus one PAGE XML per page, split into
projects — so `upload-hf` can hand it to `pagexml-hf` unchanged.

What differs from the first dataset, and is better: the **hand** is read off
the BLB's own title ("Brief von Joseph von Laßberg an …" / "… an Joseph von
Laßberg"), not inferred from a dateline. What differs and is weaker: the PAGE
geometry is **converted from ALTO line boxes**. Lines are rectangles, baselines
are each box's lower edge — adequate for line-level cropping, not a measured
baseline. Every PAGE file says so in its first comment.

Reads the OAI cache `blb_lassberg_inventory.py` wrote; downloads from the BLB
(which answers tei, not datacentre addresses); resumable by file presence.

    cd /home/dh/agentic_historian/agentic_historian
    ../.venv/bin/python3 ../scripts/blb_gt_dataset.py --oai-cache /tmp/blb \\
        --out /mnt/wbkolleg_dh_1/tei/datasets/lassberg-blb-gt --dry-run
    ../.venv/bin/python3 ../scripts/blb_gt_dataset.py ... --limit 2      # smoke
    ../.venv/bin/python3 ../scripts/blb_gt_dataset.py ...                # all
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "mets": "http://www.loc.gov/METS/",
    "mods": "http://www.loc.gov/mods/v3",
    "xlink": "http://www.w3.org/1999/xlink",
}
PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"
UA = "agentic_historian/blb-gt-dataset (research; tei.dh.unibe.ch)"
IMAGE_GROUPS = ("MAX", "DEFAULT")      # first that exists wins; --image-group overrides
LASSBERG = re.compile(r"^Brief(?:gedicht)? von Joseph von La(?:ß|ss)berg\b", re.I)
TO_LASSBERG = re.compile(r"\ban Joseph von La(?:ß|ss)berg\b", re.I)
BY_OTHER = re.compile(r"^Brief von (?!Joseph von La(?:ß|ss)berg)", re.I)


# ── fetching ─────────────────────────────────────────────────────────────────

def fetch(url: str, *, retries: int = 4, timeout: int = 180) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"{url}: {last}")


def head_size(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


# ── the set, from the cached METS ────────────────────────────────────────────

def norm_shelf(s: str) -> str:
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", s.strip()))


def text(el, path: str) -> str:
    e = el.find(path, NS)
    return (e.text or "").strip() if e is not None and e.text else ""


def objects(oai_cache: Path, image_group: str | None) -> list[dict]:
    """One entry per digitised object with pages: shelfmark, title, hand, and
    per page the image and ALTO hrefs. Anchor records without files are
    dropped; where several records share a shelfmark the one with pages wins."""
    best: dict[str, dict] = {}
    for f in sorted(glob.glob(str(oai_cache / "oai_mets_*.xml"))):
        root = ET.parse(f).getroot()
        for rec in root.findall(".//oai:record", NS):
            ident = text(rec, "oai:header/oai:identifier")
            mets = rec.find(".//mets:mets", NS)
            if mets is None:
                continue
            mods = mets.find(".//mods:mods", NS)
            if mods is None:
                continue
            title = text(mods, "mods:titleInfo/mods:title")
            shelf = [norm_shelf(e.text) for e in mods.findall(".//mods:shelfLocator", NS) if e.text]
            date = text(mods, "mods:originInfo/mods:dateCreated") or text(mods, "mods:originInfo/mods:dateIssued")
            m = re.search(r"[:/](\d+)$", ident)
            vl_id = m.group(1) if m else ident
            groups: dict[str, dict[str, str]] = {}
            for grp in mets.findall(".//mets:fileSec/mets:fileGrp", NS):
                use = (grp.get("USE") or "").upper()
                files = {}
                for fl in grp.findall("mets:file", NS):
                    loc = fl.find("mets:FLocat", NS)
                    href = loc.get(f"{{{NS['xlink']}}}href") if loc is not None else None
                    if fl.get("ID") and href:
                        files[fl.get("ID")] = href
                groups[use] = files
            img_group = image_group or next((g for g in IMAGE_GROUPS if groups.get(g)), None)
            ft_group = next((g for g in ("FULLTEXT", "ALTO") if groups.get(g)), None)
            divs = [d for d in mets.findall(".//mets:structMap[@TYPE='PHYSICAL']//mets:div", NS)
                    if d.find("mets:fptr", NS) is not None]
            ordered = sorted(enumerate(divs), key=lambda t: (int(t[1].get("ORDER") or 0), t[0]))
            pages = []
            for n, (_, d) in enumerate(ordered, 1):
                ids = [fp.get("FILEID") for fp in d.findall("mets:fptr", NS)]
                img = next((groups[img_group][i] for i in ids if img_group and i in groups[img_group]), "")
                alto = next((groups[ft_group][i] for i in ids if ft_group and i in groups[ft_group]), "")
                pages.append({"n": n, "order": d.get("ORDER") or str(n), "image": img, "alto": alto})
            if not pages or not any(p["image"] for p in pages):
                continue
            hand = ("lassberg" if LASSBERG.search(title) else
                    "korrespondenten" if TO_LASSBERG.search(title) and BY_OTHER.search(title) else
                    "unbestimmt")
            entry = {"vl_id": vl_id, "shelfmark": shelf[0] if shelf else f"vl{vl_id}",
                     "title": title, "date": date, "hand": hand, "image_group": img_group,
                     "pages": pages}
            key = entry["shelfmark"]
            if key not in best or len(pages) > len(best[key]["pages"]):
                best[key] = entry
    return sorted(best.values(), key=lambda e: [int(x) if x.isdigit() else x
                                                for x in re.findall(r"\d+|\D+", e["shelfmark"])])


def stem_of(shelfmark: str, n: int) -> str:
    """``K 2911,65-67`` page 3 → ``K2911-65-67_0003``: no spaces, no commas,
    the shelfmark still readable, `pagexml-hf` pairs image and XML by it."""
    s = shelfmark.replace(" ", "").replace(",", "-")
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return f"{s}_{n:04d}"


# ── ALTO → PAGE ──────────────────────────────────────────────────────────────

def _alto_ns(root) -> str:
    m = re.match(r"\{([^}]+)\}", root.tag)
    return m.group(1) if m else ""


def alto_to_page(alto_bytes: bytes, *, image_name: str, image_size: tuple[int, int],
                 provenance: dict) -> tuple[str, int, int]:
    """A PAGE 2013 document from an ALTO file. Returns (xml, lines, chars).

    Coordinates: ALTO measures in ``MeasurementUnit`` — ``pixel`` or
    ``mm10`` — on a Page of ``WIDTH``×``HEIGHT``; both are scaled onto the
    actual image size, so a page scanned at one resolution and read at another
    still lands on the ink. Lines are the ALTO line boxes as rectangles;
    baselines are the lower edge of each box, which is stated in the comment.
    """
    root = ET.fromstring(alto_bytes)
    ns = _alto_ns(root)
    q = (lambda tag: f"{{{ns}}}{tag}") if ns else (lambda tag: tag)
    page = root.find(f".//{q('Page')}")
    W, H = image_size
    aw = float(page.get("WIDTH") or 0) if page is not None else 0.0
    ah = float(page.get("HEIGHT") or 0) if page is not None else 0.0
    sx = W / aw if aw else 1.0
    sy = H / ah if ah else 1.0

    def box(el):
        x = float(el.get("HPOS") or 0) * sx
        y = float(el.get("VPOS") or 0) * sy
        w = float(el.get("WIDTH") or 0) * sx
        h = float(el.get("HEIGHT") or 0) * sy
        x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
        x1, y1 = min(W, int(round(x + w))), min(H, int(round(y + h)))
        if x1 <= x0:
            x1 = min(W, x0 + 1)
        if y1 <= y0:
            y1 = min(H, y0 + 1)
        return x0, y0, x1, y1

    regions = []
    n_lines = n_chars = 0
    blocks = list(root.iter(q("TextBlock"))) or [root]
    for bi, blk in enumerate(blocks, 1):
        lines = []
        for li, tl in enumerate(blk.iter(q("TextLine")), 1):
            words = []
            for el in tl:
                tag = el.tag.split("}")[-1]
                if tag == "String" and el.get("CONTENT") is not None:
                    words.append(el.get("CONTENT"))
                elif tag == "HYP":
                    words.append(el.get("CONTENT") or "-")
            ltxt = " ".join(words).strip()
            if not ltxt:
                continue
            x0, y0, x1, y1 = box(tl)
            lines.append((f"r{bi}l{li}", (x0, y0, x1, y1), ltxt))
            n_lines += 1
            n_chars += len(ltxt)
        if not lines:
            continue
        if blk is root:
            bx = (min(l[1][0] for l in lines), min(l[1][1] for l in lines),
                  max(l[1][2] for l in lines), max(l[1][3] for l in lines))
        else:
            bx = box(blk)
        regions.append((f"r{bi}", bx, lines))

    def pts(b):
        x0, y0, x1, y1 = b
        return f"{x0},{y0} {x1},{y0} {x1},{y1} {x0},{y1}"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    comment = ("GROUND TRUTH, converted from the Badische Landesbibliothek's ALTO full text "
               "(Public Domain). " + "; ".join(f"{k}: {v}" for k, v in provenance.items())
               + ". Geometry: ALTO line boxes as rectangles, scaled onto the image; "
                 "baselines are each box's lower edge, not measured.")
    out = [f'<?xml version="1.0" encoding="UTF-8"?>',
           f"<!-- {escape(comment)} -->",
           f'<PcGts xmlns="{PAGE_NS}">',
           "  <Metadata>",
           "    <Creator>agentic_historian scripts/blb_gt_dataset.py (ALTO→PAGE)</Creator>",
           f"    <Created>{now}</Created>", f"    <LastChange>{now}</LastChange>",
           f"    <Comments>{escape(json.dumps(provenance, ensure_ascii=False))}</Comments>",
           "  </Metadata>",
           f'  <Page imageFilename="{escape(image_name, {chr(34): "&quot;"})}" imageWidth="{W}" imageHeight="{H}">']
    for rid, rb, lines in regions:
        out.append(f'    <TextRegion id="{rid}" type="paragraph">')
        out.append(f'      <Coords points="{pts(rb)}"/>')
        for lid, lb, ltxt in lines:
            x0, y0, x1, y1 = lb
            out.append(f'      <TextLine id="{lid}">')
            out.append(f'        <Coords points="{pts(lb)}"/>')
            out.append(f'        <Baseline points="{x0},{y1} {x1},{y1}"/>')
            out.append(f"        <TextEquiv><Unicode>{escape(ltxt)}</Unicode></TextEquiv>")
            out.append("      </TextLine>")
        out.append("      <TextEquiv><Unicode>" + escape("\n".join(l[2] for l in lines)) + "</Unicode></TextEquiv>")
        out.append("    </TextRegion>")
    out += ["  </Page>", "</PcGts>", ""]
    return "\n".join(out), n_lines, n_chars


def image_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# ── building ─────────────────────────────────────────────────────────────────

def build_page(obj: dict, pg: dict, out: Path, *, dry_run: bool) -> dict:
    stem = stem_of(obj["shelfmark"], pg["n"])
    proj = out / obj["hand"]
    ext = Path(pg["image"].split("?")[0]).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        ext = ".jpg"
    img_path = proj / f"{stem}{ext}"
    xml_path = proj / "page" / f"{stem}.xml"
    if img_path.exists() and xml_path.exists():
        return {"stem": stem, "status": "present"}
    if dry_run:
        return {"stem": stem, "status": "planned"}
    try:
        img = fetch(pg["image"])
        alto = fetch(pg["alto"])
    except RuntimeError as exc:
        return {"stem": stem, "status": "failed", "error": str(exc)}
    try:
        size = image_size(img)
    except Exception as exc:  # noqa: BLE001 — a bad image is one page's failure
        return {"stem": stem, "status": "failed", "error": f"image unreadable: {exc}"}
    prov = {"shelfmark": obj["shelfmark"], "title": obj["title"], "date": obj["date"],
            "hand": obj["hand"], "vl_id": obj["vl_id"], "page": pg["order"],
            "image": pg["image"], "alto": pg["alto"], "image_group": obj["image_group"]}
    xml, n_lines, n_chars = alto_to_page(alto, image_name=img_path.name, image_size=size, provenance=prov)
    if n_lines == 0:
        return {"stem": stem, "status": "no-text"}
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = img_path.with_name(img_path.name + ".part")
    tmp.write_bytes(img)
    os.replace(tmp, img_path)
    tmpx = xml_path.with_name(xml_path.name + ".part")
    tmpx.write_text(xml, encoding="utf-8")
    os.replace(tmpx, xml_path)
    return {"stem": stem, "status": "written", "lines": n_lines, "chars": n_chars,
            "bytes": len(img), "size": size}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--oai-cache", default="/tmp/blb")
    ap.add_argument("--out", required=True, help="the export tree to write")
    ap.add_argument("--image-group", default=None, help="METS file group for images (default: MAX, else DEFAULT)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N objects (smoke run)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="plan, sample image sizes, write nothing")
    args = ap.parse_args()

    objs = objects(Path(args.oai_cache), args.image_group)
    with_text = [o for o in objs if any(p["alto"] for p in o["pages"])]
    if args.limit:
        with_text = with_text[:args.limit]
    pages = [(o, p) for o in with_text for p in o["pages"] if p["image"] and p["alto"]]
    skipped_no_alto = sum(1 for o in with_text for p in o["pages"] if p["image"] and not p["alto"])
    hands = {}
    for o in with_text:
        hands[o["hand"]] = hands.get(o["hand"], 0) + sum(1 for p in o["pages"] if p["alto"])
    print(f"objects with pages: {len(objs)}; with full text: {len(with_text)}"
          + (f" (limited to {args.limit})" if args.limit else ""))
    print(f"pages to build: {len(pages)}  (pages without full text left out: {skipped_no_alto})")
    print("by hand (project): " + ", ".join(f"{k} {v}" for k, v in sorted(hands.items())))
    print(f"image group: {with_text[0]['image_group'] if with_text else '-'}   out: {args.out}")

    if args.dry_run:
        sample = pages[:: max(1, len(pages) // 12)][:12]
        sizes = [s for s in (head_size(p["image"]) for _, p in sample) if s]
        if sizes:
            avg = sum(sizes) / len(sizes)
            print(f"image size, sampled over {len(sizes)} page(s): {avg / 1e6:.1f} MB each → "
                  f"about {avg * len(pages) / 1e9:.1f} GB for the tree")
        for o in with_text[:5]:
            print(f"  {o['shelfmark']:<14} {len(o['pages']):>4} p  {o['hand']:<16} {o['title'][:60]}")
        print("\n(dry run — nothing written)")
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    counts: dict[str, int] = {}
    failures = []
    total_bytes = 0
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = [ex.submit(build_page, o, p, out, dry_run=False) for o, p in pages]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            r = fut.result()
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            total_bytes += r.get("bytes", 0)
            if r["status"] == "failed":
                failures.append(r)
            if i % 100 == 0 or i == len(pages):
                print(f"  {i}/{len(pages)}  {counts}  {total_bytes / 1e9:.2f} GB  {time.time() - t0:.0f}s")
    manifest = {"mode": "raw_xml", "kind": "ground-truth",
                "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "Badische Landesbibliothek, Digitale Sammlungen, OAI set blbknajvl "
                          "(Joseph von Laßberg); images and ALTO full text, Public Domain",
                "geometry": "ALTO line boxes converted to PAGE rectangles; baselines synthesised "
                            "as each box's lower edge",
                "projects": sorted(hands), "objects": len(with_text), "pages": len(pages),
                "counts": counts}
    (out / "export.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    (out / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    print(f"\ntree: {out}  {counts}  {total_bytes / 1e9:.2f} GB downloaded  "
          f"{(time.time() - t0) / 60:.1f} min")
    if failures:
        print(f"{len(failures)} page(s) failed — see failures.json; re-run to retry (resumable)")
    print("next: python3 -m agentic_historian upload-hf --tree", out,
          "--repo-id dh-unibe/image-text_lassberg-blb-gt --dry-run")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
