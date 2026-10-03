"""A PDF is a container of pages, not a page (#476).

`INGEST_EXTS` listed `.pdf`; `CONVERTIBLE_SUFFIXES` did not. So a PDF was listed,
downloaded, written into the page cache unchanged and posted to the gateway as an
image, where kraken said `cannot identify image file`. The runner saw only a 502,
read it as transient, retried twice with backoff, and charged a failed page to the
model — fourteen times in the corpus run of 2026-09-23, none of them a page.

What a PDF *means* here depends on what is beside it, and both readings have a
failure mode:

- In the Kantonsbibliothek Appenzell material an 82 KB PDF sits beside the four
  13 MB TIFFs it was made from, its own name listing them
  (`…_514-517.pdf` next to `…_0514.tif` … `_0517.tif`). Reading it too would
  transcribe the same leaves twice.
- A folder where the PDF is the only digitisation exists as well, and excluding
  every PDF wholesale would lose it in silence — the one failure nobody notices.

So: a derivative is skipped, a lone PDF is rendered to one JPEG per page, and
both are counted in the report. The keys of the scans must not move while that
happens, or every running corpus loses its resume.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as b                      # noqa: E402
from utils import images                   # noqa: E402

#: The Appenzell shape, named as the share names it.
APPENZELL = [
    "lassberg-letter-0905/40C-01-04-01-2_MS-321-04-01_514-517.pdf",
    "lassberg-letter-0905/40C-01-04-01-2_Ms-321-04-01_0514.tif",
    "lassberg-letter-0905/40C-01-04-01-2_Ms-321-04-01_0515.tif",
    "lassberg-letter-0905/40C-01-04-01-2_Ms-321-04-01_0516.tif",
    "lassberg-letter-0905/40C-01-04-01-2_Ms-321-04-01_0517.tif",
]


def _pdf(path: Path, pages: int = 3) -> Path:
    from PIL import Image
    Image.init()                            # JPEG encoder, for Pillow's PDF writer
    shades = ["white", "gray", "black", "red", "blue"]
    first, *rest = [Image.new("RGB", (400, 600), shades[i % len(shades)])
                    for i in range(pages)]
    path.parent.mkdir(parents=True, exist_ok=True)
    first.save(path, save_all=True, append_images=rest)
    return path


def _tif(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (60, 40), "white").save(path)
    return path


# ── the listing ─────────────────────────────────────────────────────────────
def test_a_pdf_beside_its_scans_is_not_listed_as_a_page():
    pages = b.pages_from_paths(APPENZELL, count_pages=lambda p: 4)

    assert [p.path.suffix for p in pages] == [".tif"] * 4


def test_the_scan_keys_do_not_move():
    """The test that protects every running corpus: a changed key is a lost
    resume, and the whole run starts again."""
    before = ["lassberg-letter-0905__40C-01-04-01-2_Ms-321-04-01_0514",
              "lassberg-letter-0905__40C-01-04-01-2_Ms-321-04-01_0515",
              "lassberg-letter-0905__40C-01-04-01-2_Ms-321-04-01_0516",
              "lassberg-letter-0905__40C-01-04-01-2_Ms-321-04-01_0517"]

    assert [p.key for p in b.pages_from_paths(APPENZELL, count_pages=lambda p: 4)] == before


def test_the_skipped_pdf_is_reported_not_dropped():
    pages = b.pages_from_paths(APPENZELL, count_pages=lambda p: 4)

    assert [s.rel for s in pages.skipped] == [APPENZELL[0]]
    assert "derivative" in pages.skipped[0].reason


def test_a_pdf_alone_in_its_folder_becomes_one_page_per_sheet():
    """The half a blanket exclusion would lose in silence."""
    pages = b.pages_from_paths(["only-a-pdf/letter.pdf"], count_pages=lambda p: 3)

    assert [p.pdf_page for p in pages] == [0, 1, 2]
    assert [p.key for p in pages] == ["only-a-pdf__letter__p0001",
                                      "only-a-pdf__letter__p0002",
                                      "only-a-pdf__letter__p0003"]
    assert pages.skipped == []


def test_a_rendered_page_cannot_collide_with_a_scan_named_in_digits():
    """`Ms-321_0514.tif` and page 514 of a PDF are different things."""
    pages = b.pages_from_paths(["d/Ms-321_0514.tif", "e/doc.pdf"],
                               count_pages=lambda p: 514)

    assert "d__Ms-321_0514" in {p.key for p in pages}
    assert "e__doc__p0514" in {p.key for p in pages}


def test_a_pdf_that_cannot_be_read_is_reported_and_never_sent():
    """0 pages means "cannot say". A container nobody can count must not reach a
    recogniser as an image, which is the whole of this issue.

    In a *remote* listing nobody can count any of them, and the reason says so
    (#531): handing share names to the local reader printed thirteen lines of
    `cannot read the PDF ([Errno 2] No such file or directory:
    'digitalisate/Basel/PA 82a B 9.pdf')` — a missing-file error for a file that
    is on the share and simply had not been downloaded.
    """
    pages = b.pages_from_paths(["broken/x.pdf"], count_pages=lambda p: 0)

    assert list(pages) == []
    assert pages.skipped[0].reason == b.UNCOUNTED_REMOTE
    assert "download" in pages.skipped[0].reason


def test_the_local_walk_still_says_a_pdf_could_not_be_read(tmp_path):
    """The two reasons stay distinct: a local file that will not open is a broken
    file, and that is worth a different sentence than one nobody fetched."""
    pdf = tmp_path / "only-a-pdf" / "letter.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"not a PDF at all")

    pages = b.discover_pages(tmp_path)

    assert list(pages) == []
    assert "could not be read" in pages.skipped[0].reason


def test_a_corpus_of_scans_alone_is_completely_unchanged():
    pages = b.pages_from_paths(["a/1.tif", "a/2.tif"])

    assert [p.key for p in pages] == ["a__1", "a__2"]
    assert [p.pdf_page for p in pages] == [None, None]
    assert pages.skipped == []


def test_the_local_walk_applies_the_same_rule(tmp_path: Path):
    _pdf(tmp_path / "derivative/scan_1-2.pdf", pages=2)
    _tif(tmp_path / "derivative/scan_0001.tif")
    _pdf(tmp_path / "lonely/letter.pdf", pages=2)

    pages = b.discover_pages(tmp_path)

    assert [p.key for p in pages] == ["derivative__scan_0001",
                                      "lonely__letter__p0001", "lonely__letter__p0002"]
    assert [s.reason for s in pages.skipped] == [
        "PDF derivative of the scans in the same folder"]


def test_a_sample_draws_over_pages_not_over_files(tmp_path: Path):
    """A PDF of forty pages is forty chances to be picked, exactly as forty
    TIFFs would be — otherwise a sampled run under-reads whole documents."""
    _pdf(tmp_path / "lonely/letter.pdf", pages=6)

    assert len(b.discover_pages(tmp_path, sample=4)) == 4


def test_a_limit_counts_pages_too(tmp_path: Path):
    _pdf(tmp_path / "lonely/letter.pdf", pages=6)

    assert len(b.discover_pages(tmp_path, limit=2)) == 2


# ── rendering ───────────────────────────────────────────────────────────────
def test_a_pdf_page_renders_to_a_jpeg(tmp_path: Path):
    data = _pdf(tmp_path / "x.pdf", pages=2).read_bytes()
    out = images.render_pdf_page(data, 1, tmp_path / "out.jpg")

    from PIL import Image
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.size[0] > 1000, "rendered below 300 dpi"


def test_the_render_is_compressed_not_archival(tmp_path: Path):
    """Light compression, like every other working copy: quality 85 is the
    visually-lossless-for-text point, and a recogniser resizes it again anyway."""
    data = _pdf(tmp_path / "x.pdf", pages=1).read_bytes()
    out = images.render_pdf_page(data, 0, tmp_path / "out.jpg")

    assert out.suffix == ".jpg"
    assert images.WORKING_QUALITY == 85


def test_a_page_index_past_the_end_is_an_error_not_a_blank_page(tmp_path: Path):
    data = _pdf(tmp_path / "x.pdf", pages=2).read_bytes()

    with pytest.raises(ValueError, match="page 9 of a PDF with 2"):
        images.render_pdf_page(data, 8, tmp_path / "out.jpg")


def test_a_damaged_pdf_counts_zero_rather_than_raising():
    """One corrupt derivative in a corpus of thousands is a file to report and
    walk past, not a reason to end the listing."""
    assert images.pdf_page_count(b"%PDF-1.4 and then nothing") == 0


def test_the_page_path_is_numbered_and_sorts(tmp_path: Path):
    assert images.pdf_page_path(Path("a/letter.pdf"), 2).name == "letter_p0003.jpg"


# ── the cache ───────────────────────────────────────────────────────────────
def test_the_cache_renders_one_page_and_reuses_it(tmp_path: Path):
    root, cache_dir = tmp_path / "share", tmp_path / "cache"
    _pdf(root / "lonely/letter.pdf", pages=3)
    cache = images.PageCache(root, cache_dir)

    first, record = cache.fetch(root / "lonely/letter.pdf", 1)
    second, _ = cache.fetch(root / "lonely/letter.pdf", 1)

    assert first == second and first.name == "letter_p0002.jpg"
    assert (cache.misses, cache.hits) == (1, 1)
    assert record["pdf_page"] == 1


def test_each_page_of_a_pdf_is_its_own_cache_entry(tmp_path: Path):
    root, cache_dir = tmp_path / "share", tmp_path / "cache"
    _pdf(root / "lonely/letter.pdf", pages=3)
    cache = images.PageCache(root, cache_dir)

    paths = {cache.fetch(root / "lonely/letter.pdf", i)[0] for i in range(3)}

    assert len(paths) == 3


def test_the_record_names_the_pdf_in_the_archive_not_the_rendered_bytes(tmp_path: Path):
    """A reading says "this came from these bytes", and the bytes in the archive
    are the PDF's — the JPEG on the scratch disk is not a thing the share has."""
    import hashlib

    root, cache_dir = tmp_path / "share", tmp_path / "cache"
    src = _pdf(root / "lonely/letter.pdf", pages=2)
    cache = images.PageCache(root, cache_dir)

    _, record = cache.fetch(src, 0)

    assert record["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert record["name"] == "letter.pdf"


def test_an_existing_image_cache_entry_is_not_invalidated(tmp_path: Path):
    """A sidecar written before PDFs were rendered carries no `pdf_page`, which
    reads as None — what a plain scan is. A whole cache re-fetched over a schema
    change would be 160 GB across the mount."""
    root, cache_dir = tmp_path / "share", tmp_path / "cache"
    _tif(root / "a/scan.tif")
    cache = images.PageCache(root, cache_dir)
    cache.fetch(root / "a/scan.tif")

    cache.fetch(root / "a/scan.tif")

    assert (cache.misses, cache.hits) == (1, 1)


# ── the report ──────────────────────────────────────────────────────────────
def test_the_report_names_how_many_files_were_not_pages(tmp_path: Path):
    report = b.BatchReport(
        run="r", out_root=tmp_path, pages=4,
        skipped_files=[b.SkippedFile(APPENZELL[0],
                                     "PDF derivative of the scans in the same folder")])

    text = b.format_report(report)

    assert "not pages, skipped: **1**" in text
    assert "derivative" in text


def test_a_run_with_nothing_skipped_says_nothing(tmp_path: Path):
    text = b.format_report(b.BatchReport(run="r", out_root=tmp_path, pages=4))

    assert "not pages" not in text


def test_the_json_report_carries_the_files_themselves(tmp_path: Path):
    """The count says how much, the list says which — and "which" is what tells
    somebody whether a folder was lost."""
    report = b.BatchReport(run="r", out_root=tmp_path, pages=4,
                           skipped_files=[b.SkippedFile("a/x.pdf", "PDF could not be read")])

    assert report.to_dict()["skipped_files"] == [
        {"file": "a/x.pdf", "reason": "PDF could not be read"}]


def test_a_hand_built_page_list_still_runs(tmp_path: Path):
    """`pages` is a PageList from a listing and a plain list when a caller built
    one; the second must not become an AttributeError in the runner."""
    report = b.run_batch([], models=[], run="r", out_root=tmp_path,
                         recognise=lambda *a, **k: None)

    assert report.skipped_files == []


# ── a missing renderer is "cannot say", not an aborted walk ──────────────────
#
# `pdf_page_count`'s docstring says 0 "rather than an exception: a corrupt
# derivative in a corpus of thousands is a file to report and walk past, not a
# reason to end the listing". The `import pypdfium2` sat outside its own `try`,
# so an environment without the module got the exception the docstring rules out
# — and on 2026-10-02 that ended an export over 6742 pages on its first PDF.

def _without_pypdfium(monkeypatch):
    import builtins

    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "pypdfium2":
            raise ModuleNotFoundError("No module named 'pypdfium2'")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake)


def test_a_missing_renderer_counts_zero_rather_than_raising(monkeypatch):
    from utils import images

    _without_pypdfium(monkeypatch)
    monkeypatch.setattr(images, "_SAID_NO_RENDERER", False)

    assert images.pdf_page_count(b"%PDF-1.4") == 0


def test_it_says_so_once_not_once_per_file(monkeypatch, caplog):
    """Thirteen PDFs and thirteen identical lines is how a real message gets
    scrolled past."""
    from utils import images

    _without_pypdfium(monkeypatch)
    monkeypatch.setattr(images, "_SAID_NO_RENDERER", False)
    said = []
    monkeypatch.setattr(images.logger, "error", lambda msg: said.append(msg))

    for _ in range(5):
        images.pdf_page_count(b"%PDF-1.4")

    assert len(said) == 1
    assert "pypdfium2" in said[0]
    assert "pip install" in said[0]


def test_the_walk_counts_a_pdf_zero_instead_of_dying(tmp_path, monkeypatch):
    """The caller's contract is the same: 0 means "cannot say from here". It
    caught only OSError, which is the error a *missing file* raises."""
    import atr_batch as batch
    from utils import images

    pdf = tmp_path / "letter.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    _without_pypdfium(monkeypatch)
    monkeypatch.setattr(images, "_SAID_NO_RENDERER", False)

    assert batch._count_pdf_pages(pdf) == 0
