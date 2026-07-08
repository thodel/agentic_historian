"""
eval/harness.py

CER evaluation harness — produces a deterministic CER table so
"does fusion beat the best single engine?" is a number, not a vibe.

Input format (per document, fixture or live):
    {
        "doc_id":  str,
        "reference": str,          # ground-truth text
        "recognitions": {           # engine_name → raw output text
            "vlm":  "...",         # one entry per engine
            "kraken": "...",
            "trocr": "...",
        },
        "fused": str,               # fused output (P2-4), may be absent initially
    }

Run from repo root:
    python -m eval.harness --fixtures eval/fixtures --output eval/results
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from eval.metrics import cer

# ─── Public API ──────────────────────────────────────────────────────────────


def cer_table(
    recognitions: dict[str, str],
    fused: Optional[str],
    reference: str,
    *,
    ignore_case: bool = True,
    ignore_whitespace: bool = True,
    ignore_punctuation: bool = True,
    abbrev_fold: bool = False,
) -> dict:
    """
    Compute CER for each engine, for fused, and for the best single engine.

    Returns
    -------
    {
        "engines": {
            <engine_name>: { "cer": float, "text": str }
        },
        "fused":    { "cer": float, "text": str } | None,   # absent if fused is None
        "best":     { "name": str, "cer": float },          # lowest CER of any engine
        "fusion_beats_best": bool | None,   # True/False or None if no fused text
        "reference_len": int,
    }
    """
    opts = dict(
        ignore_case=ignore_case,
        ignore_whitespace=ignore_whitespace,
        ignore_punctuation=ignore_punctuation,
        abbrev_fold=abbrev_fold,
    )

    engine_results: dict[str, dict] = {}
    best_cer = float("inf")
    best_name = None

    for name, text in recognitions.items():
        c = cer(reference, text, **opts)
        engine_results[name] = {"cer": round(c, 4), "text": text}
        if c < best_cer:
            best_cer = c
            best_name = name

    fused_result: Optional[dict] = None
    fusion_beats_best: Optional[bool] = None
    if fused is not None:
        fc = cer(reference, fused, **opts)
        fused_result = {"cer": round(fc, 4), "text": fused}
        fusion_beats_best = fc < best_cer

    return {
        "engines": engine_results,
        "fused": fused_result,
        "best": {"name": best_name, "cer": round(best_cer, 4)},
        "fusion_beats_best": fusion_beats_best,
        "reference_len": len(reference),
    }


def format_cer_table(doc_results: list[dict]) -> str:
    """
    Build a markdown CER comparison table.

    doc_results: list of dicts with keys doc_id, engines, fused, best, reference_len
    """
    if not doc_results:
        return "No results."

    # Collect all engine names across all docs for column headers
    all_engines: set[str] = set()
    for r in doc_results:
        all_engines.update(r.get("engines", {}).keys())
    engine_cols = sorted(all_engines)

    header = [
        "| Doc-ID | " + " | ".join(engine_cols) + " | Fused | Best Engine | Beat Best? |",
        "|---|" + "|".join("---" for _ in range(len(engine_cols) + 3)) + "|",
    ]

    rows = []
    for r in doc_results:
        engine_vals = []
        for eng in engine_cols:
            entry = r.get("engines", {}).get(eng)
            if entry is not None:
                engine_vals.append(f"{entry['cer']:.3f}")
            else:
                engine_vals.append("—")

        fused_entry = r.get("fused")
        fused_str = f"{fused_entry['cer']:.3f}" if fused_entry else "—"

        best = r.get("best", {})
        best_str = f"{best.get('name','?')} ({best.get('cer',0):.3f})"

        beat = r.get("fusion_beats_best")
        beat_str = {True: "✅", False: "❌", None: "—"}[beat]

        rows.append(
            f"| {r.get('doc_id','?')} | "
            + " | ".join(engine_vals) + f" | {fused_str} | {best_str} | {beat_str} |"
        )

    # Aggregate summary row — weighted average CER per engine
    n = len(doc_results)
    summary_parts = ["| **Avg CER**"]
    for eng in engine_cols:
        vals = [r.get("engines", {}).get(eng, {}).get("cer", 0) for r in doc_results]
        summary_parts.append(f"**{sum(vals)/n:.3f}**")
    fused_vals = [r.get("fused", {}).get("cer", 0) for r in doc_results if r.get("fused")]
    if fused_vals:
        summary_parts.append(f"**{sum(fused_vals)/len(fused_vals):.3f}**")
    else:
        summary_parts.append("—")
    summary_parts.append("| — | — |")
    header.append("|".join(summary_parts))

    return "\n".join([header[0], header[1]] + rows + [header[2] if len(header) > 2 else ""])


def run_table(doc_results: list[dict]) -> str:
    """Alias for format_cer_table — backwards-compatible entry point."""
    return format_cer_table(doc_results)


# ─── Fixture loader ──────────────────────────────────────────────────────────


def load_fixtures(fixtures_dir: Path) -> list[dict]:
    """
    Load all .json fixture files from fixtures_dir.

    Expected structure per file:
    {
        "doc_id":  str,
        "reference": str,
        "recognitions": { <engine>: <text> },
        "fused": str | null
    }
    """
    results = []
    for p in sorted(fixtures_dir.glob("*.json")):
        if p.name.startswith("golden"):
            continue  # skip golden files — they are test artefacts, not fixtures
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("doc_id", p.stem)
            results.append(data)
        except Exception as e:
            print(f"[Eval] Skipping {p}: {e}", file=sys.stderr)
    return results


# ─── Main CLI ────────────────────────────────────────────────────────────────


def evaluate_fixtures(
    fixtures_dir: Path,
    output_dir: Optional[Path] = None,
    *,
    ignore_case: bool = True,
    ignore_whitespace: bool = True,
    ignore_punctuation: bool = True,
    abbrev_fold: bool = False,
) -> list[dict]:
    """
    Run cer_table() over every fixture in fixtures_dir.
    Optionally write JSON + Markdown results to output_dir.
    """
    fixtures = load_fixtures(fixtures_dir)
    if not fixtures:
        print(f"[Eval] No fixture files found in {fixtures_dir}", file=sys.stderr)
        return []

    opts = dict(
        ignore_case=ignore_case,
        ignore_whitespace=ignore_whitespace,
        ignore_punctuation=ignore_punctuation,
        abbrev_fold=abbrev_fold,
    )

    results = []
    for fix in fixtures:
        doc_id = fix.get("doc_id", "unknown")
        result = cer_table(
            recognitions=fix.get("recognitions", {}),
            fused=fix.get("fused"),
            reference=fix.get("reference", ""),
            **opts,
        )
        result["doc_id"] = doc_id
        result["reference"] = fix.get("reference", "")
        results.append(result)

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        out_json = output_dir / "cer_table.json"
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        out_md = output_dir / "cer_table.md"
        out_md.write_text(format_cer_table(results), encoding="utf-8")

        print(f"[Eval] Results: {out_json}")
        print(f"[Eval] Table:   {out_md}")

    return results


def main():
    parser = argparse.ArgumentParser(description="AH CER Evaluation Harness")
    parser.add_argument(
        "--fixtures", "-f", type=Path, required=True,
        help="Directory containing per-document .json fixture files",
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output directory for cer_table.json and cer_table.md",
    )
    parser.add_argument(
        "--no-abbrev-fold", action="store_true",
        help="Disable abbreviation folding (default: enabled)",
    )
    parser.add_argument(
        "--case-sensitive", action="store_true",
        help="Make comparison case-sensitive (default: case-insensitive)",
    )
    args = parser.parse_args()

    results = evaluate_fixtures(
        fixtures_dir=args.fixtures,
        output_dir=args.output,
        ignore_case=not args.case_sensitive,
        ignore_whitespace=True,
        ignore_punctuation=True,
        abbrev_fold=not args.no_abbrev_fold,
    )

    if not results:
        print("[Eval] No results.", file=sys.stderr)
        sys.exit(1)

    print(format_cer_table(results))


if __name__ == "__main__":
    main()