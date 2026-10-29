#!/usr/bin/env python3
"""fusion_on_lineset.py — #416 on a line draw that several readers have read.

    python -m eval.fusion_on_lineset <draw dir> --arm vlm=t4b-vlm.raw.jsonl \\
        --arm trocr-full=t4b-trocr-full.raw.jsonl --arm trocr-100p=t4b-trocr-100p.raw.jsonl \\
        [--exclude-pages pages.txt] [--lead 0.02] [--out report.md]

The draw is a ``*.jsonl`` of lines (``image``, ``text``, ``page``), the arms are
the readers' raw outputs on the same lines (``image`` + ``prediction`` or
``raw``) — the shape training-atr-models keeps under
``eval_sets/<set>/`` on the share. The lines of one page are joined, in draw
order, into one text per reader and one reference, and the page goes through
:func:`eval.vlm_bench.fusion_effect` exactly as a bench page would: the vote
without arbitration, cut by how far the best reader led.

Why pages and not lines: the pipeline fuses a page's text in one call, and a
page is the unit a reader is chosen for. Fusing line by line asks the same
question of the same vote and (measured 29.10.2026 on the T4 draw) answers it
the same way; it is not a second method.

**The order check.** A vote should not care in which order the candidates
arrive. ``fuse`` does: a column where every candidate reads differently falls
to ``_most_backed``, which takes the *first* — and that is the pivot, the
longest candidate, or on equal length whichever came first in the list. The
report fuses every page a second time with the candidates reversed and prints
both pooled CERs. The gap between them is the share of the result that is
list order, not evidence.

Needs no network and no model. Nothing here gates the pipeline (#416's guard).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.metrics import cer as _cer  # noqa: E402
from eval.vlm_bench import (  # noqa: E402
    COMPARABLE_LEAD,
    FusionEffect,
    _stratum_table,
    fusion_effect,
)


def load_draw(path: Path) -> list[dict]:
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def load_arm(path: Path) -> dict[str, str]:
    """image → reading. Accepts ``prediction`` (TrOCR's evaluate) or ``raw``
    (the VLM rescorer); a missing line is an error, not an empty reading — the
    draw says every arm read every line, and an arm that did not is a
    different measurement."""
    out = {}
    for row in map(json.loads, open(path, encoding="utf-8")):
        text = row["prediction"] if "prediction" in row else row["raw"]
        out[row["image"]] = text
    return out


def page_texts(draw: list[dict], arms: dict[str, dict[str, str]],
               exclude: Optional[set] = None) -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, int]]:
    """(readings per arm per page, reference per page, lines per page)."""
    ref_lines: dict[str, list[str]] = defaultdict(list)
    arm_lines: dict[str, dict[str, list[str]]] = {a: defaultdict(list) for a in arms}
    for row in draw:
        page = Path(row["page"]).stem
        if exclude and page in exclude:
            continue
        key = row["image"]
        missing = [a for a, texts in arms.items() if key not in texts]
        if missing:
            raise SystemExit(f"{key}: not read by {', '.join(missing)}")
        ref_lines[page].append(row["text"])
        for a, texts in arms.items():
            arm_lines[a][page].append(texts[key])
    readings = {a: {p: "\n".join(lines) for p, lines in pages.items()} for a, pages in arm_lines.items()}
    references = {p: "\n".join(lines) for p, lines in ref_lines.items()}
    return readings, references, {p: len(lines) for p, lines in ref_lines.items()}


def _fuse_in_order(order: list[str]):
    """A fuse_fn for fusion_effect that hands the candidates over in ``order``
    (fusion_effect's own hands them over in dict order, i.e. ``--arm`` order)."""
    from fusion import fuse

    def fuse_fn(texts: dict[str, str]) -> str:
        recognitions = [{"engine": name, "model_id": name, "text": texts[name]}
                        for name in order if name in texts]
        return fuse(recognitions, arbitrate=False).text
    return fuse_fn


def pooled_cer(readings: dict[str, str], references: dict[str, str]) -> float:
    """Edits over characters across all pages — the number TROCR.md reports,
    as opposed to the medians fusion_effect reports. Raw, case and punctuation
    kept, so a single arm reproduces its published figure."""
    errors = chars = 0
    for key, ref in references.items():
        hyp = readings.get(key, "")
        errors += round(_cer(ref, hyp, ignore_case=False, ignore_whitespace=False,
                             ignore_punctuation=False) * len(ref))
        chars += len(ref)
    return errors / chars if chars else 0.0


def fused_pages(readings: dict[str, dict[str, str]], references: dict[str, str],
                order: list[str]) -> dict[str, str]:
    fuse_fn = _fuse_in_order(order)
    out = {}
    for key in references:
        texts = {a: readings[a][key] for a in order if key in readings[a] and readings[a][key].strip()}
        if len(texts) >= 2:
            out[key] = fuse_fn(texts)
    return out


def report(draw_name: str, arms: list[str], effect: FusionEffect, pooled: dict[str, float],
           pooled_fused: float, pooled_reversed: float, pooled_best_page: float,
           n_lines: int, excluded: int) -> str:
    lines = [f"# Fusion on `{draw_name}` — {effect.pages} pages, {n_lines} lines, "
             f"readers: {', '.join(f'`{a}`' for a in arms)}", ""]
    if excluded:
        lines += [f"_{excluded} pages excluded (a reader trained on them)._", ""]
    lines += ["## Pooled CER (edits / characters, raw)", "",
              "| reading | CER |", "|---|---:|"]
    for a in arms:
        lines.append(f"| `{a}` | {pooled[a]:.4f} |")
    lines += [f"| best single reader **per page** (oracle) | {pooled_best_page:.4f} |",
              f"| **fused, vote, candidates in the order above** | **{pooled_fused:.4f}** |",
              f"| fused, vote, candidates reversed | {pooled_reversed:.4f} |", "",
              f"_The two fused rows differ by {abs(pooled_fused - pooled_reversed) * 100:.2f} points. "
              "A vote has no order; this one does, because a column with no majority "
              "falls to the first candidate._", "",
              "## By kind of field (medians over pages, the harness CER)", ""]
    lines += _stratum_table(effect.strata,
                            f"cut at a lead of {effect.comparable_lead:.0%} CER between the best "
                            "reader and the next")
    lines += ["", "## By number of candidates", ""]
    lines += _stratum_table(effect.by_count)
    lines += ["", f"Over all pages: fusion won on {effect.fusion_wins} of {effect.pages} "
              f"({effect.win_share:.0%}); median fused {effect.median_fused_cer:.1%} "
              f"against median best single {effect.median_best_single_cer:.1%}."]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draw_dir", type=Path)
    ap.add_argument("--draw", default="t4b_draw.jsonl", help="the lines, relative to draw_dir")
    ap.add_argument("--arm", action="append", required=True, metavar="NAME=FILE",
                    help="a reader's raw output, relative to draw_dir; order is the candidate order")
    ap.add_argument("--exclude-pages", type=Path, help="page stems to leave out, one per line")
    ap.add_argument("--lead", type=float, default=COMPARABLE_LEAD)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)

    names, files = zip(*(a.split("=", 1) for a in args.arm))
    arms = {n: load_arm(args.draw_dir / f) for n, f in zip(names, files)}
    draw = load_draw(args.draw_dir / args.draw)
    exclude = set(args.exclude_pages.read_text().split()) if args.exclude_pages else set()
    readings, references, lines_per_page = page_texts(draw, arms, exclude)
    order = list(names)

    effect = fusion_effect(readings, references, fuse_fn=_fuse_in_order(order),
                           comparable_lead=args.lead)
    fused = fused_pages(readings, references, order)
    reversed_ = fused_pages(readings, references, order[::-1])
    pooled = {a: pooled_cer(readings[a], references) for a in order}
    best_page = {}
    for key, ref in references.items():
        best_page[key] = min((readings[a][key] for a in order), key=lambda t: _cer(
            ref, t, ignore_case=False, ignore_whitespace=False, ignore_punctuation=False))
    text = report(args.draw, order, effect, pooled, pooled_cer(fused, references),
                  pooled_cer(reversed_, references), pooled_cer(best_page, references),
                  sum(lines_per_page.values()), len(exclude & {Path(r["page"]).stem for r in draw}))
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
