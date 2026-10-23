#!/usr/bin/env python3
"""Hold out objects of the `lassberg-blb-gt` tree before it becomes a dataset.

The trainer splits pages, not documents, unless a dataset names evaluation
projects — and two pages of one letter are the same hand, paper and ink, so a
page split reports recognition of hands the model trained on. This moves a
seeded tenth of the **objects** (shelfmarks) of every project into a sibling
project ``<project>_eval``, image and PAGE XML together, so every engine the
trainer has can be given ``train_projects`` / ``eval_projects`` and all three
are measured on the same objects.

Two kinds of object go to the evaluation side regardless of the draw:

* the shelfmarks named in ``--force-eval`` (default: those of the HTR project's
  evaluation manifest that the BLB holds — ``K 2911,17``, ``K 2911,107``,
  ``K 2911,110`` — so the measurements made on that split on 22/23 October stay
  comparable with a model trained here);
* nothing else; the overlap with the project's hand-corrected Transkribus pages
  (54 K 2911 objects) is **not** held out, because the point of this dataset is
  to train on Laßberg's hand, and the Transkribus-based evaluations of other
  engines are a different question (see the card, caveat 5).

Idempotent: an object already in ``<project>_eval`` stays there; re-running with
the same seed moves nothing further. ``export.json`` records the split.

    python3 scripts/blb_gt_split.py --tree /mnt/wbkolleg_dh_1/tei/datasets/lassberg-blb-gt --dry-run
    python3 scripts/blb_gt_split.py --tree ...                                        # move
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import sys
from pathlib import Path

PROJECTS = ("lassberg", "korrespondenten", "unbestimmt", "manuskripte", "drucke")
DEFAULT_FORCE = ("K2911-17", "K2911-107", "K2911-110")


def shelf_of(stem: str) -> str:
    """``K2911-47_0001`` → ``K2911-47``."""
    return stem.rsplit("_", 1)[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tree", required=True)
    ap.add_argument("--fraction", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=20261023)
    ap.add_argument("--force-eval", default=",".join(DEFAULT_FORCE),
                    help="comma-separated file-name shelfmarks always held out")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    tree = Path(args.tree)
    force = {s.strip() for s in args.force_eval.split(",") if s.strip()}
    record = {}
    for project in PROJECTS:
        src = tree / project
        if not src.is_dir():
            continue
        dst = tree / f"{project}_eval"
        objects: dict[str, list[Path]] = collections.defaultdict(list)
        for img in src.iterdir():
            if img.is_file() and img.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                objects[shelf_of(img.stem)].append(img)
        already = set()
        if dst.is_dir():
            already = {shelf_of(p.stem) for p in dst.iterdir() if p.is_file() and p.suffix.lower() != ".xml"}
        names = sorted(objects)
        forced = [n for n in names if n in force]
        want = max(1, round(args.fraction * (len(names) + len(already)))) if names else 0
        pool = [n for n in names if n not in force]
        rng_local = random.Random(f"{args.seed}:{project}")
        rng_local.shuffle(pool)
        drawn = pool[:max(0, want - len(already) - len(forced))]
        chosen = sorted(forced + drawn)
        moved_pages = 0
        for shelf in chosen:
            for img in objects[shelf]:
                xml = src / "page" / f"{img.stem}.xml"
                if not args.dry_run:
                    (dst / "page").mkdir(parents=True, exist_ok=True)
                    img.rename(dst / img.name)
                    if xml.is_file():
                        xml.rename(dst / "page" / xml.name)
                moved_pages += 1
        record[project] = {"objects_train": len(names) - len(chosen),
                           "objects_eval": len(chosen) + len(already),
                           "pages_moved_now": moved_pages, "forced": forced,
                           "eval_objects": sorted(set(chosen) | already)}
        print(f"{project:<16} objects {len(names) + len(already):>4} → eval {len(chosen) + len(already):>3} "
              f"({moved_pages} page(s) moved{' — dry run' if args.dry_run else ''})"
              + (f"  forced: {', '.join(forced)}" if forced else ""))
    if not args.dry_run:
        manifest = tree / "export.json"
        data = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
        data["split"] = {"by": "object (shelfmark)", "fraction": args.fraction, "seed": args.seed,
                         "forced_eval": sorted(force), "projects": record}
        data["projects"] = sorted(p.name for p in tree.iterdir() if p.is_dir())
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nexport.json updated: projects {data['projects']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
