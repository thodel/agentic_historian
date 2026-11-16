"""
eval/veto_report.py — the scorer veto in production, read back (#875).

Since 14.11.2026 every fusion with a scorer writes ``fusion_scores`` (bits/char
and z of every usable reading on the page, under one scorer) and
``fusion_vetoed`` (the readings more than ``FUSION_VETO_MARGIN`` bits/char
above the page's best) into ``a_meta`` of the pipeline record (#491 L3). #491
calibrated the margin on eight arms of **one** model; this reads the margin
against the engines production actually runs:

* how often a page has a scorer at all, and how often the veto fires;
* per engine and per reading key (``engine/model_id``): scored, vetoed, rate;
* the gap to the page's best reading, as a histogram around the margin — a
  gap piled up just under the margin says the margin decides more pages than
  it seems to;
* which scorer cell answered;
* each reading's character distance to the fused text. That is **agreement,
  not quality** (#326, #336): there is no reference here, and a vetoed reading
  is far from a fusion it took no part in by construction. It says how far
  apart the engines are, never which one is right.

Records written before the per-reading keys (#875) name a reading by its
engine alone; two readings of one engine on such a page were scored as one.
They are counted and reported apart, not mixed in.

    python -m eval.veto_report data/outputs                   # tei: <doc>_pipeline.json
    python -m eval.veto_report ../agentic-historian-outputs/docs --json veto.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: Gap to the page's best (bits/char): the bins of the histogram. The margin is
#: drawn into the report, not into the bins, so a changed margin reads off the
#: same table.
BINS = (0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
#: How close under the margin counts as "knapp" — a reading that a margin
#: smaller by this much would have vetoed.
NEAR = 0.5


def find_records(paths: list[Path]) -> list[Path]:
    """Every pipeline record under ``paths``: ``<doc>_pipeline.json`` (tei's
    outputs) and ``pipeline.json`` (the published catalogue, one per doc)."""
    out: list[Path] = []
    for p in paths:
        if p.is_file():
            out.append(p)
        elif p.is_dir():
            out += sorted(p.rglob("*_pipeline.json")) + sorted(p.rglob("pipeline.json"))
    return sorted(set(out))


def _engine(key: str) -> str:
    return key.split("/", 1)[0].split("#", 1)[0]


def _bin(gap: float) -> str:
    for lo, hi in itertools.pairwise(BINS):
        if gap <= hi:
            return "0" if gap == 0 else f"{lo:g}–{hi:g}"
    return f">{BINS[-1]:g}"


def _distances(record: dict) -> dict[str, float]:
    """Character distance of each reading to the fused text, by the same
    per-reading keys the veto writes. Empty when the record lacks either."""
    fused = (record.get("transcription") or "").strip()
    recs = record.get("recognitions") or []
    if not fused or not recs:
        return {}
    from eval.metrics import cer
    from fusion import _usable_with_keys
    return {key: cer(text, fused) for key, _r, text in _usable_with_keys(recs)}


def analyse(records: list[dict], *, margin: float) -> dict:
    """The table, from pipeline records as dicts."""
    pages = len(records)
    with_scorer = vetoed_pages = legacy_pages = 0
    per_key: dict[str, dict] = defaultdict(lambda: {"scored": 0, "vetoed": 0, "gaps": [],
                                                    "to_fusion": []})
    hist: Counter = Counter()
    hist_vetoed: Counter = Counter()
    cells: Counter = Counter()
    near: list[dict] = []
    strategies: Counter = Counter()
    for rec in records:
        meta = rec.get("a_meta") or {}
        scores = meta.get("fusion_scores") or {}
        if meta.get("fusion_strategy"):
            strategies[meta["fusion_strategy"]] += 1
        if not scores:
            continue
        with_scorer += 1
        if any("/" not in k for k in scores):
            legacy_pages += 1
        vetoed = {v.get("label") for v in meta.get("fusion_vetoed") or []}
        if vetoed:
            vetoed_pages += 1
        best = min(v["bits_per_char"] for v in scores.values())
        dist = _distances(rec)
        for key, v in scores.items():
            gap = round(v["bits_per_char"] - best, 4)
            row = per_key[key]
            row["scored"] += 1
            row["gaps"].append(gap)
            if key in dist:
                row["to_fusion"].append(dist[key])
            cells[v.get("cell") or "?"] += 1
            hist[_bin(gap)] += 1
            if key in vetoed:
                row["vetoed"] += 1
                hist_vetoed[_bin(gap)] += 1
            elif margin - NEAR <= gap <= margin:
                near.append({"doc_id": rec.get("doc_id"), "reading": key, "gap": gap})
    by_engine: dict[str, dict] = defaultdict(lambda: {"scored": 0, "vetoed": 0})
    keys = {}
    for key, row in sorted(per_key.items()):
        e = by_engine[_engine(key)]
        e["scored"] += row["scored"]
        e["vetoed"] += row["vetoed"]
        vg = [g for g in row["gaps"] if g > margin]
        keys[key] = {
            "scored": row["scored"], "vetoed": row["vetoed"],
            "rate": row["vetoed"] / row["scored"],
            "median_gap": statistics.median(row["gaps"]),
            "vetoed_gap_min": min(vg) if vg else None,
            "vetoed_gap_max": max(vg) if vg else None,
            "median_to_fusion": statistics.median(row["to_fusion"]) if row["to_fusion"] else None,
            "legacy": "/" not in key,
        }
    for e in by_engine.values():
        e["rate"] = e["vetoed"] / e["scored"] if e["scored"] else None
    order = ["0"] + [f"{lo:g}–{hi:g}" for lo, hi in itertools.pairwise(BINS)] + [f">{BINS[-1]:g}"]
    return {
        "margin": margin,
        "pages": pages,
        "pages_with_scorer": with_scorer,
        "pages_with_veto": vetoed_pages,
        "pages_legacy_keys": legacy_pages,
        "strategies": dict(strategies),
        "cells": dict(cells),
        "by_engine": dict(sorted(by_engine.items())),
        "by_reading": keys,
        "gap_histogram": {b: {"readings": hist.get(b, 0), "vetoed": hist_vetoed.get(b, 0)}
                          for b in order},
        "near_margin": sorted(near, key=lambda r: -r["gap"]),
    }


def _pct(v) -> str:
    return "–" if v is None else f"{v:.0%}"


def _num(v) -> str:
    return "–" if v is None else f"{v:.2f}"


def render(t: dict) -> str:
    m = t["margin"]
    lines = ["# Das Scorer-Veto in der Produktion (#875)", "",
             (f"- Seiten: **{t['pages']}**, davon mit Scorer: **{t['pages_with_scorer']}**, "
             f"mit Veto: **{t['pages_with_veto']}** "
             f"({_pct(t['pages_with_veto'] / t['pages_with_scorer']) if t['pages_with_scorer'] else '–'}"
             " der Seiten mit Scorer)"),
             f"- Marge: **{m:g}** bits/char über der besten Lesung der Seite",
             "- Zellen: " + (", ".join(f"`{c}` {n}" for c, n in sorted(t["cells"].items()))
                               or "–")]
    if t["pages_legacy_keys"]:
        lines.append(f"- **{t['pages_legacy_keys']} Seite(n) mit alter Aufzeichnung** (Lesung nur "
                     "nach Engine benannt, vor #875): zwei Lesungen einer Engine wurden dort "
                     "als eine bewertet. Ihre Zeilen sind unten markiert.")
    lines += ["", "## Je Engine", "", "| Engine | bewertet | vetoed | Rate |", "|---|---:|---:|---:|"]
    for e, row in t["by_engine"].items():
        lines.append(f"| {e} | {row['scored']} | {row['vetoed']} | {_pct(row['rate'])} |")
    lines += ["", "## Je Lesung (Engine/Modell)", "",
              ("| Lesung | bewertet | vetoed | Rate | Abstand zur Besten (Median) | "
              "vetoed: Abstand min–max | Abstand zur Fusion (Median) |"),
              "|---|---:|---:|---:|---:|---:|---:|"]
    for k, row in t["by_reading"].items():
        rng = ("–" if row["vetoed_gap_min"] is None
               else f"{row['vetoed_gap_min']:.2f}–{row['vetoed_gap_max']:.2f}")
        mark = " (alt)" if row["legacy"] else ""
        lines.append(f"| `{k}`{mark} | {row['scored']} | {row['vetoed']} | {_pct(row['rate'])} | "
                     f"{_num(row['median_gap'])} | {rng} | {_pct(row['median_to_fusion'])} |")
    lines += ["", "## Abstand zur besten Lesung der Seite", "",
              (f"Die Marge {m:g} liegt in der Spalte, die sie enthält; was rechts davon liegt, "
              "fällt."), "", "| Abstand (bits/char) | Lesungen | davon vetoed |", "|---|---:|---:|"]
    for b, row in t["gap_histogram"].items():
        lines.append(f"| {b} | {row['readings']} | {row['vetoed']} |")
    near = t["near_margin"]
    lines += ["", f"## Knapp unter der Marge ({m - NEAR:g}–{m:g})", ""]
    if near:
        lines.append(f"{len(near)} Lesung(en), die eine um {NEAR:g} kleinere Marge "
                     "genommen hätte:")
        lines += [f"- `{r['reading']}` auf `{r['doc_id']}`: +{r['gap']:.2f}" for r in near[:30]]
    else:
        lines.append("Keine.")
    lines += ["", ("**Was diese Zahlen nicht sagen.** Der Abstand zur Fusion ist "
              "Übereinstimmung, keine Qualität: es gibt hier keine Referenz, und eine vetoed "
              "Lesung ist der Fusion, an der sie nicht teilnahm, schon deshalb fern. Ob die "
              "Marge die richtigen Lesungen trifft, sagt nur ein Abgleich mit Ground Truth "
              "(`calibrate_veto_margin.py` in training-atr-models)."), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", type=Path,
                    help="pipeline records, or directories to search for them")
    ap.add_argument("--margin", type=float, default=None,
                    help="bits/char; default FUSION_VETO_MARGIN from config")
    ap.add_argument("--json", type=Path, help="also write the table as JSON")
    args = ap.parse_args(argv)
    margin = args.margin
    if margin is None:
        import config
        margin = float(config.FUSION_VETO_MARGIN)
    files = find_records(args.paths)
    records = []
    for f in files:
        try:
            records.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            print(f"übersprungen: {f} ({e})", file=sys.stderr)
    table = analyse(records, margin=margin)
    print(render(table))
    if args.json:
        args.json.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
