"""Drop the lines a line-model writes when there was nothing to read.

**What this is for.** kraken's baseline segmenter finds more lines on a page
than the page has. On a hand that loops large — the Berner Disputation copies
are the case that prompted this — a descender or a flourish from the line above
gets its own baseline, and the model is then handed an 80 x 100 pixel speck and
asked to transcribe it. It answers. On the Schöni sample of 29.09.2026, 333 of
989 line boxes (34%) came back with three characters or fewer, and 93% of those
boxes did not overlap any line the segmenter had also found in full.

**Why the cut is on the text and not on the box.** The obvious rule — drop
anything under N pixels — is wrong here, and measurably so. On page 110 the
folio number ``109`` occupies 123 x 105 px and was read *correctly*; a speck
two lines below occupies 118 x 90 px and came back as ``S.``. Geometry cannot
separate those two. What separates them is that one is a plausible reading and
the other is the model's way of writing "nothing here":

    'S.'  181x     'de'   8x     'd.'  4x     '.'  3x
    '1'    19x     'und'  5x     'ist' 4x     's.' 2x
    '1.'   16x     'in'   5x     'v'   4x

``S.`` alone is 181 of 303 short readings. So the rule below drops a bare
punctuation stub — one optional letter or digit followed by a dot, or a dot on
its own — and nothing else. ``109`` and ``406`` survive it, which matters: the
recto folio numerals are the one signal in this manuscript that measured as
reliable (r=0.987 over 180 pages), and an edition that discards them to tidy up
its transcriptions has thrown away the better datum of the two.

Words like ``und`` or ``ist`` on a 400-pixel box are a different failure — the
model under-reading a real line — and this module deliberately leaves them
alone. They are wrong, but they are a reading, and hiding them would make the
run look cleaner than it is.

**It never edits the JSON.** ``<page>.json`` is the record of what the model
actually returned, and stays that way; only the ``.txt`` beside it is rebuilt,
from the JSON, so running this twice is the same as running it once.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: A reading that carries no text: an optional single letter or digit, a dot,
#: nothing else. Deliberately narrow — see the module docstring for why the
#: tempting "drop anything short" and "drop anything small" both cut real text.
STUB = re.compile(r"^\s*[A-Za-z0-9]?\s*\.\s*$")


def is_stub(text: str) -> bool:
    """Whether this reading is the model's way of saying the crop was empty."""
    return bool(STUB.match(text or ""))


def clean_lines(lines: list[dict]) -> tuple[list[str], int]:
    """``(the readings worth keeping, how many were dropped)``."""
    kept, dropped = [], 0
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text:
            continue
        if is_stub(text):
            dropped += 1
            continue
        kept.append(text)
    return kept, dropped


def clean_run(model_dir: Path, *, dry_run: bool = False) -> dict:
    """Rebuild every ``.txt`` in a run's model directory from its ``.json``.

    Returns the counts, because a cleanup nobody can audit is indistinguishable
    from a cleanup that ate something.
    """
    pages = dropped = kept = 0
    touched = []
    for meta in sorted(model_dir.glob("*.json")):
        data = json.loads(meta.read_text(encoding="utf-8"))
        lines = data.get("lines") or []
        if not lines:
            continue
        texts, n = clean_lines(lines)
        pages += 1
        dropped += n
        kept += len(texts)
        if n:
            touched.append((meta.stem, n))
        if not dry_run:
            meta.with_suffix(".txt").write_text("\n".join(texts) + "\n",
                                                encoding="utf-8")
    return {"pages": pages, "lines_kept": kept, "lines_dropped": dropped,
            "pages_touched": touched}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("model_dir", type=Path,
                    help="a run's <run>/<model>/ directory on this machine")
    ap.add_argument("--dry-run", action="store_true",
                    help="count what would go, write nothing")
    args = ap.parse_args(argv)
    if not args.model_dir.is_dir():
        print(f"not a directory: {args.model_dir}", file=sys.stderr)
        return 2
    result = clean_run(args.model_dir, dry_run=args.dry_run)
    share = result["lines_dropped"] / max(1, result["lines_kept"] + result["lines_dropped"])
    print(f"{result['pages']} page(s): kept {result['lines_kept']} line(s), "
          f"dropped {result['lines_dropped']} stub(s) ({share:.0%})"
          + (" — dry run, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
