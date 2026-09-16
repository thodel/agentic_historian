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

import os
from pathlib import Path
from typing import Optional

from loguru import logger

__all__ = [
    "WORKING_SUFFIX",
    "WORKING_QUALITY",
    "CONVERTIBLE_SUFFIXES",
    "working_path",
    "needs_conversion",
    "convert_file",
    "convert_bytes",
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
