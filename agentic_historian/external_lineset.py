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

def fetch(dataset: str, dest: Path) -> list[Path]:
    """The dataset's parquet shards, downloaded once to ``dest``."""
    from huggingface_hub import snapshot_download
    local = snapshot_download(repo_id=dataset, repo_type="dataset",
                              allow_patterns=["*.parquet", "**/*.parquet"],
                              local_dir=str(dest), token=config.HF_TOKEN or None)
    shards = sorted(Path(local).rglob("*.parquet"))
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


def rows(shards: list[Path], wanted: set[int]) -> Iterator[tuple[int, str, str, bytes]]:
    """``(index, key, reference, image bytes)`` in order, a batch at a time —
    the MCP's jobs run under a 200 MiB memory limit, the set is 220 MB."""
    import pyarrow.parquet as pq
    i = 0
    for shard in shards:
        pf = pq.ParquetFile(shard)
        for batch in pf.iter_batches(batch_size=64, columns=["image", "text", "filename"]):
            for rec in batch.to_pylist():
                if i in wanted:
                    img = rec["image"]
                    data = img.get("bytes") if isinstance(img, dict) else img
                    yield i, str(rec.get("filename") or i), rec.get("text") or "", data
                i += 1


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
    }


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
    shards = fetch(args.dataset, base / args.dataset.replace("/", "__"))
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
    for _i, key, ref, data in rows(shards, wanted):
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
        img.write_bytes(data)
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
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    raise SystemExit(main())
