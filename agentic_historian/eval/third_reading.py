#!/usr/bin/env python3
"""third_reading.py — the epoch's Qwen as a third, combined reading (#903).

    python -m eval.third_reading plan.json --out <run dir> --dry-run
    python -m eval.third_reading plan.json --out <run dir> --max-pages 3
    python -m eval.third_reading plan.json --out <run dir> [--level line|page|both]
    python -m eval.third_reading plan.json --out <run dir> --report-only

The question: does a Qwen fine-tuned on the epoch read better when it is given
the classical engines' transcription along with the image? Seven arms, on the
same lines, against ground truth:

    A1  kraken                       /recognize
    A2  TrOCR                        /recognize
    B   fusion of A1 + A2            fusion.fuse, no LLM arbitration
    C   the epoch's Qwen, image only its own prompt
    B3  fusion of A1 + A2 + C        the vote with the Qwen as a third voter
    D   Qwen, image + B as a hint    HINT_PROMPT
    D2  Qwen, image + A1 and A2      HINTS_PROMPT (D' in the issue)

**Two levels.**

* ``line`` — the ground truth's own lines. kraken and TrOCR get them as
  ``lines`` and read exactly those (serving-atr-inference#285); the Qwen gets
  the crop of each line's box, fitted to the line budget. Every arm reads the
  same pixels, so a line's numbers compare.
* ``page`` — what the pipeline would do with a page it has never seen. The page
  is segmented once (``/segment``) and kraken and TrOCR read those lines, so B
  fuses line by line on one geometry. A page model (``qwen_page``) reads the
  whole page, fitted to the page budget; a set without one has its line model
  read the segmented lines. Scored on the page text against the ground truth's
  lines in document order.

**Plan file** — one JSON object, ``sets`` a list; paths relative to the plan::

    {"sets": [{"name": "medieval", "pages": "missiven/pages",
               "kraken": "kraken-bastarda_inzigkofen",
               "trocr": "trocr-medieval-escriptmask",
               "qwen": "qwen3vl-medieval-german-v3",
               "qwen_page": null,
               "docs": "missiven/docs.csv", "years": [1450, 1550]}]}

``pages`` is a directory of PAGE XML files (searched recursively) with their
images. ``docs`` is an optional CSV with the columns ``page`` (the XML's path
relative to ``pages``), ``doc`` and ``year``. The document is the unit of the
bootstrap; without the CSV it is the XML's parent directory. ``years`` keeps
the pages whose year lies in the closed interval, and needs the CSV: a set
that asks for a date filter and cannot apply one stops.

**Resumable.** Every reading is appended to ``<out>/readings.jsonl`` as it
comes, keyed by item, arm and settings (model, level, pixel budget, prompt
version). A stopped run starts where it stood; a failed call is not a reading
and is asked again next time. ``--report-only`` scores what is there.

**What the report says** (``report.json``, ``report.md``): per set, level and
arm the corpus CER and WER (whitespace collapsed, case and punctuation kept),
length ratio, the spread over items, empty and truncated answers, seconds and
tokens; a 95 % interval from resampling **documents**; paired differences
(D − B, D − C, D2 − B, D2 − C, B3 − B, C − B) on the same resamples; and the
anchoring of D and D2 split by whether the hint was right — did the Qwen keep a
right hint, break it, copy a wrong one, or fix it.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import re
import statistics
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval.metrics import edit_distance  # noqa: E402
from ruemlang.pagexml import parse_lines  # noqa: E402

#: Must equal serving-atr-inference ``seam.VLM_PIXEL_BUDGET``: the scale the
#: epoch models were trained at. A model's own ``max_pixels`` wins.
PIXELS = {"line": 256 * 32 * 32, "page": 2048 * 32 * 32}
#: The gateway's defaults for a model that names no ``max_new_tokens``.
TOKENS = {"line": 512, "page": 4096}
LEVELS = ("line", "page")
ARMS = ("A1", "A2", "B", "C", "B3", "D", "D2")
#: The arms that cost a call; B and B3 are computed from them.
READ_ARMS = ("A1", "A2", "C", "D", "D2")
PAIRS = (("D", "B"), ("D", "C"), ("D2", "B"), ("D2", "C"), ("B3", "B"), ("C", "B"))
BOOTSTRAP = 1000
SEED = 903

#: Bumped whenever the hint wording changes: it is part of every D/D2
#: reading's settings, so a new wording is read anew, never mixed with the old.
HINT_VERSION = "903-v1"
HINT_PROMPT = (
    "{base}\n\n"
    "Another recogniser read this image as follows. Its reading may contain errors.\n"
    "<reading>\n{hint}\n</reading>\n"
    "Transcribe what the image shows. Use the reading only where the image agrees with it."
)
HINTS_PROMPT = (
    "{base}\n\n"
    "Two other recognisers read this image as follows. Both readings may contain errors.\n"
    "<reading n=\"1\">\n{a}\n</reading>\n"
    "<reading n=\"2\">\n{b}\n</reading>\n"
    "Transcribe what the image shows. Use the readings only where the image agrees with them."
)
#: When the model has no prompt of its own.
BASE_PROMPT = "Transcribe the handwritten text in this image exactly as written."

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".JPG", ".JPEG", ".PNG", ".TIF", ".TIFF")


class PlanError(ValueError):
    """The plan cannot be measured as written."""


# ── the ground truth ─────────────────────────────────────────────────────────

@dataclass
class GtLine:
    id: str
    text: str
    bbox: list[int]
    baseline: Optional[list[list[int]]]
    boundary: Optional[list[list[int]]]

    def wire(self, order: int) -> dict:
        """The line as ``/recognize`` takes it in ``lines``."""
        out = {"order": order, "bbox": self.bbox}
        if self.baseline:
            out["baseline"] = self.baseline
        if self.boundary:
            out["boundary"] = self.boundary
        return out


@dataclass
class GtPage:
    set: str
    key: str                      # the XML's path relative to the set's pages
    doc: str
    year: Optional[int]
    xml: Path
    image: Path
    lines: list[GtLine]           # transcribed lines with a box, document order
    skipped_lines: int = 0        # transcribed lines without a box
    #: Every transcribed line, boxed or not, document order. The page's text is
    #: all of them: a TEI edition (the Missiven) has no coordinates at all and is
    #: still a page reference. None means "the boxed lines are all there is".
    ref_lines: Optional[list[str]] = None

    @property
    def reference(self) -> str:
        if self.ref_lines is not None:
            return "\n".join(self.ref_lines)
        return "\n".join(ln.text for ln in self.lines)


def _image_name(xml_text: str) -> Optional[str]:
    root = ET.fromstring(xml_text)
    for el in root.iter():
        if el.tag.endswith("}Page") or el.tag == "Page":
            return el.get("imageFilename")
    return None


def find_image(xml: Path, name: Optional[str]) -> Path:
    """The page image: ``imageFilename`` next to the XML or one level up (the
    Transkribus export puts PAGE files in ``page/``), else the XML's stem with
    an image suffix."""
    tried = []
    if name:
        for d in (xml.parent, xml.parent.parent):
            tried.append(d / Path(name).name)
    for d in (xml.parent, xml.parent.parent):
        tried.extend(d / (xml.stem + s) for s in IMAGE_SUFFIXES)
    for p in tried:
        if p.is_file():
            return p
    raise PlanError(f"no image for {xml} (imageFilename={name!r})")


def read_page(set_name: str, root: Path, xml: Path, doc: str, year: Optional[int]) -> GtPage:
    text = xml.read_text(encoding="utf-8")
    lines, skipped, every = [], 0, []
    for raw in parse_lines(text):
        ref = (raw["text"] or "").strip()
        if not ref:
            continue
        every.append(ref)
        if not raw["bbox"]:
            skipped += 1
            continue
        lines.append(GtLine(
            id=raw["id"] or f"l{len(lines)}", text=ref, bbox=list(raw["bbox"]),
            baseline=[list(p) for p in raw["baseline"]] if raw["baseline"] else None,
            boundary=[list(p) for p in raw["points"]] if raw["points"] else None))
    return GtPage(set=set_name, key=xml.relative_to(root).as_posix(), doc=doc, year=year,
                  xml=xml, image=find_image(xml, _image_name(text)), lines=lines,
                  skipped_lines=skipped, ref_lines=every)


def _is_page_xml(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return False
    return "PcGts" in head


def _docs_table(path: Path) -> dict[str, tuple[str, Optional[int]]]:
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            page = (row.get("page") or "").strip()
            if not page:
                continue
            year = (row.get("year") or "").strip()
            out[page] = ((row.get("doc") or "").strip() or page,
                         int(year) if re.fullmatch(r"-?\d+", year) else None)
    return out


def load_set(spec: dict, base: Path) -> list[GtPage]:
    """The set's pages, filtered by year when asked, sorted by key."""
    name = spec["name"]
    root = (base / spec["pages"]).resolve()
    if not root.is_dir():
        raise PlanError(f"set {name}: pages {root} is not a directory")
    table = _docs_table(base / spec["docs"]) if spec.get("docs") else None
    years = spec.get("years")
    if years and table is None:
        raise PlanError(f"set {name} asks for years {years} but names no docs CSV with a year column")
    pages = []
    for xml in sorted(p for p in root.rglob("*.xml") if _is_page_xml(p)):
        key = xml.relative_to(root).as_posix()
        if table is not None:
            if key not in table:
                continue              # the CSV is the selection
            doc, year = table[key]
        else:
            doc, year = (xml.parent.relative_to(root).as_posix() or xml.stem), None
        if years and (year is None or not years[0] <= year <= years[1]):
            continue
        pages.append(read_page(name, root, xml, doc, year))
    return pages


def spread(pages: list[GtPage], n: Optional[int]) -> list[GtPage]:
    """Up to ``n`` pages, taken a document at a time in turn — so a small sample
    has as many documents as it can, and the bootstrap over documents has
    something to resample."""
    if not n or n >= len(pages):
        return list(pages)
    by_doc: dict[str, list[GtPage]] = {}
    for p in pages:
        by_doc.setdefault(p.doc, []).append(p)
    queues = [by_doc[d] for d in sorted(by_doc)]
    out: list[GtPage] = []
    while len(out) < n:
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return sorted(out, key=lambda p: p.key)


def load_plan(path: Path) -> list[dict]:
    plan = json.loads(path.read_text(encoding="utf-8"))
    sets = plan.get("sets") if isinstance(plan, dict) else None
    if not sets:
        raise PlanError(f"{path}: no sets")
    seen = set()
    for s in sets:
        for k in ("name", "pages", "kraken", "trocr", "qwen"):
            if not s.get(k):
                raise PlanError(f"{path}: a set without '{k}': {s}")
        if s["name"] in seen:
            raise PlanError(f"{path}: set {s['name']} twice")
        seen.add(s["name"])
        y = s.get("years")
        if y is not None and (len(y) != 2 or y[0] > y[1]):
            raise PlanError(f"set {s['name']}: years must be [from, to], not {y}")
    return sets


# ── images ───────────────────────────────────────────────────────────────────

def fit(img, budget: int):
    """``img`` scaled down to at most ``budget`` pixels; never up. The gateway's
    chat passthrough applies no budget, so this is where the model's training
    scale is kept."""
    w, h = img.size
    if w * h <= budget:
        return img
    f = (budget / (w * h)) ** 0.5
    return img.resize((max(1, int(w * f)), max(1, int(h * f))))


def encode(img, level: str) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if level == "page":
        img.convert("RGB").save(buf, format="JPEG", quality=92)
        return buf.getvalue(), "image/jpeg"
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), "image/png"


def crop(img, bbox) -> Optional[object]:
    x0, y0, x1, y1 = (int(v) for v in bbox)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(img.width, x1), min(img.height, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return img.crop((x0, y0, x1, y1))


# ── the gateway ──────────────────────────────────────────────────────────────

class Gateway:
    """The four calls this measurement makes, over httpx."""

    def __init__(self, url: str, key: Optional[str], timeout: float = 600.0, transport=None):
        import httpx
        self.url = url.rstrip("/")
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={"X-API-Key": key} if key else {})

    def _ok(self, r):
        if r.status_code >= 400:
            raise RuntimeError(f"{r.request.method} {r.request.url.path}: {r.status_code} {r.text[:300]}")
        return r.json()

    def models(self) -> dict[str, dict]:
        data = self._ok(self._http.get(f"{self.url}/models"))
        return {m["id"]: m for m in data.get("models", [])}

    def segment(self, image: bytes, name: str) -> list[dict]:
        r = self._http.post(f"{self.url}/segment", files={"image": (name, image)},
                            data={"mode": "baseline"})
        return self._ok(r).get("lines", [])

    def recognize(self, image: bytes, name: str, model: str, lines: list[dict]) -> dict:
        r = self._http.post(f"{self.url}/recognize", files={"image": (name, image)},
                            data={"model": model, "lines": json.dumps(lines)})
        return self._ok(r)

    def chat(self, model: str, image: bytes, mime: str, prompt: str, max_tokens: int) -> dict:
        import base64
        url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        body = {"model": model, "temperature": 0.0, "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": url}},
                    {"type": "text", "text": prompt}]}]}
        data = self._ok(self._http.post(f"{self.url}/v1/chat/completions", json=body))
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return {"text": choice["message"]["content"] or "",
                "finish": choice.get("finish_reason"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens")}


# ── readings ─────────────────────────────────────────────────────────────────

class Store:
    """``readings.jsonl``: one record per (item, arm, settings), appended as it
    comes. Records with an ``error`` are kept for the log but are not readings."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._done: dict[tuple[str, str, str], dict] = {}
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue                  # cut by a kill: read again
                if not r.get("error"):
                    self._done[(r["key"], r["arm"], r["settings"])] = r

    def get(self, key: str, arm: str, settings: str) -> Optional[dict]:
        return self._done.get((key, arm, settings))

    def put(self, rec: dict) -> dict:
        with self._lock:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if not rec.get("error"):
                self._done[(rec["key"], rec["arm"], rec["settings"])] = rec
        return rec


def settings_of(**kw) -> str:
    raw = json.dumps(kw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def engine_settings(arm: str, model: str, level: str) -> str:
    return settings_of(arm=arm, model=model, level=level)


def qwen_settings(arm: str, spec: dict, level: str) -> str:
    """Everything that makes a Qwen reading what it is: model, level, pixel
    budget, its prompt, and for D/D2 the hint wording's version."""
    return settings_of(arm=arm, model=spec["id"], level=level,
                       pixels=spec.get("max_pixels") or PIXELS[level],
                       hint=HINT_VERSION if arm in ("D", "D2") else None,
                       prompt=spec.get("prompt") or BASE_PROMPT)


def fuse_texts(named: dict[str, str]) -> str:
    """The pipeline's vote without arbitration over the non-empty readings."""
    import fusion
    recs = [{"engine": k, "text": v} for k, v in named.items() if (v or "").strip()]
    if not recs:
        return ""
    return fusion.fuse(recs, arbitrate=False).text or ""


def hint_prompt(base: str, hint: str) -> str:
    return HINT_PROMPT.format(base=base, hint=hint.strip() or "(empty)")


def hints_prompt(base: str, a: str, b: str) -> str:
    return HINTS_PROMPT.format(base=base, a=a.strip() or "(empty)", b=b.strip() or "(empty)")


# ── reading one page ─────────────────────────────────────────────────────────

@dataclass
class Arm:
    """What one set reads with, resolved against the gateway's /models."""
    kraken: str
    trocr: str
    qwen: dict                    # the /models entry
    qwen_page: Optional[dict]


class Reader:
    def __init__(self, gw: Gateway, store: Store, workers: int = 4,
                 log: Callable[[str], None] = print):
        self.gw, self.store, self.workers, self.log = gw, store, max(1, workers), log

    # one call, recorded
    def _call(self, key: str, arm: str, settings: str, fn: Callable[[], dict],
              extra: Optional[dict] = None) -> Optional[dict]:
        have = self.store.get(key, arm, settings)
        if have is not None:
            return have
        t0 = time.monotonic()
        rec = {"key": key, "arm": arm, "settings": settings, **(extra or {})}
        try:
            rec.update(fn())
        except Exception as exc:  # noqa: BLE001 — a failed call is logged, not fatal
            rec.update(text="", error=f"{type(exc).__name__}: {exc}")
            self.log(f"  {arm} {key}: {rec['error']}")
        rec["sec"] = round(time.monotonic() - t0, 3)
        self.store.put(rec)
        return None if rec.get("error") else rec

    def _engine(self, key, arm, model, image, name, lines, level) -> Optional[dict]:
        """kraken or TrOCR on the given lines: one record for the page, a text per line."""
        st = engine_settings(arm, model, level)

        def go():
            res = self.gw.recognize(image, name, model, lines)
            got = sorted(res.get("lines") or [], key=lambda ln: ln.get("order", 0))
            if len(got) != len(lines):
                raise RuntimeError(f"{len(got)} lines back for {len(lines)} given "
                                   f"(segmented_by={res.get('segmented_by')!r})")
            return {"lines": [(ln.get("text") or "").strip() for ln in got],
                    "text": "\n".join((ln.get("text") or "").strip() for ln in got),
                    "segmented_by": res.get("segmented_by")}
        return self._call(key, arm, st, go, {"model": model, "level": level})

    def _qwen(self, key, arm, spec, level, png, mime, prompt, extra) -> Optional[dict]:
        st = qwen_settings(arm, spec, level)
        tokens = spec.get("max_new_tokens") or TOKENS[level]
        return self._call(key, arm, st,
                          lambda: self.gw.chat(spec["id"], png, mime, prompt, tokens),
                          {"model": spec["id"], "level": level, **extra})

    @staticmethod
    def _px(spec: dict, level: str) -> int:
        return spec.get("max_pixels") or PIXELS[level]

    def _qwen_lines(self, key_of, spec, img, boxes, a1, a2) -> None:
        """C, D and D2 for each line box, in parallel; texts by line index."""
        base = spec.get("prompt") or BASE_PROMPT
        px = self._px(spec, "line")

        def one(i):
            piece = crop(img, boxes[i])
            if piece is None:
                # Nothing to read is an empty reading, recorded, so the page
                # it belongs to is complete rather than waiting for it forever.
                for arm in ("C", "D", "D2"):
                    self._call(key_of(i), arm, qwen_settings(arm, spec, "line"),
                               lambda: {"text": "", "note": "nothing to crop"},
                               {"model": spec["id"], "level": "line"})
                return
            png, mime = encode(fit(piece, px), "line")
            hint = fuse_texts({"kraken": a1[i], "trocr": a2[i]})
            for arm, prompt, extra in (
                ("C", base, {}),
                ("D", hint_prompt(base, hint), {"hint": hint}),
                ("D2", hints_prompt(base, a1[i], a2[i]), {"hints": [a1[i], a2[i]]}),
            ):
                self._qwen(key_of(i), arm, spec, "line", png, mime, prompt, extra)

        with ThreadPoolExecutor(self.workers) as pool:
            list(pool.map(one, range(len(boxes))))

    def line_level(self, page: GtPage, arms: Arm) -> None:
        from PIL import Image
        if not page.lines:
            return
        image = page.image.read_bytes()
        lines = [ln.wire(i) for i, ln in enumerate(page.lines)]
        pkey = f"{page.set}/line/{page.key}"
        r1 = self._engine(pkey, "A1", arms.kraken, image, page.image.name, lines, "line")
        r2 = self._engine(pkey, "A2", arms.trocr, image, page.image.name, lines, "line")
        if r1 is None or r2 is None:
            self.log(f"  line {page.key}: an engine failed; the Qwen arms wait for the next run")
            return
        with Image.open(page.image) as im:
            img = im.convert("RGB")
            self._qwen_lines(lambda i: f"{pkey}#{page.lines[i].id}", arms.qwen, img,
                             [ln.bbox for ln in page.lines], r1["lines"], r2["lines"])

    def page_level(self, page: GtPage, arms: Arm) -> None:
        from PIL import Image
        image = page.image.read_bytes()
        pkey = f"{page.set}/page/{page.key}"
        seg = self._call(pkey, "SEG", settings_of(arm="SEG"),
                         lambda: {"lines": self.gw.segment(image, page.image.name)})
        if seg is None:
            return
        lines = [{k: v for k, v in ln.items() if k in ("bbox", "baseline", "boundary", "regions")}
                 | {"order": i} for i, ln in enumerate(seg["lines"])]
        if not lines:
            self.log(f"  page {page.key}: the segmenter found no lines")
            return
        r1 = self._engine(pkey, "A1", arms.kraken, image, page.image.name, lines, "page")
        r2 = self._engine(pkey, "A2", arms.trocr, image, page.image.name, lines, "page")
        if r1 is None or r2 is None:
            self.log(f"  page {page.key}: an engine failed; the Qwen arms wait for the next run")
            return
        with Image.open(page.image) as im:
            img = im.convert("RGB")
            if arms.qwen_page is not None:
                spec = arms.qwen_page
                base = spec.get("prompt") or BASE_PROMPT
                png, mime = encode(fit(img, self._px(spec, "page")), "page")
                hint = "\n".join(fuse_texts({"kraken": a, "trocr": b})
                                 for a, b in zip(r1["lines"], r2["lines"]))
                self._qwen(pkey, "C", spec, "page", png, mime, base, {})
                self._qwen(pkey, "D", spec, "page", png, mime, hint_prompt(base, hint), {"hint": hint})
                self._qwen(pkey, "D2", spec, "page", png, mime,
                           hints_prompt(base, r1["text"], r2["text"]),
                           {"hints": [r1["text"], r2["text"]]})
                return
            # A line model reads the segmented lines; the page is their join.
            self._qwen_lines(lambda i: f"{pkey}#{i}", arms.qwen, img,
                             [ln.get("bbox") or [0, 0, 0, 0] for ln in lines],
                             r1["lines"], r2["lines"])


# ── assembling items from readings ───────────────────────────────────────────

@dataclass
class Item:
    set: str
    level: str
    key: str
    doc: str
    ref: str
    texts: dict[str, Optional[str]] = field(default_factory=dict)    # arm → text (None = no reading)
    meta: dict[str, list[dict]] = field(default_factory=dict)       # arm → records behind it
    hint: Optional[str] = None
    hints: Optional[list[str]] = None


def _text(rec: Optional[dict]) -> Optional[str]:
    return None if rec is None else (rec.get("text") or "").strip()


def items_for(page: GtPage, level: str, arms: Arm, store: Store) -> list[Item]:
    """The scored items of one page at one level, from what the store holds.
    An arm with no reading is ``None`` and scores as an empty answer."""
    def get(key, arm, spec=None, lvl=level, model=None):
        if arm in ("A1", "A2"):
            return store.get(key, arm, engine_settings(arm, model, lvl))
        return store.get(key, arm, qwen_settings(arm, spec, lvl))

    out: list[Item] = []
    if level == "line":
        pkey = f"{page.set}/line/{page.key}"
        r1, r2 = get(pkey, "A1", model=arms.kraken), get(pkey, "A2", model=arms.trocr)
        for i, ln in enumerate(page.lines):
            key = f"{pkey}#{ln.id}"
            it = Item(page.set, level, key, page.doc, ln.text)
            a1 = r1["lines"][i] if r1 else None
            a2 = r2["lines"][i] if r2 else None
            c, d, d2 = (get(key, a, arms.qwen) for a in ("C", "D", "D2"))
            it.texts = {"A1": a1, "A2": a2, "C": _text(c), "D": _text(d), "D2": _text(d2)}
            it.meta = {"A1": [r1] if r1 else [], "A2": [r2] if r2 else [],
                       "C": [c] if c else [], "D": [d] if d else [], "D2": [d2] if d2 else []}
            it.hint = fuse_texts({"kraken": a1 or "", "trocr": a2 or ""})
            it.hints = [a1 or "", a2 or ""]
            out.append(it)
    else:
        pkey = f"{page.set}/page/{page.key}"
        r1, r2 = get(pkey, "A1", model=arms.kraken), get(pkey, "A2", model=arms.trocr)
        it = Item(page.set, level, pkey, page.doc, page.reference)
        it.texts = {"A1": _text(r1), "A2": _text(r2)}
        it.meta = {"A1": [r1] if r1 else [], "A2": [r2] if r2 else []}
        if r1 and r2:
            it.hint = "\n".join(fuse_texts({"kraken": a, "trocr": b})
                                for a, b in zip(r1["lines"], r2["lines"]))
        it.hints = [it.texts["A1"] or "", it.texts["A2"] or ""]
        if arms.qwen_page is not None:
            for a in ("C", "D", "D2"):
                rec = get(pkey, a, arms.qwen_page)
                it.texts[a], it.meta[a] = _text(rec), [rec] if rec else []
        else:
            n = len(r1["lines"]) if r1 else 0
            for a in ("C", "D", "D2"):
                recs = [get(f"{pkey}#{i}", a, arms.qwen, lvl="line") for i in range(n)]
                done = n > 0 and all(r is not None for r in recs)
                it.texts[a] = "\n".join(_text(r) for r in recs) if done else None
                it.meta[a] = [r for r in recs if r]
        out.append(it)
    for it in out:
        it.texts["B"] = (it.hint if it.texts.get("A1") is not None
                         and it.texts.get("A2") is not None else None)
        if level == "line" or arms.qwen_page is None:
            # Line by line on one geometry; at page level for a line model, the
            # same vote as B with the Qwen's line readings as a third voter.
            it.texts["B3"] = _b3(it, level)
        else:
            it.texts["B3"] = (fuse_texts({"kraken": it.texts["A1"], "trocr": it.texts["A2"],
                                          "qwen": it.texts["C"]})
                              if None not in (it.texts["A1"], it.texts["A2"], it.texts["C"]) else None)
    return out


def _b3(it: Item, level: str) -> Optional[str]:
    a1, a2, c = it.texts.get("A1"), it.texts.get("A2"), it.texts.get("C")
    if None in (a1, a2, c):
        return None
    if level == "line":
        return fuse_texts({"kraken": a1, "trocr": a2, "qwen": c})
    rows = zip(a1.split("\n"), a2.split("\n"), c.split("\n"))
    return "\n".join(fuse_texts({"kraken": x, "trocr": y, "qwen": z}) for x, y, z in rows)


# ── scoring ──────────────────────────────────────────────────────────────────

def flat(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def errors(hyp: Optional[str], ref: str) -> tuple[int, int, int, int]:
    """(char edits, ref chars, word edits, ref words), whitespace collapsed."""
    h, r = flat(hyp), flat(ref)
    hw, rw = h.split(), r.split()
    return edit_distance(h, r), len(r), _word_distance(hw, rw), len(rw)


def _word_distance(a: list[str], b: list[str]) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, y in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y))
        prev = cur
    return prev[-1]


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def corpus(items: list[Item], arm: str) -> dict:
    ce = cc = we = wc = hyp = 0
    per = []
    for it in items:
        e, c, w, n = errors(it.texts.get(arm), it.ref)
        ce, cc, we, wc = ce + e, cc + c, we + w, wc + n
        hyp += len(flat(it.texts.get(arm)))
        if c:
            per.append(e / c)
    recs = [r for it in items for r in it.meta.get(arm, [])]
    per.sort()
    return {
        "arm": arm, "items": len(items),
        "answered": sum(1 for it in items if it.texts.get(arm) is not None),
        "empty": sum(1 for it in items if it.texts.get(arm) == ""),
        "chars": cc, "cer": _rate(ce, cc), "wer": _rate(we, wc), "length_ratio": _rate(hyp, cc),
        "per_item": None if not per else {
            "median": round(statistics.median(per), 4),
            "p90": round(per[min(len(per) - 1, round(0.9 * (len(per) - 1)))], 4)},
        "truncated": sum(1 for r in recs if r.get("finish") == "length"),
        "sec": round(sum(r.get("sec") or 0 for r in recs), 1),
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in recs),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in recs),
    }


def bootstrap(items: list[Item], arms: Iterable[str], pairs=PAIRS,
              n: int = BOOTSTRAP, seed: int = SEED) -> dict:
    """95 % intervals of the corpus CER from resampling documents with
    replacement, and of paired differences on the same resamples."""
    arms = list(arms)
    docs: dict[str, dict[str, list[int]]] = {}
    for it in items:
        d = docs.setdefault(it.doc, {a: [0, 0] for a in arms})
        for a in arms:
            e, c, _, _ = errors(it.texts.get(a), it.ref)
            d[a][0] += e
            d[a][1] += c
    names = sorted(docs)
    if len(names) < 2:
        return {"documents": len(names), "note": "fewer than two documents: no interval"}
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {a: [] for a in arms}
    for _ in range(n):
        pick = [names[rng.randrange(len(names))] for _ in names]
        for a in arms:
            e = sum(docs[d][a][0] for d in pick)
            c = sum(docs[d][a][1] for d in pick)
            draws[a].append(e / c if c else 0.0)

    def ci(xs):
        xs = sorted(xs)
        return [round(xs[int(0.025 * (len(xs) - 1))], 4), round(xs[int(0.975 * (len(xs) - 1))], 4)]

    out = {"documents": len(names), "resamples": n, "seed": seed,
           "cer": {a: ci(draws[a]) for a in arms}, "diff": {}}
    for x, y in pairs:
        if x in draws and y in draws:
            diff = [p - q for p, q in zip(draws[x], draws[y])]
            out["diff"][f"{x}-{y}"] = {"ci": ci(diff),
                                       "p_better": round(sum(1 for v in diff if v < 0) / n, 3)}
    return out


def _cer(hyp: Optional[str], ref: str) -> float:
    e, c, _, _ = errors(hyp, ref)
    return e / c if c else 0.0


def anchoring(items: list[Item]) -> dict:
    """What D and D2 did with their hints, split by whether the hint was right.

    For D the hint is B. For D2 "right" means either reading was, "copied"
    that the answer equals one of them, and better/worse is against B.
    """
    out = {}
    for arm in ("D", "D2"):
        s = {"hint_right": 0, "kept": 0, "broke": 0,
             "hint_wrong": 0, "copied": 0, "fixed": 0, "better": 0, "worse": 0}
        for it in items:
            ans = it.texts.get(arm)
            if ans is None or it.hint is None:
                continue
            ref = flat(it.ref)
            hints = [flat(it.hint)] if arm == "D" else [flat(h) for h in (it.hints or [])]
            if ref in hints:
                s["hint_right"] += 1
                s["kept" if flat(ans) == ref else "broke"] += 1
                continue
            s["hint_wrong"] += 1
            if flat(ans) in hints:
                s["copied"] += 1
            elif flat(ans) == ref:
                s["fixed"] += 1
            a, b = _cer(ans, it.ref), _cer(it.hint, it.ref)
            s["better"] += a < b
            s["worse"] += a > b
        s["copy_rate_when_wrong"] = _rate(s["copied"], s["hint_wrong"])
        s["break_rate_when_right"] = _rate(s["broke"], s["hint_right"])
        out[arm] = s
    return out


def report(groups: dict[tuple[str, str], list[Item]], meta: dict) -> dict:
    out = {"meta": meta, "groups": []}
    for (set_name, level), items in sorted(groups.items()):
        docs = sorted({it.doc for it in items})
        out["groups"].append({
            "set": set_name, "level": level, "items": len(items), "documents": len(docs),
            "arms": [corpus(items, a) for a in ARMS],
            "bootstrap": bootstrap(items, ARMS),
            "anchoring": anchoring(items),
        })
    return out


def markdown(rep: dict) -> str:
    lines = ["# Third reading (#903)", "",
             f"Hint prompt {rep['meta'].get('hint_version')}; CER/WER whitespace-collapsed, "
             "case and punctuation kept; 95 % intervals from resampling documents.", ""]
    for g in rep["groups"]:
        bs = g["bootstrap"]
        lines += [f"## {g['set']} · {g['level']} — {g['items']} items, {g['documents']} documents", "",
                  "| arm | CER | 95 % | WER | len | answered | empty | trunc. | s |",
                  "|---|---:|---|---:|---:|---:|---:|---:|---:|"]
        for a in g["arms"]:
            ci = (bs.get("cer") or {}).get(a["arm"])
            lines.append(f"| {a['arm']} | {a['cer']} | {ci or '—'} | {a['wer']} | {a['length_ratio']} "
                         f"| {a['answered']}/{a['items']} | {a['empty']} | {a['truncated']} | {a['sec']} |")
        if bs.get("diff"):
            lines += ["", "| difference | 95 % | P(better) |", "|---|---|---:|"]
            for k, v in bs["diff"].items():
                lines.append(f"| {k} | {v['ci']} | {v['p_better']} |")
        lines += ["", "| anchoring | hint right | kept | broke | hint wrong | copied | fixed | better | worse |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for arm, s in g["anchoring"].items():
            lines.append(f"| {arm} | {s['hint_right']} | {s['kept']} | {s['broke']} | {s['hint_wrong']} "
                         f"| {s['copied']} | {s['fixed']} | {s['better']} | {s['worse']} |")
        lines.append("")
    return "\n".join(lines)


# ── the run ──────────────────────────────────────────────────────────────────

def resolve(spec: dict, models: dict[str, dict], levels: Iterable[str]) -> Arm:
    """The set's models against /models: present, and the Qwen at the level it reads."""
    for k in ("kraken", "trocr", "qwen", "qwen_page"):
        mid = spec.get(k)
        if mid and mid not in models:
            raise PlanError(f"set {spec['name']}: {k} '{mid}' is not on the gateway")
    for k, eng in (("kraken", "kraken"), ("trocr", "trocr"), ("qwen", "vllm"), ("qwen_page", "vllm")):
        mid = spec.get(k)
        if mid and models[mid].get("engine") != eng:
            raise PlanError(f"set {spec['name']}: {k} '{mid}' is a {models[mid].get('engine')} model, not {eng}")
    if models[spec["qwen"]].get("level") != "line":
        raise PlanError(f"set {spec['name']}: qwen '{spec['qwen']}' is level "
                        f"{models[spec['qwen']].get('level')}, the line arms need a line model")
    page = models.get(spec.get("qwen_page") or "")
    if page is not None and page.get("level") != "page":
        raise PlanError(f"set {spec['name']}: qwen_page '{spec['qwen_page']}' is not a page model")
    return Arm(spec["kraken"], spec["trocr"], models[spec["qwen"]], page)


def run(args, gateway_factory=None, log=print) -> int:
    plan_path = Path(args.plan).resolve()
    sets = load_plan(plan_path)
    if args.sets:
        wanted = set(args.sets.split(","))
        sets = [s for s in sets if s["name"] in wanted]
        if not sets:
            raise PlanError(f"no set named {sorted(wanted)} in the plan")
    levels = LEVELS if args.level == "both" else (args.level,)
    chosen = {s["name"]: spread(load_set(s, plan_path.parent), args.max_pages) for s in sets}
    for s in sets:
        ps = chosen[s["name"]]
        years = sorted(p.year for p in ps if p.year is not None)
        log(f"{s['name']}: {len(ps)} pages, {len({p.doc for p in ps})} documents, "
            f"{sum(len(p.lines) for p in ps)} lines"
            + (f", years {years[0]}–{years[-1]}" if years else "")
            + (f", {sum(p.skipped_lines for p in ps)} transcribed lines without a box" if any(
                p.skipped_lines for p in ps) else ""))
        if not ps:
            raise PlanError(f"set {s['name']}: no pages after the filter")
    def levels_of(name: str) -> tuple[str, ...]:
        """A set without any line geometry — a TEI edition — has no line level:
        there is nothing to crop and nothing to hand kraken as given lines."""
        if any(p.lines for p in chosen[name]):
            return levels
        return tuple(lv for lv in levels if lv != "line")

    for s in sets:
        if "line" in levels and "line" not in levels_of(s["name"]):
            log(f"{s['name']}: no line has coordinates (an edition without geometry) — "
                "page level only")
    if args.dry_run:
        return 0

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(out / "readings.jsonl")
    if gateway_factory is None:
        import config
        gw = Gateway(args.gateway or config.ATR_GATEWAY_URL, config.ATR_API_KEY)
    else:
        gw = gateway_factory()
    models = gw.models()
    resolved = {s["name"]: resolve(s, models, levels) for s in sets}

    if not args.report_only:
        reader = Reader(gw, store, workers=args.workers, log=log)
        for s in sets:                    # set by set: one Qwen resident at a time
            arms = resolved[s["name"]]
            for level in levels_of(s["name"]):
                for i, page in enumerate(chosen[s["name"]], 1):
                    log(f"{s['name']} {level} {i}/{len(chosen[s['name']])} {page.key}")
                    (reader.line_level if level == "line" else reader.page_level)(page, arms)

    groups: dict[tuple[str, str], list[Item]] = {}
    for s in sets:
        for level in levels_of(s["name"]):
            groups[(s["name"], level)] = [it for page in chosen[s["name"]]
                                          for it in items_for(page, level, resolved[s["name"]], store)]
    meta = {"plan": str(plan_path), "hint_version": HINT_VERSION, "levels": list(levels),
            "max_pages": args.max_pages, "pixels": PIXELS,
            "sets": {s["name"]: {k: s.get(k) for k in ("kraken", "trocr", "qwen", "qwen_page", "years")}
                     for s in sets},
            "pages": {n: [p.key for p in ps] for n, ps in chosen.items()}}
    rep = report(groups, meta)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "report.md").write_text(markdown(rep), encoding="utf-8")
    log(f"report: {out / 'report.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    doc = __doc__ or ""
    ap = argparse.ArgumentParser(description=doc.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=doc.split("\n\n", 1)[1])
    ap.add_argument("plan", help="the plan JSON (see above)")
    ap.add_argument("--out", required=True, help="run directory (readings.jsonl, report.*)")
    ap.add_argument("--level", choices=("line", "page", "both"), default="both")
    ap.add_argument("--sets", help="comma-separated set names (default: all)")
    ap.add_argument("--max-pages", type=int, default=None,
                    help="pages per set, spread over documents (default: all)")
    ap.add_argument("--workers", type=int, default=4, help="parallel Qwen calls per page")
    ap.add_argument("--gateway", help="gateway URL (default: ATR_GATEWAY_URL)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be read; no network")
    ap.add_argument("--report-only", action="store_true", help="score what readings.jsonl holds")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except PlanError as exc:
        print(f"third_reading: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
