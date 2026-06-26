"""
eval/harness.py

Evaluation harness for Agent A HTR output.
Compares transcriptions against ground truth and computes CER + WER.

Usage:
  python -m eval.harness [--ground-truth DIR] [--transcriptions DIR] [--output FILE]

Ground truth format (per document):
  data/ground_truth/<doc_id>.txt  (plain text, same as Agent A output)
"""

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

import config
from eval.metrics import cer, wer, format_report, levenshtein

GT_DIR = config.DATA_DIR / "ground_truth"


def evaluate_doc(doc_id: str, gt_dir: Path = GT_DIR, hyp_dir: Path = config.TRANSCRIPTIONS_DIR) -> dict:
    """
    Compute CER and WER for one document.
    Returns dict with doc_id, cer, wer, gt_len, hyp_len, errors.
    """
    gt_path = gt_dir / f"{doc_id}.txt"
    hyp_path = hyp_dir / f"{doc_id}.txt"

    if not gt_path.exists():
        logger.warning(f"[Eval] Ground truth not found: {gt_path}")
        return None
    if not hyp_path.exists():
        logger.warning(f"[Eval] Transcription not found: {hyp_path}")
        return None

    gt = gt_path.read_text(encoding="utf-8")
    hyp = hyp_path.read_text(encoding="utf-8")

    doc_cer = cer(gt, hyp)
    doc_wer = wer(gt, hyp)
    errors = levenshtein(gt, hyp)

    return {
        "doc_id": doc_id,
        "cer": doc_cer,
        "wer": doc_wer,
        "gt_len": len(gt),
        "hyp_len": len(hyp),
        "errors": errors,
    }


def evaluate_all(gt_dir: Path = GT_DIR, hyp_dir: Path = config.TRANSCRIPTIONS_DIR) -> list[dict]:
    """Evaluate all documents that have ground truth files."""
    results = []
    for gt_path in gt_dir.glob("*.txt"):
        doc_id = gt_path.stem
        result = evaluate_doc(doc_id, gt_dir, hyp_dir)
        if result:
            results.append(result)

    results.sort(key=lambda r: r["doc_id"])
    return results


def main():
    parser = argparse.ArgumentParser(description="AH HTR Evaluation Harness")
    parser.add_argument(
        "--gt", "--ground-truth", dest="gt_dir", type=Path, default=GT_DIR,
        help="Ground truth directory (default: data/ground_truth/)"
    )
    parser.add_argument(
        "--hyp", "--transcriptions", dest="hyp_dir", type=Path, default=config.TRANSCRIPTIONS_DIR,
        help="Hypothesis/transcription directory (default: data/transcriptions/)"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Output JSON path (default: data/outputs/eval_results.json)"
    )
    parser.add_argument(
        "--format", choices=["md", "json", "both"], default="both",
        help="Output format"
    )
    args = parser.parse_args()

    logger.info(f"[Eval] Ground truth: {args.gt_dir}")
    logger.info(f"[Eval] Hypotheses:  {args.hyp_dir}")

    results = evaluate_all(args.gt_dir, args.hyp_dir)

    if not results:
        logger.warning("[Eval] No matching document pairs found.")
        logger.info(f"[Eval] Put ground truth files in: {GT_DIR}")
        logger.info(f"[Eval] Named as: <doc_id>.txt")
        sys.exit(0)

    # JSON output
    output_dir = config.OUTPUTS_DIR / "eval"
    output_dir.mkdir(parents=True, exist_ok=True)

    out_json = args.output or (output_dir / "eval_results.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # Markdown report
    out_md = output_dir / "eval_report.md"
    report_md = format_report(results)
    out_md.write_text(report_md, encoding="utf-8")

    logger.info(f"[Eval] Results: {out_json}")
    logger.info(f"[Eval] Report:  {out_md}")

    if args.format in ("md", "both"):
        print(report_md)
    if args.format in ("json", "both"):
        print(json.dumps(results, indent=2, ensure_ascii=False))

    # Exit with non-zero if any CER > 0.2
    bad = [r for r in results if r["cer"] > 0.2]
    if bad:
        logger.warning(f"[Eval] {len(bad)} document(s) with CER > 0.2")
        sys.exit(1)


if __name__ == "__main__":
    main()