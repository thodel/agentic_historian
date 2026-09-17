"""Working copies of archival scans.

**Why this exists.** The Lassberg digitisations are uncompressed TIFF: 2636 x 3212
at three bytes a pixel is 25 MB for one page, and the share holds thousands. A
full mirror came to roughly 160 GB against the 92 GB tei has in total, and the
pull filled the disk and died at 899 pages (2026-09-16).

Uncompressed TIFF is an archival format, and the archive is the Nextcloud share.
What recognition needs is a working copy: the same pixels, encoded so they fit.
JPEG at quality 85 is about 1.5-2.5 MB for one of these pages — an order of
magnitude less for a difference no recogniser can see, and the gateway resizes
the image again before any model reads it.

**Full resolution is kept.** Only the encoding changes. Downscaling here would
decide, once and irreversibly, what every future model gets to see; that decision
belongs to the model's own budget at inference time, not to ingest.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger

__all__ = [
    "WORKING_SUFFIX",
    "WORKING_QUALITY",
    "CONVERTIBLE_SUFFIXES",
    "SIDECAR_SUFFIX",
    "working_path",
    "needs_conversion",
    "convert_file",
    "convert_bytes",
    "PageCache",
]

#: What a working copy is called. One suffix, so the mirror is self-describing.
WORKING_SUFFIX = ".jpg"

#: Quality 85 is the usual "visually lossless for text" point. Below ~75 JPEG
#: ringing starts to show on thin pen strokes, which is exactly the signal a
#: handwriting recogniser reads.
WORKING_QUALITY = 85

#: Formats worth re-encoding. A JPEG is already compressed and is left alone —
#: re-encoding it would lose quality for nothing.
CONVERTIBLE_SUFFIXES = {".tif", ".tiff", ".bmp", ".png"}


def working_path(path: Path) -> Path:
    """Where this file's working copy lives: same name, ``.jpg``."""
    return path.with_suffix(WORKING_SUFFIX)


def needs_conversion(path: Path) -> bool:
    return path.suffix.lower() in CONVERTIBLE_SUFFIXES


def convert_bytes(data: bytes, dest: Path, quality: int = WORKING_QUALITY) -> Path:
    """Write ``data`` to ``dest`` as JPEG, through a temp file.

    Pillow is imported here rather than at module import: the recognition path
    does not need it, and an ingest-only dependency should not be able to stop
    the batch runner from starting.
    """
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        return _save(img, dest, quality)


def convert_file(src: Path, quality: int = WORKING_QUALITY,
                 remove_source: bool = True) -> Optional[Path]:
    """Convert one file in place: ``x.tif`` -> ``x.jpg``, then drop the original.

    Returns the working copy, or ``None`` when the file needs no conversion or
    cannot be read as an image. Never removes the source before the replacement
    is on disk, because a conversion that fails halfway must cost nothing.
    """
    from PIL import Image

    if not needs_conversion(src):
        return None
    dest = working_path(src)
    try:
        with Image.open(src) as img:
            _save(img, dest, quality)
    except Exception as exc:  # noqa: BLE001 — one unreadable scan must not stop a corpus
        logger.error(f"[images] {src.name}: cannot convert ({exc})")
        return None
    if remove_source and src != dest:
        src.unlink(missing_ok=True)
    return dest


def _save(img, dest: Path, quality: int) -> Path:
    """RGB, JPEG, atomic. ``.part`` then rename, so a reader never sees half."""
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    img.save(tmp, format="JPEG", quality=quality, optimize=True, progressive=True)
    os.replace(tmp, dest)
    return dest


# ── reading a corpus that lives on a mount ───────────────────────────────────

#: Written beside every cached working copy, recording the **original** file it
#: was made from: its name, its size, and the sha256 of its bytes.
#:
#: The cache cannot vouch for the archival file from its own contents — a JPEG
#: re-encoded from a TIFF has different bytes and therefore a different digest —
#: and a result that says "this reading came from these bytes" has to name the
#: bytes in the archive, not the bytes on the scratch disk. Recording the digest
#: at conversion time is also what keeps the archival file to **one** read: the
#: runner used to hash the source separately, which over a mount is a second
#: 25 MB transfer for a number we already had in hand.
SIDECAR_SUFFIX = ".src.json"


class PageCache:
    """Local working copies of pages that live somewhere expensive to read.

    The Lassberg scans are no longer mirrored to tei — the Nextcloud share is
    mounted and read in place — which removes the 160 GB copy and replaces it
    with a per-page cost: 25 MB of uncompressed TIFF across the network *every
    time a page is opened*. A batch opens each page at least once per model, and
    a second model over the same corpus would pay the whole transfer again.

    So a page is fetched once, converted to its JPEG working copy (about 0.7 MB
    at full resolution), and every later read — this model's retry, the next
    model's pass, next week's re-run — is a local file. The cache is keyed by the
    page's path relative to the corpus root, so it mirrors the share's structure
    and two shares cannot collide in one cache directory.

    Like the rest of the runner, its state is what is on disk: a cached copy with
    its sidecar is a hit, anything else is a miss. Nothing needs to be reconciled
    after an interrupted run, and deleting the cache directory costs time, never
    correctness.
    """

    def __init__(self, root: Path, cache_dir: Path,
                 quality: int = WORKING_QUALITY) -> None:
        self.root = Path(root).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.quality = quality
        self.hits = 0
        self.misses = 0
        self.source_bytes = 0

    def path_for(self, src: Path) -> Path:
        """Where ``src``'s working copy belongs in the cache."""
        rel = Path(src).resolve().relative_to(self.root)
        dest = self.cache_dir / rel
        return working_path(dest) if needs_conversion(dest) else dest

    def fetch(self, src: Path) -> tuple[Path, dict]:
        """``(local path to read, record of the original)``, converting on a miss.

        The record is ``{"name", "bytes", "sha256"}`` of the file in the share.
        A sidecar naming a *different* file is treated as a miss rather than
        trusted: two sources in one directory can share a working-copy name
        (``a.tif`` and ``a.png`` both become ``a.jpg``), and reusing one for the
        other would attach a reading to bytes that never produced it.
        """
        src = Path(src)
        dest = self.path_for(src)
        side = dest.with_name(dest.name + SIDECAR_SUFFIX)
        record = _read_sidecar(side)
        if record and record.get("name") == src.name and dest.exists():
            self.hits += 1
            return dest, record

        data = src.read_bytes()          # the one and only read of the original
        record = {
            "name": src.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if needs_conversion(src):
            convert_bytes(data, dest, self.quality)
        else:
            _write_atomic_bytes(dest, data)
        _write_atomic_bytes(side, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        self.misses += 1
        self.source_bytes += len(data)
        return dest, record


def _read_sidecar(path: Path) -> Optional[dict]:
    """The sidecar, or None when it is missing or unreadable.

    Parsed rather than merely checked for existence, for the same reason the
    batch runner parses its results: an interrupted run is exactly what leaves a
    file that exists and does not parse.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("sha256") else None


def _write_atomic_bytes(dest: Path, data: bytes) -> Path:
    """``.part`` then rename, so a reader never sees a half-written cache entry."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return dest
