"""tree_split.py — hold out whole objects of an export tree before it is uploaded (#711).

The trainer splits **pages** unless a dataset names ``eval_projects``. Two pages
of one letter share hand, paper and ink, so a page split measures recognition
of hands the model has already trained on — a number that looks like a
held-out score and is not one. This moves a seeded share of the **objects** of
every project into a sibling ``<project>_eval``, image and PAGE XML together,
so a dataset can be trained on ``<project>`` and measured on
``<project>_eval`` by every engine alike.

The object is the file-name stem before the ``_NNNN`` page suffix
(``K2911-47_0001`` → ``K2911-47``), the convention of ``oai_gt`` and
``transkribus_gt_export``. A project whose names carry no object is refused,
not split by page under another name.

Rules, each one a mistake that would otherwise be silent:

* **Seeded per project**, so adding a project leaves the draw of the others alone.
* **Idempotent.** What already lies in ``_eval`` counts towards the share and
  stays; a second run with the same arguments moves nothing.
* **Forced objects** go to the evaluation side whatever the draw — the
  shelfmarks an earlier measurement was made on, so that measurement stays
  comparable.
* **Not after an upload.** Once ``export.json`` records an upload, a split
  changes what an already published revision means; refused unless asked.
* **Undoable.** ``undo`` moves every ``_eval`` page back and drops the record.

First used on ``lassberg-blb-gt`` on 2026-10-24 (as a script, which this replaces):
40 of 390 objects, 288 pages, K 2911,17/107/110 forced.
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import hf_export as hf

EVAL_SUFFIX = "_eval"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
DEFAULT_FRACTION = 0.10
DEFAULT_SEED = 20261023


class SplitError(Exception):
    """The tree cannot be split as asked; the message says why."""


@dataclass
class ProjectSplit:
    project: str
    objects: int                       # train + eval, after the split
    eval_objects: list[str]            # all objects on the eval side
    moving: list[str]                  # objects this call moves
    pages_moving: int
    forced: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"objects": self.objects, "eval_objects": len(self.eval_objects),
                "moving": self.moving, "pages_moving": self.pages_moving,
                "forced": self.forced, "eval": self.eval_objects}


def object_of(stem: str) -> Optional[str]:
    m = hf._PAGE_SUFFIX.match(stem)
    return m.group("obj") if m else None


def _images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def source_projects(tree: Path) -> list[str]:
    """Projects that can be split: a ``page/`` folder, not an ``_eval`` side,
    not a cache (``_``/``.``)."""
    return sorted(p.name for p in Path(tree).iterdir()
                  if p.is_dir() and (p / "page").is_dir()
                  and not p.name.endswith(EVAL_SUFFIX)
                  and not p.name.startswith(("_", ".")))


def _by_object(images: Iterable[Path], project: str) -> dict[str, list[Path]]:
    objects: dict[str, list[Path]] = defaultdict(list)
    loose = []
    for img in images:
        obj = object_of(img.stem)
        if obj is None:
            loose.append(img.name)
        else:
            objects[obj].append(img)
    if loose:
        raise SplitError(
            f"{project}: {len(loose)} page(s) whose names carry no object "
            f"(e.g. {', '.join(loose[:3])}) — a split by object needs "
            f"<object>_<NNNN> names; this project would be split by page")
    return objects


def plan(tree: Path, *, fraction: float = DEFAULT_FRACTION, seed: int = DEFAULT_SEED,
         force_eval: Iterable[str] = ()) -> list[ProjectSplit]:
    """What a split would move, project by project. Reads, moves nothing."""
    tree = Path(tree)
    if not tree.is_dir():
        raise SplitError(f"no tree at {tree}")
    if not 0.0 < fraction < 0.5:
        raise SplitError(f"fraction must lie in (0, 0.5), got {fraction}")
    force = {f.strip() for f in force_eval if f and f.strip()}
    out = []
    for project in source_projects(tree):
        train = _by_object(_images(tree / project), project)
        already = set(_by_object(_images(tree / f"{project}{EVAL_SUFFIX}"),
                                 f"{project}{EVAL_SUFFIX}"))
        names = sorted(train)
        total = len(names) + len(already - set(names))
        forced = [n for n in names if n in force]
        want = max(1, round(fraction * total)) if total else 0
        pool = [n for n in names if n not in force]
        random.Random(f"{seed}:{project}").shuffle(pool)
        drawn = pool[:max(0, want - len(already) - len(forced))]
        moving = sorted(forced + drawn)
        out.append(ProjectSplit(
            project=project, objects=total,
            eval_objects=sorted(already | set(moving)), moving=moving,
            pages_moving=sum(len(train[n]) for n in moving), forced=forced))
    if not out:
        raise SplitError(f"{tree} holds no project with a page/ folder to split")
    return out


def _move_object(src: Path, dst: Path, images: list[Path]) -> int:
    (dst / "page").mkdir(parents=True, exist_ok=True)
    for img in images:
        xml = src / "page" / f"{img.stem}.xml"
        img.rename(dst / img.name)
        if xml.is_file():
            xml.rename(dst / "page" / xml.name)
    return len(images)


def split(tree: Path, *, fraction: float = DEFAULT_FRACTION, seed: int = DEFAULT_SEED,
          force_eval: Iterable[str] = (), dry_run: bool = True,
          allow_after_upload: bool = False) -> dict:
    """Plan, and unless ``dry_run`` move, and record the split in ``export.json``."""
    tree = Path(tree)
    force_eval = list(force_eval)
    manifest = hf.read_manifest(tree)
    uploads = manifest.get("uploads") or []
    if uploads and not allow_after_upload and not dry_run:
        raise SplitError(
            f"{tree.name} was uploaded already ({uploads[-1].get('repo_id')}, "
            f"{uploads[-1].get('at')}) — a split now changes what that revision "
            f"means. Split before uploading, or pass allow_after_upload and "
            f"upload again")
    planned = plan(tree, fraction=fraction, seed=seed, force_eval=force_eval)
    moved = 0
    if not dry_run:
        for ps in planned:
            src, dst = tree / ps.project, tree / f"{ps.project}{EVAL_SUFFIX}"
            train = _by_object(_images(src), ps.project)
            for obj in ps.moving:
                moved += _move_object(src, dst, train[obj])
        manifest["split"] = {
            "by": "object (file-name stem before _NNNN)", "fraction": fraction,
            "seed": seed, "forced_eval": sorted(set(force_eval)),
            "projects": {ps.project: {"objects": ps.objects,
                                      "eval_objects": ps.eval_objects}
                         for ps in planned}}
        manifest["projects"] = sorted(p.name for p in tree.iterdir()
                                      if p.is_dir() and (p / "page").is_dir())
        (tree / hf.MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "tree": str(tree), "dry_run": dry_run,
            "fraction": fraction, "seed": seed, "pages_moved": moved,
            "uploaded_before": bool(uploads),
            "projects": {ps.project: ps.as_dict() for ps in planned}}


def undo(tree: Path, *, dry_run: bool = True, allow_after_upload: bool = False) -> dict:
    """Every ``_eval`` page back to its project; the split record dropped."""
    tree = Path(tree)
    manifest = hf.read_manifest(tree)
    if manifest.get("uploads") and not allow_after_upload and not dry_run:
        raise SplitError(f"{tree.name} was uploaded already — undoing the split "
                         f"changes what that revision means")
    back: dict[str, int] = {}
    for side in sorted(p for p in tree.iterdir()
                       if p.is_dir() and p.name.endswith(EVAL_SUFFIX)):
        project = side.name[: -len(EVAL_SUFFIX)]
        images = _images(side)
        back[project] = len(images)
        if not dry_run:
            _move_object(side, tree / project, images)
            for leftover in (side / "page", side):
                try:
                    leftover.rmdir()
                except OSError:
                    pass
    if not dry_run and "split" in manifest:
        manifest.pop("split")
        manifest["projects"] = sorted(p.name for p in tree.iterdir()
                                      if p.is_dir() and (p / "page").is_dir())
        (tree / hf.MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "tree": str(tree), "dry_run": dry_run, "undo": True,
            "pages_back": back}


def format_result(result: dict) -> str:
    if result.get("undo"):
        lines = [f"{p:<24} {n:>5} page(s) back" for p, n in result["pages_back"].items()]
        return "\n".join(lines or ["nothing to undo"]) + (
            "\n\n(dry run — nothing moved)" if result["dry_run"] else "")
    lines = []
    for p, d in result["projects"].items():
        line = (f"{p:<24} objects {d['objects']:>4} → eval {d['eval_objects']:>3}  "
                f"moving now {len(d['moving']):>3} object(s), {d['pages_moving']:>4} page(s)")
        if d["forced"]:
            line += f"  forced: {', '.join(d['forced'])}"
        lines.append(line)
    if result["dry_run"]:
        lines += ["", "(dry run — nothing moved)"]
    else:
        lines += ["", f"moved {result['pages_moved']} page(s); export.json records the split"]
    return "\n".join(lines)
