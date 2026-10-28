"""
external_lineset.py — an external model (Gemini, Argon) on a line-level test set.

The Bern volumes measure an external reader on pages (``ruemlang/pipeline.py
eval``, reader G). A published line benchmark asks the cleaner question — can
it read this script — and has numbers to compare with: the Federal Council
minutes (Hodel & Schoch 2021, doi:10.5281/zenodo.4746342), 2,751 Kurrent lines
1848–1903, mirrored as ``dh-unibe/image-text_federal-minutes-testset`` and
scored on UBELIX for the trainer's adapters (training-atr-models
``docs/EVAL_SETS.md``).

**Comparable on purpose.** The CER is corpus-level, Σ edits / Σ reference
characters, with the trainer's edit direction, ``truncated_cer`` (the answer
clipped to the reference length) and ``length_ratio`` — ``textmetrics.Score``'s
definitions, recomputed here so the number lands in the same table. A line the
API never answered counts as an empty answer in the headline, as a model that
returned nothing would; the CER over answered lines is reported beside it.

**Billed per line.** Nothing runs without ``--sample N`` (evenly spread over
the set) or ``--full``; every model is preflighted against the API's list;
readings are appended to ``readings_<model>.jsonl`` as they come, so a stopped
run resumes where it stood and a line is never paid for twice under the same
settings (prompt digest, thinking level, ceiling).

    python external_lineset.py --model gemini-4-argon --out <run dir> --sample 50
    python external_lineset.py --model gemini-4-argon --out <run dir> --full
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator, Optional

PKG = Path(__file__).resolve().parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from loguru import logger                     # noqa: E402

import config                                 # noqa: E402
import textdiff                               # noqa: E402

DEFAULT_DATASET = "dh-unibe/image-text_federal-minutes-testset"
DEFAULT_PROMPT = "federal_minutes_line.md"


# ── the set ──────────────────────────────────────────────────────────────────

def fetch(dataset: str, dest: Path, project: Optional[str] = None) -> list[Path]:
    """The dataset's parquet shards, downloaded once to ``dest`` — only one
    project's (``data/<split>/<project>/``) when ``project`` is named: the St.
    Gallen Missiven carry a second, 534 MB single-line copy of the same pages."""
    from huggingface_hub import snapshot_download
    patterns = ([f"data/*/{project}/*.parquet"] if project
                else ["*.parquet", "**/*.parquet"])
    local = snapshot_download(repo_id=dataset, repo_type="dataset",
                              allow_patterns=patterns,
                              local_dir=str(dest), token=config.HF_TOKEN or None)
    shards = sorted(p for p in Path(local).rglob("*.parquet")
                    if not project or p.parent.name == project)
    if not shards:
        raise SystemExit(f"no parquet shards in {dataset}")
    return shards


def count(shards: list[Path]) -> int:
    import pyarrow.parquet as pq
    return sum(pq.ParquetFile(s).metadata.num_rows for s in shards)


def chosen(total: int, sample: Optional[int]) -> set[int]:
    """Row indices: all of them, or ``sample`` spread evenly over the set, so a
    sample is not the first shard's first documents."""
    if not sample or sample >= total:
        return set(range(total))
    step = total / sample
    return {int(i * step) for i in range(sample)}


def page_text(xml: str) -> str:
    """A page's reference: its lines' own ``TextEquiv``, one per line, in
    document order — what ``pagexml-hf`` stores as ``xml_content``."""
    from ruemlang import pagexml
    return "\n".join(ln["text"] for ln in pagexml.parse_lines(xml) if ln["text"].strip())


def rows(shards: list[Path], wanted: set[int],
         pages: bool = False) -> Iterator[tuple[int, str, str, bytes]]:
    """``(index, key, reference, image bytes)`` in order, a batch at a time —
    the MCP's jobs run under a 200 MiB memory limit, the line set is 220 MB.
    A line set carries its text in ``text``; a page set (``pages``) only in
    the PAGE XML of ``xml_content``, and its rows are whole scans, so they come
    four at a time."""
    import pyarrow.parquet as pq
    cols = ["image", "xml_content" if pages else "text", "filename"]
    i = 0
    for shard in shards:
        pf = pq.ParquetFile(shard)
        for batch in pf.iter_batches(batch_size=4 if pages else 64, columns=cols):
            for rec in batch.to_pylist():
                if i in wanted:
                    img = rec["image"]
                    data = img.get("bytes") if isinstance(img, dict) else img
                    ref = page_text(rec.get("xml_content") or "") if pages else rec.get("text") or ""
                    yield i, str(rec.get("filename") or i), ref, data
                i += 1


#: Longest side sent for a page. A scan straight from the archive is 20 MB and
#: more; the model reads a 3500 px copy as well, and the batch runner sends
#: working copies of about that size too.
PAGE_MAX_SIDE = 3500


def shrink(data: bytes) -> bytes:
    """A page image no larger than :data:`PAGE_MAX_SIDE`, as JPEG; small ones
    unchanged."""
    import io

    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        if max(im.size) <= PAGE_MAX_SIDE and len(data) < 8_000_000:
            return data
        f = min(1.0, PAGE_MAX_SIDE / max(im.size))
        out = io.BytesIO()
        im.convert("RGB").resize((round(im.width * f), round(im.height * f))).save(out, "JPEG", quality=90)
        return out.getvalue()


# ── scoring: textmetrics.Score's definitions ─────────────────────────────────

def score(pairs: list[tuple[str, str]]) -> dict:
    """Corpus-level, as the trainer reports it: ``cer`` = Σ edits / Σ reference
    chars over ``(hypothesis, reference)``; insertions are reference characters
    the hypothesis never wrote, deletions hypothesis characters the reference
    does not have (kraken's direction, training-atr-models #55)."""
    from rapidfuzz.distance import Levenshtein
    chars = errors = hyp_chars = ins = dels = subs = t_err = 0
    for hyp, ref in pairs:
        chars += len(ref)
        hyp_chars += len(hyp)
        ops = Levenshtein.editops(hyp, ref)
        errors += len(ops)
        for op in ops:
            ins += op.tag == "insert"
            dels += op.tag == "delete"
            subs += op.tag == "replace"
        t_err += Levenshtein.distance(hyp[:len(ref)], ref)
    rate = lambda n: round(n / chars, 4) if chars else None
    return {"samples": len(pairs), "chars": chars, "errors": errors, "cer": rate(errors),
            "truncated_cer": rate(t_err), "length_ratio": rate(hyp_chars),
            "insertions": ins, "deletions": dels, "substitutions": subs}


def _flat(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def item_cers(readings: dict[str, dict], refs: dict[str, str],
              norm=_flat) -> dict[str, float]:
    """CER per answered page (or line), whitespace collapsed by default — the
    pipeline's and ``score_ground_truth``'s page CER, whose median is what
    pages compare by: a corpus rate is dominated by the longest pages."""
    from rapidfuzz.distance import Levenshtein
    out = {}
    for k, r in refs.items():
        if k in readings and norm(r):
            out[k] = Levenshtein.distance(norm(readings[k].get("text", "")), norm(r)) / len(norm(r))
    return out


def page_cers(readings: dict[str, dict], refs: dict[str, str]) -> list[float]:
    return sorted(item_cers(readings, refs).values())


def report(readings: dict[str, dict], refs: dict[str, str]) -> dict:
    """Headline over every chosen line (unanswered = empty), the same over
    answered lines, whitespace-flat, and what went wrong."""
    every = [((readings.get(k) or {}).get("text", "").strip(), r) for k, r in refs.items()]
    answered = [(readings[k].get("text", "").strip(), r) for k, r in refs.items() if k in readings]
    conf: Counter = Counter()
    for h, r in answered:
        conf.update(textdiff.confusions(r, h))
    meta = list(readings.values())
    return {
        "lines": len(refs), "answered": len(answered),
        "all": score(every), "answered_only": score(answered),
        "whitespace_flat": score([(_flat(h), _flat(r)) for h, r in every]),
        "empty_answers": sum(1 for m in meta if not (m.get("text") or "").strip()),
        "finish_reasons": dict(Counter(m.get("finish_reason") or "?" for m in meta)),
        "truncated": sum(1 for m in meta if m.get("truncated")),
        "prompt_tokens": sum(m.get("prompt_tokens") or 0 for m in meta),
        "completion_tokens": sum(m.get("completion_tokens") or 0 for m in meta),
        "confusions": textdiff.show(conf, 20),
        "per_item_cer": _spread(page_cers(readings, refs)),
        # Diagnostic only (textdiff.normalize_convention): reading without notation.
        "convention_normalized": {
            "all": score([(textdiff.normalize_convention(h), textdiff.normalize_convention(r))
                          for h, r in every]),
            "per_item_cer": _spread(sorted(item_cers(
                readings, refs, textdiff.normalize_convention).values()))},
        # The worst items by name: on 28.10. one Missiven page scored 142 %, which
        # is a reference that does not cover the leaf, not a reading.
        "worst": [{"key": k, "cer": round(v, 4)} for k, v in sorted(
            item_cers(readings, refs).items(), key=lambda kv: -kv[1])[:3]],
    }


def _spread(values: list[float]) -> Optional[dict]:
    if not values:
        return None
    import statistics
    p90 = values[min(len(values) - 1, round(0.9 * (len(values) - 1)))]
    return {"n": len(values), "median": round(statistics.median(values), 4),
            "p90": round(p90, 4), "best": round(values[0], 4), "worst": round(values[-1], 4)}


# ── reading ──────────────────────────────────────────────────────────────────

def _load_readings(path: Path, settings: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue                      # a line cut by a kill: read again
            if r.get("settings") == settings:
                out[r["key"]] = r
    return out


def run(args) -> int:
    import external_atr
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if not (args.sample or args.full):
        raise SystemExit("billed per line: give --sample N or --full")
    pf = external_atr.preflight(args.model)
    print(pf.line, flush=True)
    if pf.refused:
        return 2
    base = Path(config.ATR_DATASETS_DIR) if config.ATR_DATASETS_DIR else out.parent / "_datasets"
    shards = fetch(args.dataset, base / args.dataset.replace("/", "__"), args.project)
    total = count(shards)
    wanted = chosen(total, None if args.full else args.sample)
    print(f"{args.dataset}: {total} line(s) in {len(shards)} shard(s); "
          f"{len(wanted)} chosen", flush=True)

    read = external_atr.openai_recogniser(prompt=args.prompt, reasoning=args.reasoning)
    store = out / f"readings_{args.model}.jsonl"
    have = _load_readings(store, read.settings)
    lock = threading.Lock()
    tmp = out / "tmp"
    tmp.mkdir(exist_ok=True)
    refs: dict[str, str] = {}
    todo: list[tuple[str, str, bytes]] = []
    for _i, key, ref, data in rows(shards, wanted, pages=args.pages):
        refs[key] = ref
        if key not in have:
            todo.append((key, ref, data))
    print(f"already read under these settings: {len(have)}; to read: {len(todo)}", flush=True)
    done = [0]
    started = time.monotonic()

    def one(item):
        key, ref, data = item
        img = tmp / (re.sub(r"[^\w.-]", "_", key) or "line")
        if not img.suffix:
            img = img.with_suffix(".jpg")
        img.write_bytes(shrink(data) if args.pages else data)
        try:
            r = read(img, args.model)
        except Exception as exc:              # noqa: BLE001 — recorded, read again next run
            with lock, open(out / f"errors_{args.model}.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "error": f"{type(exc).__name__}: {exc}"}) + "\n")
            return
        finally:
            img.unlink(missing_ok=True)
        rec = {"key": key, "text": r.text, "ref": ref, "finish_reason": getattr(r, "finish_reason", ""),
               "truncated": bool(r.truncated), "prompt_tokens": r.prompt_tokens,
               "completion_tokens": r.completion_tokens, "settings": r.settings,
               "version": r.service_version}
        with lock:
            with open(store, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            have[key] = rec
            done[0] += 1
            if done[0] % 50 == 0 or done[0] == len(todo):
                rate = done[0] / max(1e-6, time.monotonic() - started)
                print(f"read {done[0]}/{len(todo)} ({rate:.1f}/s)", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        list(pool.map(one, todo))
    read.close()

    rep = {"dataset": args.dataset, "model": args.model, "settings": read.settings,
           "sample": None if args.full else args.sample, **report(have, refs)}
    (out / f"report_{args.model}.json").write_text(json.dumps(rep, indent=2, ensure_ascii=False),
                                                  encoding="utf-8")
    a, o, w = rep["all"], rep["answered_only"], rep["whitespace_flat"]
    print(f"\n{args.model} on {rep['lines']} line(s) of {args.dataset} ({read.settings})")
    print(f"  CER {a['cer']}  truncated_cer {a['truncated_cer']}  length_ratio {a['length_ratio']}"
          f"  (all chosen lines; unanswered = empty)")
    print(f"  CER {o['cer']} over the {rep['answered']} answered line(s);"
          f" whitespace-flat {w['cer']}")
    print(f"  empty answers {rep['empty_answers']}, truncated {rep['truncated']},"
          f" finish {rep['finish_reasons']}")
    print(f"  tokens {rep['prompt_tokens']:,} prompt + {rep['completion_tokens']:,} completion")
    if args.pages and rep["per_item_cer"]:
        s = rep["per_item_cer"]
        print(f"  per page (whitespace collapsed): median {s['median']}  p90 {s['p90']}"
              f"  best {s['best']}  worst {s['worst']}  over {s['n']} page(s)")
    c = rep["convention_normalized"]
    print(f"  convention-normalized (diagnostic: u/v, i/j, diacritics, ſ/ß, punctuation, case off):"
          f" CER {c['all']['cer']}"
          + (f", per page median {c['per_item_cer']['median']}" if args.pages and c["per_item_cer"] else ""))
    print("  worst: " + ", ".join(f"{w['key']} {w['cer']}" for w in rep["worst"]))
    print(f"  confusions (GT → model): {rep['confusions']}")
    print(f"report: {out / f'report_{args.model}.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--model", required=True, help="model id at GEMINI_BASE_URL, e.g. gemini-4-argon")
    ap.add_argument("--out", required=True, help="run directory")
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="file under prompts/")
    ap.add_argument("--reasoning", default=None, help="thinking level (default GEMINI_REASONING)")
    ap.add_argument("--sample", type=int, default=None, help="this many lines, spread evenly")
    ap.add_argument("--full", action="store_true", help="every line of the set")
    ap.add_argument("--workers", type=int, default=8, help="lines read at once")
    ap.add_argument("--pages", action="store_true",
                    help="a page set: the reference is the PAGE XML in xml_content, the image a "
                         "whole scan (sent at most 3500 px); report the per-page median CER")
    ap.add_argument("--project", default=None,
                    help="only this project of the dataset (data/<split>/<project>/)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    raise SystemExit(main())
