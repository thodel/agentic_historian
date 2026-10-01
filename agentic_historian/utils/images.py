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
import io
import json
import os
from pathlib import Path
from typing import Optional

from loguru import logger

import config

__all__ = [
    "WORKING_SUFFIX",
    "WORKING_QUALITY",
    "CONVERTIBLE_SUFFIXES",
    "PDF_SUFFIXES",
    "PDF_RENDER_DPI",
    "SIDECAR_SUFFIX",
    "is_pdf",
    "pdf_page_count",
    "render_pdf_page",
    "pdf_page_path",
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

#: A PDF is not an image and not a page: it is a *container* of pages, and that
#: is the whole of #476. `INGEST_EXTS` listed it, nothing converted it, so it was
#: written into the page cache unchanged and posted to the gateway as an image —
#: where kraken answered "cannot identify image file", the runner read the 502 as
#: transient, retried twice, and charged a failed page to the model. Fourteen
#: times in one corpus run, none of them a page.
PDF_SUFFIXES = {".pdf"}

#: What a PDF page is rendered at. 300 dpi is the floor for handwriting: the
#: Lassberg TIFFs are ~2636 x 3212 for an A4-ish leaf, which is about 300 dpi,
#: and rendering a derivative coarser than the scan it derives from would make
#: the two incomparable. Higher costs time and buys nothing a PDF derivative
#: holds — it was compressed once already.
PDF_RENDER_DPI = 300

#: pdfium renders at 72 dpi x scale.
_PDF_BASE_DPI = 72


def is_pdf(path: Path) -> bool:
    return Path(path).suffix.lower() in PDF_SUFFIXES


def pdf_page_path(path: Path, index: int) -> Path:
    """Where page ``index`` of a PDF lives: ``letter.pdf`` -> ``letter_p0003.jpg``.

    Zero-padded and sorted-friendly, and ``_p`` rather than a bare number so a
    rendered page cannot collide with a scan that happens to end in digits —
    ``Ms-321_0514.tif`` and page 514 of a PDF are different things.
    """
    return path.with_name(f"{path.stem}_p{index + 1:04d}{WORKING_SUFFIX}")


def pdf_page_count(data: bytes) -> int:
    """How many pages this PDF holds, or 0 when it cannot be opened.

    0 rather than an exception: a corrupt derivative in a corpus of thousands is
    a file to report and walk past, not a reason to end the listing.
    """
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — a damaged PDF is data, not a bug
        logger.error(f"[images] cannot read PDF ({exc})")
        return 0
    try:
        return len(doc)
    finally:
        doc.close()


def render_pdf_page(data: bytes, index: int, dest: Path,
                    quality: int = WORKING_QUALITY,
                    dpi: int = PDF_RENDER_DPI) -> Path:
    """Render one page of a PDF to ``dest`` as JPEG.

    Same encoder and the same atomic write as every other working copy, so a
    page that came out of a PDF and a page that came out of a TIFF are the same
    kind of thing by the time a model sees one.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(io.BytesIO(data))
    try:
        if not 0 <= index < len(doc):
            raise ValueError(
                f"{dest.name}: page {index + 1} of a PDF with {len(doc)} page(s)")
        image = doc[index].render(scale=dpi / _PDF_BASE_DPI).to_pil()
        return _save(image, dest, quality)
    finally:
        doc.close()


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


def cold_fetch(dest: Path, cache_dir: Path, accepts, *,
               archive: Optional[Path] = None) -> Optional[dict]:
    """A working copy from the cold tier, put back locally. None on a miss (#487).

    tei has 92 GB and no buffer, so a daily job moves anything in the page cache
    older than two days to the research share — paths preserved, sidecar
    alongside, verified copy before the delete (tei-vm-sanity#7). ``fetch``
    looked in exactly one directory, so a moved entry was a miss and the next
    pass re-fetched the 25 MB original and converted it again. The eviction
    bought disk and gave the working copies no second life, which is the one
    thing the cache exists for.

    A working copy is ~1.5 MB against the original's 25 MB and the share reads
    at 410 MB/s, so bringing one back is about an order of magnitude cheaper
    than making it again.

    ``accepts`` is the caller's own hit test, passed in rather than repeated
    here: :class:`PageCache` compares the name *and* the PDF page index,
    ``WebdavPageSource`` compares the name. Two copies of that rule would be two
    answers to "is this the right file", and the cold tier must be exactly as
    strict as the local one — a sidecar naming a different file is a miss here
    too, for the reason it is a miss there.

    Never raises. An archive that is not set, not mounted, or holds nothing for
    this page is today's behaviour: read the original, convert, write.
    """
    if archive is None:
        archive = config.ATR_PAGE_CACHE_ARCHIVE
    if not archive:
        return None
    try:
        rel = Path(dest).resolve().relative_to(Path(cache_dir).resolve())
    except ValueError:
        # Not under the cache root, so there is no matching path in the archive.
        return None
    cold = Path(archive) / rel
    cold_side = cold.with_name(cold.name + SIDECAR_SUFFIX)
    try:
        record = _read_sidecar(cold_side)
        if not record or not accepts(record) or not cold.exists():
            return None
        data = cold.read_bytes()
        side_bytes = cold_side.read_bytes()
    except OSError:
        # An unmounted share, a permission, a half-moved pair. All of them mean
        # "not available", and none of them may stop the run.
        return None
    _write_atomic_bytes(Path(dest), data)
    _write_atomic_bytes(Path(dest).with_name(Path(dest).name + SIDECAR_SUFFIX),
                        side_bytes)
    return record


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
        #: Served by the cold tier on the share (#487). Counted apart from
        #: ``hits`` and ``misses`` so it stays visible how often it carries —
        #: folded into either one, nobody could tell whether the eviction is
        #: costing anything.
        self.cold_hits = 0
        self.misses = 0
        self.source_bytes = 0

    def path_for(self, src: Path, pdf_page: Optional[int] = None) -> Path:
        """Where ``src``'s working copy belongs in the cache.

        ``pdf_page`` is a zero-based page index inside a PDF, and makes the
        answer one page rather than the container: ``letter.pdf`` with page 2 is
        ``letter_p0003.jpg``. The pages of one PDF therefore sit beside each
        other in the cache the way a folder of scans does.
        """
        rel = Path(src).resolve().relative_to(self.root)
        dest = self.cache_dir / rel
        if pdf_page is not None:
            return pdf_page_path(dest, pdf_page)
        return working_path(dest) if needs_conversion(dest) else dest

    def fetch(self, src: Path, pdf_page: Optional[int] = None) -> tuple[Path, dict]:
        """``(local path to read, record of the original)``, converting on a miss.

        The record is ``{"name", "bytes", "sha256"}`` of the file in the share.
        A sidecar naming a *different* file is treated as a miss rather than
        trusted: two sources in one directory can share a working-copy name
        (``a.tif`` and ``a.png`` both become ``a.jpg``), and reusing one for the
        other would attach a reading to bytes that never produced it. The page
        index is compared for the same reason; a sidecar written before PDFs were
        rendered carries no ``pdf_page`` and so reads as None, which is what a
        plain image is, so no existing cache entry is invalidated.
        """
        src = Path(src)
        dest = self.path_for(src, pdf_page)
        side = dest.with_name(dest.name + SIDECAR_SUFFIX)
        def accepts(candidate: dict) -> bool:
            return (candidate.get("name") == src.name
                    and candidate.get("pdf_page") == pdf_page)

        record = _read_sidecar(side)
        if record and accepts(record) and dest.exists():
            self.hits += 1
            return dest, record

        # Before the original: the cold tier holds the working copy this would
        # otherwise make again (#487).
        record = cold_fetch(dest, self.cache_dir, accepts)
        if record is not None:
            self.cold_hits += 1
            return dest, record

        data = src.read_bytes()          # the one and only read of the original
        record = {
            "name": src.name,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if pdf_page is not None:
            # The digest is of the whole PDF, which is right: it is the archival
            # object this page came out of, and naming the rendered bytes would
            # point at a file the archive does not have. The page index is in
            # the record so "which page of it" is answerable too.
            record["pdf_page"] = pdf_page
            render_pdf_page(data, pdf_page, dest, self.quality)
        elif needs_conversion(src):
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
