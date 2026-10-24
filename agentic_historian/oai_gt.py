"""oai_gt.py — a library's OAI-PMH set of images + ALTO as a ground-truth tree (#712).

What three scripts did by hand on 23/24 October 2026 for the Badische
Landesbibliothek's Laßberg set (OAI set ``blbknajvl``): harvest the set's METS,
take stock of it, and turn every page with an ALTO full text into one image
plus one PAGE XML in a `pagexml-hf` tree — 2,700 pages, 7.75 GB, 10.8 minutes.
Visual Library and Kitodo publish the same METS/ALTO profile, so the code is
not about one library; the **sources** it may read are.

**Sources are names, in code.** :data:`SOURCES` holds the base URL, the set,
the project rule and the tree name of every source this module may fetch from.
A caller names one; it never hands over a URL. A tool on a server on the open
net that fetched whatever it was told would be a general downloader, which is
a different and much larger thing than importing a library's ground truth.
Adding a source is a reviewed change to this file.

Steps, each resumable by file presence:

* ``inventory`` — harvest the set (cached page by page under
  ``<root>/_oai/<source>/``), write ``inventory.tsv``/``.json``: objects,
  pages, whether a full text exists, the file groups.
* ``build`` — ALTO → PAGE 2013 per page, the image beside it, projects by the
  source's rule. Dry run by default. Pages without a single ALTO line are left
  out (1,419 of 4,123 on the BLB set) and counted.

Three things the scripts did not get right, and this does:

* A **rebuild after a split** found an evaluation object's pages missing from
  ``<project>`` and wrote them there again — the same object on both sides.
  Presence is now looked up in ``<project>_eval`` as well, and a new page of an
  object that is held out is written to the eval side.
* A rebuild **overwrote** ``export.json`` and with it the split and the upload
  record. The manifest is now merged, not replaced.
* Requests were unthrottled. Now at most :data:`MAX_RPS` a second across all
  workers, and at most :data:`MAX_WORKERS` workers.
"""

from __future__ import annotations

import collections
import concurrent.futures as cf
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

NS = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "mets": "http://www.loc.gov/METS/",
    "mods": "http://www.loc.gov/mods/v3",
    "xlink": "http://www.w3.org/1999/xlink",
    "dv": "http://dfg-viewer.de/",
}
PAGE_NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"
UA = "agentic_historian/oai-gt-import (research; tei.dh.unibe.ch)"
IMAGE_GROUPS = ("MAX", "DEFAULT")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
EVAL_SUFFIX = "_eval"
MAX_WORKERS = 8
MAX_RPS = 8.0
STEPS = ("inventory", "build")


class OaiGtError(Exception):
    """The import cannot go on; the message says why."""


# ── project rules ────────────────────────────────────────────────────────────

#: The hand, read off the BLB's own title. Five projects, because the set is
#: not only letters: 1550 of its 4305 pages are the printed Fürstenbergischer
#: Landkalender (K 2914), and 1300 more are copies, notes and excerpts — very
#: probably Laßberg's own hand, but the title does not say so, and a label that
#: is not in the record is a guess.
LETTER = re.compile(r"^(?:Brief(?:gedicht)?|Bearbeitung|Korrespondenz)\b", re.I)
LASSBERG = re.compile(r"^(?:Brief(?:gedicht)?|Bearbeitung) von Joseph von La(?:ß|ss)berg\b", re.I)
TO_LASSBERG = re.compile(r"\ban Joseph von La(?:ß|ss)berg\b", re.I)
BY_OTHER = re.compile(r"^(?:Brief|Bearbeitung) von (?!Joseph von La(?:ß|ss)berg)", re.I)
PRINT_SERIES = ("K 2914",)


def lassberg_hand(title: str, shelfmark: str) -> str:
    """``lassberg`` / ``korrespondenten`` / ``unbestimmt`` for letters, by
    title; ``drucke`` for the printed series; ``manuskripte`` for the rest."""
    if LASSBERG.search(title):
        return "lassberg"
    if TO_LASSBERG.search(title) and BY_OTHER.search(title):
        return "korrespondenten"
    if LETTER.search(title):
        return "unbestimmt"
    if any(shelfmark.startswith(s + ",") or shelfmark == s for s in PRINT_SERIES):
        return "drucke"
    return "manuskripte"


RULES: dict[str, Callable[[str, str], str]] = {"lassberg_hand": lassberg_hand}


# ── sources ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Source:
    name: str
    base_url: str
    oai_path: str
    set_spec: str
    tree: str
    rule: str
    description: str
    metadata_prefix: str = "mets"
    image_group: Optional[str] = None

    @property
    def oai_url(self) -> str:
        return self.base_url.rstrip("/") + self.oai_path


SOURCES: dict[str, Source] = {
    "blb-lassberg": Source(
        name="blb-lassberg", base_url="https://digital.blb-karlsruhe.de", oai_path="/oai",
        set_spec="blbknajvl", tree="lassberg-blb-gt", rule="lassberg_hand",
        description="Badische Landesbibliothek, Digitale Sammlungen, OAI set blbknajvl "
                    "(Joseph von Laßberg); images and ALTO full text, Public Domain"),
}


def source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        raise OaiGtError(f"unknown source {name!r} — known: {', '.join(sorted(SOURCES))}. "
                         f"A new source is a change to oai_gt.SOURCES, not an argument") from None


def cache_dir(root: Path, src: Source) -> Path:
    return Path(root) / "_oai" / src.name


# ── fetching, throttled ──────────────────────────────────────────────────────

class _Throttle:
    def __init__(self, rps: float):
        self.gap = 1.0 / rps if rps > 0 else 0.0
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        if not self.gap:
            return
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


_THROTTLE = _Throttle(MAX_RPS)


def fetch(url: str, *, retries: int = 4, timeout: int = 180) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        _THROTTLE.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(min(2 ** attempt, 10))
    raise OaiGtError(f"{url}: {last}")


def head_size(url: str) -> int | None:
    _THROTTLE.wait()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req, timeout=60) as r:
            n = r.headers.get("Content-Length")
            return int(n) if n else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def _same_host(src: Source, url: str) -> bool:
    """A METS file may point anywhere; this fetches only from the source's host."""
    return urllib.parse.urlsplit(url).netloc == urllib.parse.urlsplit(src.base_url).netloc


# ── harvest and inventory ────────────────────────────────────────────────────

def harvest(src: Source, cache: Path, *, get=fetch) -> list[Path]:
    """All OAI pages of the set as files; resumable by presence."""
    cache.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    token: str | None = None
    n = 0
    while True:
        path = cache / f"oai_{src.metadata_prefix}_{n:03d}.xml"
        if path.exists():
            data = path.read_bytes()
        else:
            q = ({"verb": "ListRecords", "resumptionToken": token} if token
                 else {"verb": "ListRecords", "metadataPrefix": src.metadata_prefix,
                       "set": src.set_spec})
            data = get(f"{src.oai_url}?{urllib.parse.urlencode(q)}")
            path.write_bytes(data)
        pages.append(path)
        root = ET.fromstring(data)
        err = root.find("oai:error", NS)
        if err is not None:
            raise OaiGtError(f"OAI error on page {n}: {err.get('code')}: {err.text}")
        tok = root.find(".//oai:resumptionToken", NS)
        token = (tok.text or "").strip() if tok is not None else ""
        if not token:
            break
        n += 1
        if n > 2000:
            raise OaiGtError("more than 2000 OAI pages — a resumption token loops")
    return pages


def _text(el, path: str) -> str:
    if el is None:
        return ""
    e = el.find(path, NS)
    return (e.text or "").strip() if e is not None and e.text else ""


def norm_shelf(s: str) -> str:
    """``K 2911, 47`` / ``K 2911,47 `` → ``K 2911,47``; keeps sub-numbers."""
    return re.sub(r"\s*,\s*", ",", re.sub(r"\s+", " ", s.strip()))


def _natural(s: str):
    return [int(x) if x.isdigit() else x for x in re.findall(r"\d+|\D+", s)]


def _groups(mets) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    for grp in mets.findall(".//mets:fileSec/mets:fileGrp", NS):
        files = {}
        for fl in grp.findall("mets:file", NS):
            loc = fl.find("mets:FLocat", NS)
            href = loc.get(f"{{{NS['xlink']}}}href") if loc is not None else None
            if fl.get("ID") and href:
                files[fl.get("ID")] = href
        groups[(grp.get("USE") or "").upper()] = files
    return groups


def objects(cache: Path, src: Source, *, image_group: Optional[str] = None) -> list[dict]:
    """One entry per digitised object with pages: shelfmark, title, project,
    and per page the image and ALTO hrefs. Anchor records without files are
    dropped; where several records share a shelfmark the one with pages wins
    (23.10.: anchors share shelfmarks with the real objects, and the first
    record read used to win)."""
    rule = RULES[src.rule]
    best: dict[str, dict] = {}
    for f in sorted(cache.glob(f"oai_{src.metadata_prefix}_*.xml")):
        root = ET.parse(f).getroot()
        for rec in root.findall(".//oai:record", NS):
            header = rec.find("oai:header", NS)
            if header is not None and header.get("status") == "deleted":
                continue
            ident = _text(rec, "oai:header/oai:identifier")
            mets = rec.find(".//mets:mets", NS)
            mods = mets.find(".//mods:mods", NS) if mets is not None else None
            if mods is None:
                continue
            title = _text(mods, "mods:titleInfo/mods:title")
            shelf = [norm_shelf(e.text) for e in mods.findall(".//mods:shelfLocator", NS) if e.text]
            date = (_text(mods, "mods:originInfo/mods:dateCreated")
                    or _text(mods, "mods:originInfo/mods:dateIssued"))
            m = re.search(r"[:/](\d+)$", ident)
            vl_id = m.group(1) if m else ident
            groups = _groups(mets)
            img_group = (image_group or src.image_group
                         or next((g for g in IMAGE_GROUPS if groups.get(g)), None))
            ft_group = next((g for g in ("FULLTEXT", "ALTO") if groups.get(g)), None)
            divs = [d for d in mets.findall(".//mets:structMap[@TYPE='PHYSICAL']//mets:div", NS)
                    if d.find("mets:fptr", NS) is not None]
            ordered = sorted(enumerate(divs), key=lambda t: (int(t[1].get("ORDER") or 0), t[0]))
            pages = []
            for n, (_, d) in enumerate(ordered, 1):
                ids = [fp.get("FILEID") for fp in d.findall("mets:fptr", NS)]
                img = next((groups[img_group][i] for i in ids
                            if img_group and i in groups.get(img_group, {})), "")
                alto = next((groups[ft_group][i] for i in ids
                             if ft_group and i in groups.get(ft_group, {})), "")
                pages.append({"n": n, "order": d.get("ORDER") or str(n), "image": img, "alto": alto})
            if not pages or not any(p["image"] for p in pages):
                continue
            shelfmark = shelf[0] if shelf else f"vl{vl_id}"
            genre = "; ".join(e.text.strip() for e in mods.findall("mods:genre", NS) if e.text)
            entry = {"vl_id": vl_id, "shelfmark": shelfmark, "shelfmarks": shelf,
                     "title": title, "date": date, "genre": genre,
                     "project": rule(title, shelfmark), "image_group": img_group,
                     "file_groups": {k: len(v) for k, v in groups.items()},
                     "fulltext": any(p["alto"] for p in pages), "pages": pages}
            if shelfmark not in best or len(pages) > len(best[shelfmark]["pages"]):
                best[shelfmark] = entry
    return sorted(best.values(), key=lambda e: _natural(e["shelfmark"]))


def inventory(src: Source, root: Path, *, get=fetch) -> dict:
    """Harvest (cached) and write ``inventory.tsv``/``.json`` beside the cache."""
    cache = cache_dir(root, src)
    harvested = harvest(src, cache, get=get)
    objs = objects(cache, src)
    rows = [{k: v for k, v in o.items() if k != "pages"} | {"n_pages": len(o["pages"]),
             "pages_with_text": sum(1 for p in o["pages"] if p["alto"])} for o in objs]
    (cache / "inventory.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    with (cache / "inventory.tsv").open("w", encoding="utf-8") as fh:
        fh.write("vl_id\tshelfmark\tdate\tpages\tfulltext\tproject\tfile_groups\ttitle\n")
        for r in rows:
            fh.write("\t".join([r["vl_id"], "; ".join(r["shelfmarks"]) or r["shelfmark"],
                                r["date"], str(r["n_pages"]), "yes" if r["fulltext"] else "no",
                                r["project"],
                                ",".join(f"{k}:{v}" for k, v in sorted(r["file_groups"].items())),
                                r["title"].replace("\t", " ")]) + "\n")
    by_project = collections.Counter()
    for r in rows:
        by_project[r["project"]] += r["pages_with_text"]
    series = collections.Counter((re.match(r"(K \d+)", r["shelfmark"]) or [r["shelfmark"]])[0]
                                 for r in rows)
    return {"ok": True, "source": src.name, "step": "inventory", "cache": str(cache),
            "oai_pages": len(harvested), "objects": len(rows),
            "pages": sum(r["n_pages"] for r in rows),
            "objects_with_fulltext": sum(1 for r in rows if r["fulltext"]),
            "pages_with_fulltext_ref": sum(r["pages_with_text"] for r in rows),
            "by_project": dict(sorted(by_project.items())),
            "by_series": dict(series.most_common(15)),
            "report": str(cache / "inventory.tsv")}


# ── ALTO → PAGE ──────────────────────────────────────────────────────────────

def stem_of(shelfmark: str, n: int) -> str:
    """``K 2911,65-67`` page 3 → ``K2911-65-67_0003``: no spaces, no commas,
    the shelfmark still readable, `pagexml-hf` pairs image and XML by it."""
    s = shelfmark.replace(" ", "").replace(",", "-")
    s = re.sub(r"[^A-Za-z0-9._-]", "_", s)
    return f"{s}_{n:04d}"


def alto_to_page(alto_bytes: bytes, *, image_name: str, image_size: tuple[int, int],
                 provenance: dict, origin: str) -> tuple[str, int, int]:
    """A PAGE 2013 document from an ALTO file. Returns (xml, lines, chars).

    ALTO coordinates are scaled from its Page ``WIDTH``×``HEIGHT`` onto the
    actual image, so a page scanned at one resolution and read at another
    still lands on the ink. Lines are the ALTO line boxes as rectangles;
    baselines are the lower edge of each box, and the comment says so.
    """
    root = ET.fromstring(alto_bytes)
    m = re.match(r"\{([^}]+)\}", root.tag)
    ns = m.group(1) if m else ""
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
        return x0, y0, max(x1, min(W, x0 + 1)), max(y1, min(H, y0 + 1))

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
            lines.append((f"r{bi}l{li}", box(tl), ltxt))
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
    comment = (f"GROUND TRUTH, converted from {origin}. "
               + "; ".join(f"{k}: {v}" for k, v in provenance.items())
               + ". Geometry: ALTO line boxes as rectangles, scaled onto the image; "
                 "baselines are each box's lower edge, not measured.")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f"<!-- {escape(comment).replace('--', '- -')} -->",
           f'<PcGts xmlns="{PAGE_NS}">',
           "  <Metadata>",
           "    <Creator>agentic_historian oai_gt (ALTO→PAGE)</Creator>",
           f"    <Created>{now}</Created>", f"    <LastChange>{now}</LastChange>",
           f"    <Comments>{escape(json.dumps(provenance, ensure_ascii=False))}</Comments>",
           "  </Metadata>",
           f'  <Page imageFilename="{escape(image_name, {chr(34): "&quot;"})}" '
           f'imageWidth="{W}" imageHeight="{H}">']
    for rid, rb, lines in regions:
        out.append(f'    <TextRegion id="{rid}" type="paragraph">')
        out.append(f'      <Coords points="{pts(rb)}"/>')
        for lid, lb, ltxt in lines:
            x0, y0, x1, y1 = lb
            out += [f'      <TextLine id="{lid}">',
                    f'        <Coords points="{pts(lb)}"/>',
                    f'        <Baseline points="{x0},{y1} {x1},{y1}"/>',
                    f"        <TextEquiv><Unicode>{escape(ltxt)}</Unicode></TextEquiv>",
                    "      </TextLine>"]
        out.append("      <TextEquiv><Unicode>" + escape("\n".join(l[2] for l in lines))
                   + "</Unicode></TextEquiv>")
        out.append("    </TextRegion>")
    out += ["  </Page>", "</PcGts>", ""]
    return "\n".join(out), n_lines, n_chars


def image_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


# ── building ─────────────────────────────────────────────────────────────────

def _ext(url: str) -> str:
    ext = Path(url.split("?")[0]).suffix.lower()
    return ext if ext in IMAGE_EXTS else ".jpg"


def _held_out(tree: Path) -> set[tuple[str, str]]:
    """(project, object) pairs that live on an ``_eval`` side."""
    out = set()
    for side in tree.glob(f"*{EVAL_SUFFIX}"):
        if side.is_dir():
            project = side.name[: -len(EVAL_SUFFIX)]
            for img in side.iterdir():
                if img.is_file() and img.suffix.lower() in IMAGE_EXTS:
                    out.add((project, img.stem.rsplit("_", 1)[0]))
    return out


def build_page(src: Source, obj: dict, pg: dict, tree: Path, *, held_out: set,
               get=fetch, size_of=image_size) -> dict:
    stem = stem_of(obj["shelfmark"], pg["n"])
    project = obj["project"]
    ext = _ext(pg["image"])
    for proj in (project, project + EVAL_SUFFIX):
        if (tree / proj / f"{stem}{ext}").exists() and (tree / proj / "page" / f"{stem}.xml").exists():
            return {"stem": stem, "status": "present"}
    # A new page of an object that is held out goes where its siblings are.
    if (project, stem.rsplit("_", 1)[0]) in held_out:
        project = project + EVAL_SUFFIX
    if not (_same_host(src, pg["image"]) and _same_host(src, pg["alto"])):
        return {"stem": stem, "status": "failed",
                "error": f"refused: a file outside {src.base_url} ({pg['image']}, {pg['alto']})"}
    try:
        img = get(pg["image"])
        alto = get(pg["alto"])
    except OaiGtError as exc:
        return {"stem": stem, "status": "failed", "error": str(exc)}
    try:
        size = size_of(img)
    except Exception as exc:  # noqa: BLE001 — a bad image is one page's failure
        return {"stem": stem, "status": "failed", "error": f"image unreadable: {exc}"}
    prov = {"shelfmark": obj["shelfmark"], "title": obj["title"], "date": obj["date"],
            "genre": obj.get("genre", ""), "hand": obj["project"], "vl_id": obj["vl_id"],
            "page": pg["order"], "image": pg["image"], "alto": pg["alto"],
            "image_group": obj["image_group"]}
    try:
        xml, n_lines, n_chars = alto_to_page(alto, image_name=f"{stem}{ext}", image_size=size,
                                             provenance=prov, origin=src.description)
    except ET.ParseError as exc:
        return {"stem": stem, "status": "failed", "error": f"ALTO unreadable: {exc}"}
    if n_lines == 0:
        return {"stem": stem, "status": "no-text"}
    img_path = tree / project / f"{stem}{ext}"
    xml_path = tree / project / "page" / f"{stem}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    for path, data in ((img_path, img), (xml_path, xml.encode("utf-8"))):
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    return {"stem": stem, "status": "written", "project": project, "lines": n_lines,
            "chars": n_chars, "bytes": len(img)}


def build(src: Source, root: Path, *, dry_run: bool = True, limit: Optional[int] = None,
          workers: int = 4, get=fetch, size_of=image_size,
          progress: Optional[Callable[[str], None]] = None) -> dict:
    """The tree ``<root>/<src.tree>`` from the cached harvest. Resumable."""
    cache = cache_dir(root, src)
    if not any(cache.glob(f"oai_{src.metadata_prefix}_*.xml")):
        raise OaiGtError(f"no harvest under {cache} — run step 'inventory' first")
    workers = max(1, min(int(workers), MAX_WORKERS))
    objs = [o for o in objects(cache, src) if o["fulltext"]]
    if limit:
        objs = objs[:limit]
    pages = [(o, p) for o in objs for p in o["pages"] if p["image"] and p["alto"]]
    no_alto = sum(1 for o in objs for p in o["pages"] if p["image"] and not p["alto"])
    by_project = collections.Counter(o["project"] for o, _ in pages)
    tree = Path(root) / src.tree
    result = {"ok": True, "source": src.name, "step": "build", "dry_run": dry_run,
              "tree": str(tree), "objects": len(objs), "pages": len(pages),
              "pages_without_fulltext": no_alto, "by_project": dict(sorted(by_project.items())),
              "image_group": objs[0]["image_group"] if objs else None}
    if dry_run:
        sample = pages[:: max(1, len(pages) // 12)][:12]
        sizes = [s for s in (head_size(p["image"]) for _, p in sample) if s]
        if sizes:
            avg = sum(sizes) / len(sizes)
            result["estimate_gb"] = round(avg * len(pages) / 1e9, 1)
        return result
    tree.mkdir(parents=True, exist_ok=True)
    held = _held_out(tree)
    t0 = time.time()
    counts: collections.Counter = collections.Counter()
    failures, total = [], 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(build_page, src, o, p, tree, held_out=held, get=get, size_of=size_of)
                for o, p in pages]
        for i, fut in enumerate(cf.as_completed(futs), 1):
            r = fut.result()
            counts[r["status"]] += 1
            total += r.get("bytes", 0)
            if r["status"] == "failed":
                failures.append(r)
            if progress and (i % 100 == 0 or i == len(pages)):
                progress(f"  {i}/{len(pages)}  {dict(counts)}  {total / 1e9:.2f} GB  "
                         f"{time.time() - t0:.0f}s")
    manifest_path = tree / "export.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    if manifest.get("uploads"):
        manifest["changed_after_upload"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    manifest.update({
        "mode": "raw_xml", "kind": "ground-truth",
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": src.description, "oai": {"url": src.oai_url, "set": src.set_spec},
        "geometry": "ALTO line boxes converted to PAGE rectangles; baselines synthesised "
                    "as each box's lower edge",
        "projects": sorted(p.name for p in tree.iterdir() if p.is_dir() and (p / "page").is_dir()),
        "objects": len(objs), "pages_planned": len(pages), "counts": dict(counts)})
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    (tree / "failures.json").write_text(json.dumps(failures, ensure_ascii=False, indent=1),
                                        encoding="utf-8")
    result.update({"counts": dict(counts), "downloaded_gb": round(total / 1e9, 2),
                   "minutes": round((time.time() - t0) / 60, 1), "failures": len(failures),
                   "changed_after_upload": bool(manifest.get("uploads"))})
    return result


def format_result(r: dict) -> str:
    if r["step"] == "inventory":
        return "\n".join([
            f"source   : {r['source']}   ({r['oai_pages']} OAI page(s), cache {r['cache']})",
            f"objects  : {r['objects']} with pages, {r['objects_with_fulltext']} with full text",
            f"pages    : {r['pages']}  ({r['pages_with_fulltext_ref']} with an ALTO file)",
            "projects : " + ", ".join(f"{k} {v}" for k, v in r["by_project"].items()),
            "series   : " + ", ".join(f"{k} {v}" for k, v in r["by_series"].items()),
            f"report   : {r['report']}"])
    lines = [f"tree     : {r['tree']}",
             f"objects  : {r['objects']} with full text",
             f"pages    : {r['pages']}  (pages without full text left out: {r['pages_without_fulltext']})",
             "projects : " + ", ".join(f"{k} {v}" for k, v in r["by_project"].items()),
             f"images   : METS group {r['image_group']}"]
    if r["dry_run"]:
        if r.get("estimate_gb") is not None:
            lines.append(f"estimate : about {r['estimate_gb']} GB")
        lines += ["", "(dry run — nothing written)"]
    else:
        lines += [f"counts   : {r['counts']}  {r['downloaded_gb']} GB in {r['minutes']} min"]
        if r["failures"]:
            lines.append(f"{r['failures']} page(s) failed — failures.json; run again to retry")
        if r["changed_after_upload"]:
            lines.append("NOTE: the tree was uploaded before — upload again for the hub to match")
    return "\n".join(lines)
