"""
hand_consistency.py — is a volume one hand, and where does it change? (#773)

Today that judgement comes from the VLM, which sees a page or a few. This spike
asks whether data we already have can say it: the page images and their lines,
every page against every other.

After Ferilli & Piscopo (CEUR Vol-4304, paper 1), who compared 21 instances of
one ligature all against all (210 pairs) by the radial profile of its outline —
256 rays from the centroid, normalised, with ±5° rotation tolerance — and read
three things off the matrix:

* **centrality:** each instance's mean distance to all others; the smallest is
  the most typical instance, found in the data rather than chosen;
* **outliers:** mean + 2 standard deviations;
* **drift:** block means along the volume. A comparison against one fixed
  reference did not show the rise at the end.

Their preprocessing carries over: a global Otsu threshold is enough under even
lighting; no dewarping, because it distorts outlines (rotation is tolerated in
the comparison instead); about 50 px letter height is enough.

What is measured per page
-------------------------
The page is binarised (Otsu), its lines found from the row profile — or taken
from a PAGE XML when there is one — and per line:

* **x-height** (the core band of the row profile), and the line pitch in
  x-heights;
* **slant**: the shear angle that makes the column profile sharpest;
* **stroke width**: the median horizontal ink run, in x-heights;
* **ink density** inside the core band;
* the **radial profile** of each connected component of letter size, 256 rays.

A page is the median of its lines' scalars and the mean of its components'
profiles. Two pages differ by their z-scored scalars and by their mean profiles
compared with ±5° tolerance; each part is scaled by its own median, so neither
outweighs the other by its units.

What a number here is not
-------------------------
A spike. Whether these distances separate hands is exactly what ``validate``
measures against volumes whose catalogue gives the number of hands (e-codices,
#779). Until that table exists nothing here goes into a description (#777, #778).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

#: Rays of the radial profile, as in the paper.
RAYS = 256
#: ±5° rotation tolerance, in rays.
SHIFT = round(5 / 360 * RAYS)
#: Pages are read at this width at most; IIIF serves ≤1600 px anyway.
MAX_WIDTH = 1600
#: Components per page that enter the mean profile (evenly drawn, deterministic).
MAX_COMPONENTS = 200
#: Slant angles tried, in degrees.
SLANTS = tuple(range(-30, 31, 2))
#: Blocks along the volume for drift, as in the paper.
BLOCKS = 3


# ── binarisation and lines ───────────────────────────────────────────────────

def otsu(gray: np.ndarray) -> int:
    """The global Otsu threshold of an 8-bit grey image: values ``<=`` it are the
    dark class (ink)."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(float)
    total = hist.sum()
    if not total:
        return 128
    w0 = np.cumsum(hist)
    mu = np.cumsum(hist * np.arange(256))
    w1 = total - w0
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * w0 / total - mu) ** 2 / (w0 * w1)
    between[~np.isfinite(between)] = 0
    return int(np.argmax(between))


def load_ink(path: str | Path) -> np.ndarray:
    """The page as a boolean ink mask (ink = at or below Otsu's threshold)."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("L")
        if im.width > MAX_WIDTH:
            im = im.resize((MAX_WIDTH, max(1, round(im.height * MAX_WIDTH / im.width))))
        g = np.asarray(im, dtype=np.uint8)
    return g <= otsu(g)


def find_lines(ink: np.ndarray, min_height: int = 6) -> list[tuple[int, int, int, int]]:
    """``(x0, y0, x1, y1)`` of text lines from the row profile.

    Rows with ink above a fifth of the profile's upper level are text; runs of
    them shorter than ``min_height`` are noise. The horizontal extent is where
    the band has ink. Crude on purpose: a PAGE XML's lines are better and are
    used when given (``lines_from_pagexml``).
    """
    if not ink.size:
        return []
    rows = ink.mean(axis=1)
    k = max(1, ink.shape[0] // 200)
    smooth = np.convolve(rows, np.ones(2 * k + 1) / (2 * k + 1), mode="same")
    level = np.percentile(smooth[smooth > 0], 90) if (smooth > 0).any() else 0
    on = smooth > 0.2 * level if level else np.zeros_like(smooth, dtype=bool)
    lines = []
    y = 0
    while y < len(on):
        if on[y]:
            y0 = y
            while y < len(on) and on[y]:
                y += 1
            if y - y0 >= min_height:
                cols = np.flatnonzero(ink[y0:y].any(axis=0))
                if cols.size:
                    lines.append((int(cols[0]), y0, int(cols[-1]) + 1, y))
        y += 1
    return lines


def lines_from_pagexml(xml_text: str, scale: float = 1.0) -> list[tuple[int, int, int, int]]:
    """Line boxes from a PAGE XML, scaled to the image as read (``scale`` = read/original)."""
    from ruemlang import pagexml
    out = []
    for ln in pagexml.parse_lines(xml_text):
        b = ln.get("bbox")
        if b:
            out.append(tuple(int(round(v * scale)) for v in b))
    return out


# ── per line ─────────────────────────────────────────────────────────────────

def _runs(row: np.ndarray) -> np.ndarray:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], row.astype(np.int8), [0]))))
    return edges[1::2] - edges[0::2]


def x_height(line: np.ndarray) -> tuple[int, int, int]:
    """``(xh, top, bottom)``: the longest run of rows at ≥ half the peak density."""
    rows = line.mean(axis=1)
    if not rows.any():
        return 0, 0, 0
    core = rows >= 0.5 * rows.max()
    best, start = (0, 0, 0), None
    for i, c in enumerate(np.append(core, False)):
        if c and start is None:
            start = i
        elif not c and start is not None:
            if i - start > best[0]:
                best = (i - start, start, i)
            start = None
    return best


def slant(line: np.ndarray, max_points: int = 20000) -> float:
    """The shear angle (degrees, positive = leaning right) that sharpens the column profile."""
    ys, xs = np.nonzero(line)
    if len(xs) < 20:
        return 0.0
    if len(xs) > max_points:
        idx = np.linspace(0, len(xs) - 1, max_points).astype(int)
        ys, xs = ys[idx], xs[idx]
    yc = line.shape[0] / 2
    best, best_a = -1.0, 0.0
    for a in SLANTS:
        shifted = xs + (ys - yc) * math.tan(math.radians(a))
        h = np.bincount(np.round(shifted - shifted.min()).astype(int))
        s = float((h.astype(float) ** 2).sum())
        if s > best:
            best, best_a = s, float(a)
    return best_a


def components(mask: np.ndarray) -> list[np.ndarray]:
    """8-connected components as arrays of ``(y, x)``, by union-find over row runs."""
    runs = []                                   # (y, x0, x1)
    for y in range(mask.shape[0]):
        edges = np.flatnonzero(np.diff(np.concatenate(([0], mask[y].astype(np.int8), [0]))))
        for x0, x1 in zip(edges[0::2], edges[1::2]):
            runs.append((y, int(x0), int(x1)))
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_row: dict[int, list[int]] = {}
    for i, (y, _, _) in enumerate(runs):
        by_row.setdefault(y, []).append(i)
    for i, (y, x0, x1) in enumerate(runs):
        for j in by_row.get(y - 1, []):
            _, a0, a1 = runs[j]
            if a0 <= x1 and x0 <= a1:           # touching incl. diagonals (x1 exclusive +1)
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[ri] = rj
    groups: dict[int, list[int]] = {}
    for i in range(len(runs)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():
        pts = [(runs[i][0], x) for i in members for x in range(runs[i][1], runs[i][2])]
        out.append(np.asarray(pts, dtype=np.int32))
    return out


def radial_profile(points: np.ndarray, rays: int = RAYS) -> Optional[np.ndarray]:
    """The outline as seen from the centroid: the farthest ink per ray, normalised to max 1.

    Empty rays (a thin stroke seen edge-on) take the circular interpolation of
    their neighbours, so a profile never has holes.
    """
    if len(points) < 8:
        return None
    c = points.mean(axis=0)
    d = points - c
    ang = np.arctan2(d[:, 0], d[:, 1])
    rad = np.hypot(d[:, 0], d[:, 1])
    idx = ((ang + math.pi) / (2 * math.pi) * rays).astype(int) % rays
    prof = np.zeros(rays)
    np.maximum.at(prof, idx, rad)
    if prof.max() <= 0:
        return None
    # A letter's rim has fewer pixels than there are rays: a ray that falls
    # between two rim pixels would read short. The outline is the farthest ink
    # within ±1 ray.
    prof = np.maximum(prof, np.maximum(np.roll(prof, 1), np.roll(prof, -1)))
    filled = np.flatnonzero(prof > 0)
    if len(filled) < rays:
        prof = np.interp(np.arange(rays), filled, prof[filled], period=rays)
    return prof / prof.max()


@dataclass
class LineFeatures:
    xh: float
    slant: float
    stroke: float
    density: float
    profiles: list = field(default_factory=list)


def line_features(line: np.ndarray, max_components: int = 40) -> Optional[LineFeatures]:
    """Features of one line crop (boolean ink), or None when it is not a text line."""
    xh, top, bottom = x_height(line)
    if xh < 4:
        return None
    core = line[top:bottom]
    runs = np.concatenate([_runs(r) for r in core]) if core.size else np.array([])
    stroke = float(np.median(runs)) / xh if runs.size else 0.0
    comps = [c for c in components(line)
             if 0.5 * xh <= (c[:, 0].max() - c[:, 0].min() + 1) <= 3 * xh and len(c) >= 0.15 * xh * xh]
    if len(comps) > max_components:
        comps = [comps[i] for i in np.linspace(0, len(comps) - 1, max_components).astype(int)]
    profiles = [p for p in (radial_profile(c) for c in comps) if p is not None]
    return LineFeatures(xh=float(xh), slant=slant(line), stroke=stroke,
                        density=float(core.mean()), profiles=profiles)


# ── per page ─────────────────────────────────────────────────────────────────

#: The scalar part of a page, in this order.
SCALARS = ("slant", "stroke", "density", "xh_rel", "pitch")


@dataclass
class PageFeatures:
    page: str
    lines: int
    scalars: list            # SCALARS
    profile: list            # mean radial profile, RAYS values
    components: int


def page_features(ink: np.ndarray, lines: Optional[Sequence[tuple]] = None,
                  name: str = "") -> Optional[PageFeatures]:
    """One page's features, or None when it has fewer than two usable lines."""
    boxes = list(lines) if lines is not None else find_lines(ink)
    feats, centres = [], []
    for x0, y0, x1, y1 in boxes:
        crop = ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
        if crop.size == 0:
            continue
        lf = line_features(crop)
        if lf:
            feats.append(lf)
            centres.append((y0 + y1) / 2)
    if len(feats) < 2:
        return None
    xh = float(np.median([f.xh for f in feats]))
    gaps = np.diff(sorted(centres))
    pitch = float(np.median(gaps)) / xh if len(gaps) else 0.0
    profiles = [p for f in feats for p in f.profiles]
    if len(profiles) > MAX_COMPONENTS:
        profiles = [profiles[i] for i in np.linspace(0, len(profiles) - 1, MAX_COMPONENTS).astype(int)]
    mean_profile = np.mean(profiles, axis=0) if profiles else np.zeros(RAYS)
    scalars = [float(np.median([f.slant for f in feats])),
               float(np.median([f.stroke for f in feats])),
               float(np.median([f.density for f in feats])),
               xh / max(1, ink.shape[1]),
               pitch]
    return PageFeatures(page=name, lines=len(feats), scalars=scalars,
                        profile=mean_profile.round(5).tolist(), components=len(profiles))


def image_page_features(path: str | Path, pagexml_text: Optional[str] = None,
                        original_width: Optional[int] = None) -> Optional[PageFeatures]:
    """``page_features`` of an image file, with its PAGE XML lines when given."""
    ink = load_ink(path)
    lines = None
    if pagexml_text:
        scale = ink.shape[1] / original_width if original_width else 1.0
        lines = lines_from_pagexml(pagexml_text, scale)
    return page_features(ink, lines, name=Path(path).name)


# ── all against all ──────────────────────────────────────────────────────────

def profile_distance(a: Sequence[float], b: Sequence[float], shift: int = SHIFT) -> float:
    """Mean absolute difference of two radial profiles, the best over ±``shift`` rays."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    return float(min(np.abs(a - np.roll(b, s)).mean() for s in range(-shift, shift + 1)))


def distance_matrix(pages: Sequence[PageFeatures]) -> np.ndarray:
    """Page against page: z-scored scalars and tolerant profile distance, each scaled
    by its own median off-diagonal value and averaged."""
    n = len(pages)
    if n < 2:
        return np.zeros((n, n))
    x = np.asarray([p.scalars for p in pages], float)
    std = x.std(axis=0)
    z = (x - x.mean(axis=0)) / np.where(std > 0, std, 1.0)
    ds = np.sqrt(((z[:, None, :] - z[None, :, :]) ** 2).mean(axis=2))
    dp = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            dp[i, j] = dp[j, i] = profile_distance(pages[i].profile, pages[j].profile)
    off = ~np.eye(n, dtype=bool)

    def scaled(m):
        med = float(np.median(m[off]))
        return m / med if med > 0 else m

    return (scaled(ds) + scaled(dp)) / 2


@dataclass
class VolumeReport:
    pages: list
    matrix: list
    centrality: list
    typical: Optional[str]
    outliers: list
    outlier_threshold: Optional[float]
    blocks: list                 # per block: pages, mean centrality, mean within-block distance
    changepoint: Optional[dict]  # best single split: index, page, ratio cross/within
    silhouette2: Optional[float]


def _changepoint(d: np.ndarray, pages: list[str]) -> Optional[dict]:
    """The split into a front and a back part that makes the parts most unlike
    each other: ratio = mean distance across / mean distance within. A hand that
    changes once at page t shows as a high ratio at t."""
    n = len(d)
    best = None
    for t in range(2, n - 1):
        a, b = slice(0, t), slice(t, n)
        within = np.concatenate([d[a, a][np.triu_indices(t, 1)], d[b, b][np.triu_indices(n - t, 1)]])
        cross = d[a, b].ravel()
        if not within.size or not cross.size or within.mean() <= 0:
            continue
        ratio = float(cross.mean() / within.mean())
        if best is None or ratio > best["ratio"]:
            best = {"index": t, "page": pages[t], "ratio": round(ratio, 4)}
    return best


def _silhouette2(d: np.ndarray) -> Optional[float]:
    """Mean silhouette of the best 2-medoid split; ~0 for one homogeneous hand."""
    n = len(d)
    if n < 4:
        return None
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            lab = (d[:, j] < d[:, i]).astype(int)
            if lab.sum() in (0, n):
                continue
            cost = np.minimum(d[:, i], d[:, j]).sum()
            if best is None or cost < best[0]:
                best = (cost, lab)
    if best is None:
        return None
    lab = best[1]
    s = []
    for k in range(n):
        own = lab == lab[k]
        own[k] = False
        if not own.any():
            s.append(0.0)
            continue
        a = d[k, own].mean()
        b = d[k, lab != lab[k]].mean()
        s.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return round(float(np.mean(s)), 4)


def analyse(pages: Sequence[PageFeatures], blocks: int = BLOCKS) -> VolumeReport:
    """Matrix, centrality, the most typical page, outliers, block means, changepoint."""
    names = [p.page for p in pages]
    d = distance_matrix(pages)
    n = len(pages)
    if n < 2:
        return VolumeReport(names, d.tolist(), [0.0] * n, names[0] if names else None,
                            [], None, [], None, None)
    cent = d.sum(axis=1) / (n - 1)
    typical = names[int(np.argmin(cent))]            # ties: the earlier page
    thr = float(cent.mean() + 2 * cent.std())
    outliers = [names[i] for i in range(n) if cent[i] > thr]
    out_blocks = []
    edges = np.linspace(0, n, min(blocks, n) + 1).round().astype(int)
    for b0, b1 in zip(edges[:-1], edges[1:]):
        if b1 <= b0:
            continue
        sub = d[b0:b1, b0:b1]
        within = sub[np.triu_indices(b1 - b0, 1)]
        out_blocks.append({"pages": [names[b0], names[b1 - 1]],
                           "centrality": round(float(cent[b0:b1].mean()), 4),
                           "within": round(float(within.mean()), 4) if within.size else None})
    return VolumeReport(pages=names, matrix=d.round(4).tolist(),
                        centrality=cent.round(4).tolist(), typical=typical,
                        outliers=outliers, outlier_threshold=round(thr, 4),
                        blocks=out_blocks, changepoint=_changepoint(d, names),
                        silhouette2=_silhouette2(d))


def analyse_images(paths: Iterable[str | Path], blocks: int = BLOCKS) -> tuple[VolumeReport, list[str]]:
    """``analyse`` over image files; returns the report and the pages that gave no features."""
    pages, skipped = [], []
    for p in paths:
        try:
            f = image_page_features(p)
        except Exception:  # noqa: BLE001 — an unreadable page is named, not fatal
            f = None
        if f:
            pages.append(f)
        else:
            skipped.append(Path(p).name)
    return analyse(pages, blocks), skipped


# ── the hypothesis for the historian (#777) ──────────────────────────────────
#
# Ferilli & Piscopo read a rising block mean as a reason to *check* for a resumed
# campaign, not as a finding. So this is an entry a historian reads, marked
# ``quelle = berechnet`` and ``unsicher`` (#772), and it never sets ``Schreiber``.
# The thresholds are anchors, not measurements: docs/HAND_CONSISTENCY.md says
# where each comes from and how ``validate`` calibrates them against volumes
# whose catalogue gives the number of hands.

#: Fewer pages than this and three blocks hold two pages each — no hypothesis.
HINT_MIN_PAGES = 8
#: At most this many pages of a volume are measured, at even intervals (#771's spread).
HINT_MAX_PAGES = 30
#: Changepoint: mean distance across the split over mean distance within. 1.0 is
#: no structure; the synthetic two-hand volume gives > 1.5, one hand ≈ 1.1.
HINT_CHANGE_RATIO = 1.25
#: Silhouette of the best 2-medoid split; ~0 for one homogeneous hand.
HINT_SILHOUETTE = 0.15
#: Drift: last block's mean centrality over the first, as a fraction — the
#: "rise towards the end" of the paper.
HINT_DRIFT = 0.10
#: The key of the computed element in ``source_json``. A separate element, not a
#: sentence inside ``Weitere_Hinweise``: #772 gives every element one ``quelle``,
#: and the VLM's hints and a computed hypothesis are two sources.
HINT_ELEMENT = "Schriftverlauf"


def hypothesis(report: VolumeReport, *, total_pages: Optional[int] = None,
               min_pages: int = HINT_MIN_PAGES, change_ratio: float = HINT_CHANGE_RATIO,
               silhouette: float = HINT_SILHOUETTE, drift: float = HINT_DRIFT) -> Optional[dict]:
    """The ``Schriftverlauf`` element for ``source_json`` when the volume shows a
    change or a drift of the hand — or ``None``, which is the common case."""
    n = len(report.pages)
    if n < min_pages:
        return None
    parts: list[str] = []
    cp = report.changepoint or {}
    sil = report.silhouette2 or 0.0
    change = bool(cp) and cp.get("ratio", 0) >= change_ratio and sil >= silhouette
    if change:
        parts.append(f"Schriftbild ändert sich ab {cp['page']} (Trennmass {cp['ratio']:.2f}, "
                     f"Silhouette {sil:.2f})")
    rel = None
    if len(report.blocks) >= 2 and report.blocks[0]["centrality"]:
        rel = (report.blocks[-1]["centrality"] - report.blocks[0]["centrality"]) / report.blocks[0]["centrality"]
        if rel >= drift:
            parts.append(f"Blockmittel steigen zum Ende (+{rel * 100:.0f} %)")
    if not parts:
        return None
    if report.outliers:
        shown = ", ".join(report.outliers[:3]) + (", …" if len(report.outliers) > 3 else "")
        parts.append(f"{shown} weicht stark ab" if len(report.outliers) == 1 else f"{shown} weichen stark ab")
    text = "; ".join(parts) + ". Mögliche neue Hand oder Schreibkampagne. Nicht geprüft."
    return {
        "wert": text, "unsicher": True, "quelle": "berechnet",
        "basis": {"seiten": n, "von": total_pages or n, "methode": "hand_consistency",
                  "schwellen": {"min_pages": min_pages, "change_ratio": change_ratio,
                                "silhouette": silhouette, "drift": drift},
                  "changepoint": cp or None, "silhouette2": report.silhouette2,
                  "drift": round(rel, 4) if rel is not None else None,
                  "blocks": report.blocks, "ausreisser": list(report.outliers)},
    }


def describe_volume(paths: Sequence[str | Path], *, max_pages: int = HINT_MAX_PAGES,
                    blocks: int = BLOCKS) -> tuple[VolumeReport, list[str], Optional[dict]]:
    """Up to ``max_pages`` of a volume at even intervals, the report, the pages
    that gave no features, and the hypothesis (or ``None``)."""
    import page_sample
    paths = list(paths)
    chosen = [paths[i] for i in page_sample.spread(len(paths), min(max_pages, len(paths)))]
    rep, skipped = analyse_images(chosen, blocks)
    return rep, skipped, hypothesis(rep, total_pages=len(paths))


# ── validation against catalogued hands (#779 volumes) ───────────────────────

def validate(bench_dir: Path, pages: int = 30, get=None) -> list[dict]:
    """For each e-codices volume fetched by ``eval.ecodices_bench fetch``: up to
    ``pages`` page images at even intervals (cached as ``page-NNNN.jpg``, the
    bench's own names), the report, and the catalogue's number of hands."""
    from eval import ecodices_bench as eb
    import page_sample

    rows = []
    for vdir in sorted(p for p in bench_dir.iterdir() if (p / "tei.xml").exists()):
        ref = eb.parse_tei((vdir / "tei.xml").read_bytes(), vdir.name)
        urls = eb.canvases(json.loads((vdir / "manifest.json").read_text(encoding="utf-8")))
        files = []
        for i in page_sample.spread(len(urls), min(pages, len(urls))):
            path = vdir / f"page-{i:04d}.jpg"
            if not path.exists():
                if get is None:
                    continue
                try:
                    path.write_bytes(get(urls[i]))
                except Exception:  # noqa: BLE001, S112 — a page the server will not give is skipped
                    continue
            files.append(path)
        rep, skipped = analyse_images(files)
        (vdir / "hand_consistency.json").write_text(
            json.dumps({**asdict(rep), "skipped": skipped}, ensure_ascii=False), encoding="utf-8")
        hint = hypothesis(rep, total_pages=len(urls))
        rows.append({"id": vdir.name, "hands": ref.hands, "pages": len(rep.pages),
                     "skipped": len(skipped), "silhouette2": rep.silhouette2,
                     "changepoint_ratio": (rep.changepoint or {}).get("ratio"),
                     "changepoint_page": (rep.changepoint or {}).get("page"),
                     "outliers": len(rep.outliers), "typical": rep.typical,
                     "hint": bool(hint)})
    return rows


def format_validation(rows: list[dict]) -> str:
    """The table #773 asks for, and the comparison that decides it."""
    lines = ["| volume | hands (catalogue) | pages | silhouette (2) | changepoint ratio | at | outliers | hint (#777) |",
             "|---|---:|---:|---:|---:|---|---:|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['hands'] if r['hands'] is not None else '—'} | {r['pages']} "
                     f"| {r['silhouette2'] if r['silhouette2'] is not None else '—'} "
                     f"| {r['changepoint_ratio'] if r['changepoint_ratio'] is not None else '—'} "
                     f"| {r['changepoint_page'] or '—'} | {r['outliers']} "
                     f"| {'fires' if r.get('hint') else '—'} |")

    def mean(key, pred):
        v = [r[key] for r in rows if pred(r) and r[key] is not None]
        return (round(sum(v) / len(v), 4), len(v)) if v else (None, 0)

    one = lambda r: r["hands"] == 1                                     # noqa: E731
    many = lambda r: r["hands"] is not None and r["hands"] > 1         # noqa: E731
    lines += ["", "| | one hand | several hands |", "|---|---:|---:|"]
    for key in ("silhouette2", "changepoint_ratio"):
        (m1, n1), (m2, n2) = mean(key, one), mean(key, many)
        lines.append(f"| mean {key} | {m1} (n={n1}) | {m2} (n={n2}) |")
    fired_many = sum(1 for r in rows if many(r) and r.get("hint"))
    fired_one = sum(1 for r in rows if one(r) and r.get("hint"))
    n_many, n_one = sum(1 for r in rows if many(r)), sum(1 for r in rows if one(r))
    lines += ["", f"The #777 hypothesis fires on {fired_many} of {n_many} several-hands volumes "
              f"and on {fired_one} of {n_one} one-hand volumes (it should: all of the first, "
              f"none of the second; the catalogue does not say *where* a hand changes, so "
              f"the block is not checked here)."]
    lines += ["", "The measure separates hands only if the several-hands column is clearly "
              "higher on both rows, over at least five volumes with a known count. If it is "
              "not, #773 ends with that finding and #777/#778 fall away."]
    return "\n".join(lines)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Consistency of the hand, all pages against all (#773)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyse", help="one volume: page images in a directory, in name order")
    a.add_argument("images", type=Path)
    a.add_argument("--out", type=Path, default=None, help="JSON report (default: stdout summary only)")
    a.add_argument("--blocks", type=int, default=BLOCKS)
    v = sub.add_parser("validate", help="e-codices volumes fetched by eval.ecodices_bench (network for pages)")
    v.add_argument("bench_dir", type=Path)
    v.add_argument("--pages", type=int, default=30)
    v.add_argument("--offline", action="store_true", help="only page images already on disk")
    args = ap.parse_args(argv)

    if args.cmd == "analyse":
        files = sorted(p for p in args.images.iterdir()
                       if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"))
        rep, skipped = analyse_images(files, args.blocks)
        if args.out:
            args.out.write_text(json.dumps({**asdict(rep), "skipped": skipped}, ensure_ascii=False),
                                encoding="utf-8")
        print(f"{len(rep.pages)} page(s) measured, {len(skipped)} skipped; typical: {rep.typical}")
        print(f"outliers (> {rep.outlier_threshold}): {', '.join(rep.outliers) or '—'}")
        for b in rep.blocks:
            print(f"  block {b['pages'][0]}–{b['pages'][1]}: centrality {b['centrality']}, within {b['within']}")
        print(f"changepoint: {rep.changepoint}; silhouette(2): {rep.silhouette2}")
        return 0
    from eval.ecodices_bench import _http_get
    rows = validate(args.bench_dir, args.pages, get=None if args.offline else _http_get)
    table = format_validation(rows)
    (args.bench_dir / "hand_consistency.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
