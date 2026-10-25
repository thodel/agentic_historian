"""pagexml.py — a PAGE XML (2019-07-15) per page from the gateway's line geometry.

The geometry is the line model's: ``/recognize`` returns, per line, a ``bbox``
``[x0, y0, x1, y1]`` and — when the segmenter gives one — a ``baseline``
``[[x, y], …]`` in the pixel space of the image it was sent. The image sent is
the Transkribus page image, so the coordinates land where Transkribus draws
them; if a copy was downscaled before sending, ``scale`` multiplies everything
back up. A line without any geometry cannot be placed on the page and is
reported rather than invented.

Regions come from the segmenter's region ids on each line; a line without one
goes to a single main region. Region polygons are the hull of their lines,
because that is all the geometry there is.

Text is whatever the caller decided: this module never chooses a reading.
"""

from __future__ import annotations

import json
import statistics
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

#: Transkribus' own namespace. It writes 2013-07-15 and its server answered a
#: 2019-07-15 document with an instant 500 (15.10.2026, three pages, 30 ms each):
#: the parser is bound to this schema, so this is what goes back. ReadingOrder is
#: carried the way Transkribus carries it, in each element's ``custom``.
NS = "http://schema.primaresearch.org/PAGE/gts/pagecontent/2013-07-15"


@dataclass
class LineGeom:
    """One placed line: integer pixel coordinates inside the page."""

    index: int
    text: str
    bbox: tuple[int, int, int, int]
    baseline: Optional[list[tuple[int, int]]]
    region: str
    custom: dict = field(default_factory=dict)


def _clip(v: float, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, round(v))))


def _points(pts: Iterable[tuple[int, int]]) -> str:
    return " ".join(f"{x},{y}" for x, y in pts)


def _rect(b: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = b
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _custom(d: dict) -> str:
    """Transkribus' ``custom`` syntax: ``key {k:v; k:v;}`` groups.

    Its parser splits on braces, semicolons **and colons**, so none of the three
    may appear in a value: ``llm:A`` becomes ``llm-A``.
    """
    parts = []
    for group, kv in d.items():
        inner = " ".join(
            f"{k}:{str(v).replace('{', '(').replace('}', ')').replace(';', ',').replace(':', '-')};"
            for k, v in kv.items())
        parts.append(f"{group} {{{inner}}}")
    return " ".join(parts)


def place_lines(lines: list[dict], width: int, height: int, *,
                scale: float = 1.0) -> tuple[list[LineGeom], list[dict]]:
    """Gateway lines → placed lines; the second list is what could not be placed.

    ``lines`` carry ``text``, ``bbox``, ``baseline``, ``regions`` and ``order``
    as the gateway sends them; ``text`` may already have been replaced by the
    adjudicated reading, and ``custom`` (a dict of groups) is carried through.
    """
    placed: list[LineGeom] = []
    dropped: list[dict] = []
    heights = []
    for ln in lines:
        b = ln.get("bbox")
        if isinstance(b, list) and len(b) == 4:
            heights.append((float(b[3]) - float(b[1])) * scale)
    line_h = statistics.median(heights) if heights else max(20.0, height / 60)

    for i, ln in enumerate(lines):
        text = (ln.get("text") or "").strip()
        b = ln.get("bbox")
        bl = ln.get("baseline")
        baseline = None
        if isinstance(bl, list) and bl and isinstance(bl[0], (list, tuple)) and len(bl[0]) == 2:
            baseline = [(_clip(x * scale, 0, width - 1), _clip(y * scale, 0, height - 1))
                        for x, y in bl]
        if isinstance(b, list) and len(b) == 4:
            x0, y0, x1, y1 = (float(v) * scale for v in b)
        elif baseline:
            xs = [x for x, _ in baseline]
            ys = [y for _, y in baseline]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys) - 0.8 * line_h, max(ys) + 0.3 * line_h
        else:
            dropped.append({"index": i, "text": text, "reason": "no bbox and no baseline"})
            continue
        bbox = (_clip(x0, 0, width - 1), _clip(y0, 0, height - 1),
                _clip(x1, 0, width - 1), _clip(y1, 0, height - 1))
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            dropped.append({"index": i, "text": text, "reason": f"degenerate bbox {bbox}"})
            continue
        regions = ln.get("regions") or []
        region = str(regions[0]) if regions else "r_main"
        placed.append(LineGeom(index=int(ln.get("order", i)), text=text, bbox=bbox,
                               baseline=baseline, region=region,
                               custom=dict(ln.get("custom") or {})))
    return placed, dropped


def build_pagexml(image_filename: str, width: int, height: int, lines: list[dict], *,
                  scale: float = 1.0, creator: str = "agentic_historian ruemlang",
                  comments: str = "") -> tuple[str, list[dict]]:
    """The XML as a string, and the lines that could not be placed."""
    placed, dropped = place_lines(lines, width, height, scale=scale)

    # Regions in the order their first line appears; lines inside in page order.
    by_region: dict[str, list[LineGeom]] = {}
    for lg in sorted(placed, key=lambda g: (g.index, g.bbox[1])):
        by_region.setdefault(lg.region, []).append(lg)
    region_order = sorted(by_region, key=lambda r: (min(g.bbox[1] for g in by_region[r]),
                                                    min(g.bbox[0] for g in by_region[r])))

    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}PcGts")
    meta = ET.SubElement(root, f"{{{NS}}}Metadata")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    ET.SubElement(meta, f"{{{NS}}}Creator").text = creator
    ET.SubElement(meta, f"{{{NS}}}Created").text = now
    ET.SubElement(meta, f"{{{NS}}}LastChange").text = now
    if comments:
        ET.SubElement(meta, f"{{{NS}}}Comments").text = comments
    page = ET.SubElement(root, f"{{{NS}}}Page", {
        "imageFilename": image_filename, "imageWidth": str(int(width)),
        "imageHeight": str(int(height))})
    for ri, rid in enumerate(region_order):
        group = by_region[rid]
        region_id = f"r_{ri + 1}"
        pad = 6
        rb = (_clip(min(g.bbox[0] for g in group) - pad, 0, width - 1),
              _clip(min(g.bbox[1] for g in group) - pad, 0, height - 1),
              _clip(max(g.bbox[2] for g in group) + pad, 0, width - 1),
              _clip(max(g.bbox[3] for g in group) + pad, 0, height - 1))
        region = ET.SubElement(page, f"{{{NS}}}TextRegion", {
            "id": region_id, "custom": _custom({"readingOrder": {"index": ri}})})
        ET.SubElement(region, f"{{{NS}}}Coords", {"points": _points(_rect(rb))})
        for li, g in enumerate(group):
            custom = {"readingOrder": {"index": li}}
            custom.update(g.custom)
            line = ET.SubElement(region, f"{{{NS}}}TextLine", {
                "id": f"{region_id}_tl_{li + 1}", "custom": _custom(custom)})
            ET.SubElement(line, f"{{{NS}}}Coords", {"points": _points(_rect(g.bbox))})
            if g.baseline and len(g.baseline) >= 2:
                ET.SubElement(line, f"{{{NS}}}Baseline", {"points": _points(g.baseline)})
            else:
                y = g.bbox[3] - max(1, (g.bbox[3] - g.bbox[1]) // 4)
                ET.SubElement(line, f"{{{NS}}}Baseline",
                              {"points": _points([(g.bbox[0], y), (g.bbox[2], y)])})
            eq = ET.SubElement(line, f"{{{NS}}}TextEquiv")
            ET.SubElement(eq, f"{{{NS}}}Unicode").text = g.text
        eq = ET.SubElement(region, f"{{{NS}}}TextEquiv")
        ET.SubElement(eq, f"{{{NS}}}Unicode").text = "\n".join(g.text for g in group)

    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml + "\n", dropped


def parse_lines(xml_text: str) -> list[dict]:
    """The lines of a PAGE XML back as ``{text, bbox, baseline, points, region}``
    — for tests and for reading what Transkribus already holds. Document order,
    which is the order :func:`replace_line_texts` writes back in."""
    root = ET.fromstring(xml_text)
    ns = {"p": root.tag[1:root.tag.index("}")]}
    region_of = {id(tl): reg.get("id") for reg in root.iter(f"{{{ns['p']}}}TextRegion")
                 for tl in reg.findall("p:TextLine", ns)}
    out = []
    for tl in root.iterfind(".//p:TextLine", ns):
        coords = tl.find("p:Coords", ns)
        pts = [tuple(int(float(v)) for v in p.split(",")) for p in
               (coords.get("points", "").split() if coords is not None else [])]
        bl = tl.find("p:Baseline", ns)
        bpts = [tuple(int(float(v)) for v in p.split(",")) for p in
                (bl.get("points", "").split() if bl is not None else [])]
        uni = tl.find("p:TextEquiv/p:Unicode", ns)
        bbox = None
        if pts:
            bbox = [min(x for x, _ in pts), min(y for _, y in pts),
                    max(x for x, _ in pts), max(y for _, y in pts)]
        out.append({"id": tl.get("id"), "text": (uni.text or "") if uni is not None else "",
                    "bbox": bbox, "baseline": bpts or None, "custom": tl.get("custom"),
                    "points": pts or None, "region": region_of.get(id(tl))})
    return out


def replace_line_texts(xml_text: str, texts: list[Optional[str]]) -> tuple[str, int]:
    """The same PAGE XML with each ``TextLine``'s text replaced, in document order.

    For a page read on its own lines (``segmented_by: given``): polygons,
    baselines, regions, ids and reading order stay exactly as Transkribus drew
    them, and only the words change. ``texts[i]`` is line ``i``; ``None`` leaves
    that line as it was. ``Word`` children go — their text would contradict the
    new line — and a region's own ``TextEquiv`` is rebuilt from its lines.
    Returns the document and how many lines were rewritten.
    """
    root = ET.fromstring(xml_text)
    uri = root.tag[1:root.tag.index("}")]
    ET.register_namespace("", uri)
    q = lambda tag: f"{{{uri}}}{tag}"                                    # noqa: E731
    lines = list(root.iter(q("TextLine")))
    if len(texts) != len(lines):
        raise ValueError(f"{len(texts)} reading(s) for {len(lines)} line(s)")
    changed = 0
    for tl, text in zip(lines, texts):
        if text is None:
            continue
        for word in tl.findall(q("Word")):
            tl.remove(word)
        te = tl.find(q("TextEquiv"))
        if te is None:
            te = ET.SubElement(tl, q("TextEquiv"))
        for child in list(te):
            te.remove(child)
        ET.SubElement(te, q("Unicode")).text = text
        changed += 1
    for region in root.iter(q("TextRegion")):
        te = region.find(q("TextEquiv"))
        if te is None:
            continue
        joined = "\n".join((tl.findtext(f"{q('TextEquiv')}/{q('Unicode')}") or "")
                            for tl in region.findall(q("TextLine")))
        for child in list(te):
            te.remove(child)
        ET.SubElement(te, q("Unicode")).text = joined
    body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body, changed


def provenance_comment(info: dict) -> str:
    """A short JSON for ``<Comments>``: which run, which models, which arbiter."""
    return json.dumps(info, ensure_ascii=False, sort_keys=True)
