"""A page the eviction moved to the share is fetched back, not made again (#487).

tei has 92 GB and no buffer. The page cache grew 3.8 GB to 9.6 GB on
24.09.2026 and took `/` from 65 to 74 per cent, so a daily job now moves
anything older than two days to the research share — paths preserved, sidecar
alongside, verified copy before the delete (tei-vm-sanity#7).

But `fetch` looked in exactly **one** directory. A moved entry was therefore a
miss, and the next pass re-fetched the 25 MB TIFF and converted it again: the
eviction bought disk and gave the working copies no second life, which is the
one thing the cache exists for, by its own docstring.

The numbers that decide it: a working copy is about 1.5 MB against the
original's 25 MB, and the share reads at 410 MB/s. Bringing a page back is
roughly an order of magnitude cheaper than making it again.

Both caches get the tier, and the second one is the one that pays for it: the
Lassberg corpus is read through `WebdavPageSource`, so a moved working copy not
looked for is a download and a re-encode. The hit test is passed **in** rather
than written twice — the cold tier has to be exactly as strict as the local one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import images
from utils.images import SIDECAR_SUFFIX, PageCache, cold_fetch


def a_png(path: Path, colour=(10, 20, 30)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), colour).save(path, format="PNG")
    return path


def take_the_original_away(src: Path) -> Path:
    """Remove the original, so that reading it raises.

    #487 asks for exactly this: "ein Pfad, der beim Lesen wirft, belegt es".

    It has to be the *file*, not the path object. A ``Path`` subclass whose
    ``read_bytes`` raises looks like the obvious way and does not work:
    ``PageCache.fetch`` normalises its argument with ``Path(src)``, so the
    override is gone by the time anything reads, and the test would quietly
    read the real file and pass with no cold tier at all. Deleting survives
    every normalisation.

    It also has to be deletion rather than a read counter, because converting
    this PNG twice is deterministic: a re-conversion produces byte-identical
    output and an identical record, so a test that only compares the result
    cannot tell a cold hit from a second conversion. That is what
    :func:`test_the_mechanism_bites` guards.
    """
    src.unlink()
    return src


@pytest.fixture
def share(tmp_path: Path) -> Path:
    root = tmp_path / "mount" / "digitalisate"
    a_png(root / "letter-0001" / "001.png")
    return root


@pytest.fixture
def archived(tmp_path: Path, share: Path):
    """A cache whose entry has been moved to the archive, as the job leaves it.

    Built by running the cache once and then moving the pair — so the archive
    holds exactly what the eviction would put there, rather than what a test
    imagines it would.
    """
    cache_dir = tmp_path / "page_cache"
    archive = tmp_path / "archive"
    src = share / "letter-0001" / "001.png"

    warm = PageCache(share, cache_dir)
    dest, record = warm.fetch(src)
    rel = dest.relative_to(cache_dir)
    side = dest.with_name(dest.name + SIDECAR_SUFFIX)

    (archive / rel).parent.mkdir(parents=True, exist_ok=True)
    dest.replace(archive / rel)
    side.replace((archive / rel).with_name((archive / rel).name + SIDECAR_SUFFIX))

    return cache_dir, archive, src, record


# ── the hit ─────────────────────────────────────────────────────────────────
def test_a_moved_page_comes_back_without_reading_the_original(archived, monkeypatch):
    """The whole point. A path that raises on read proves the original was not
    touched."""
    cache_dir, archive, src, original = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    cache = PageCache(src.parent.parent, cache_dir)

    dest, record = cache.fetch(take_the_original_away(src))

    assert record == original
    assert dest.exists()


def test_the_mechanism_bites(archived, monkeypatch):
    """The guard on the test above. With no cold tier, the same fetch must fail
    — otherwise that test would pass whether or not the tier exists, which is
    how three of these tests were written the first time."""
    cache_dir, _, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", None)
    cache = PageCache(src.parent.parent, cache_dir)

    with pytest.raises(OSError):
        cache.fetch(take_the_original_away(src))


def test_the_cold_hit_is_counted_apart(archived, monkeypatch):
    """Three cases, three counters. Folded into `hits`, nobody could tell
    whether evicting to the share costs re-downloads or saves them."""
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    cache = PageCache(src.parent.parent, cache_dir)

    cache.fetch(take_the_original_away(src))

    assert (cache.hits, cache.cold_hits, cache.misses) == (0, 1, 0)
    assert cache.source_bytes == 0


def test_the_page_is_put_back_so_the_next_read_is_local(archived, monkeypatch):
    """A cold hit that did not write locally would pay the share every time."""
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    cache = PageCache(src.parent.parent, cache_dir)
    cache.fetch(src)

    cache.fetch(take_the_original_away(src))

    assert (cache.hits, cache.cold_hits) == (1, 1)


def test_the_sidecar_comes_back_too(archived, monkeypatch):
    """Without it the local tier would miss on the very next pass, and the
    digest of the original — the thing a result cites — would be gone."""
    cache_dir, archive, src, original = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    cache = PageCache(src.parent.parent, cache_dir)

    dest, _ = cache.fetch(take_the_original_away(src))

    side = dest.with_name(dest.name + SIDECAR_SUFFIX)
    assert json.loads(side.read_text(encoding="utf-8")) == original


def test_the_bytes_are_the_working_copy_not_a_new_conversion(archived, monkeypatch):
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    cache = PageCache(src.parent.parent, cache_dir)
    rel = cache.path_for(src).relative_to(cache_dir)

    dest, _ = cache.fetch(take_the_original_away(src))

    assert dest.read_bytes() == (archive / rel).read_bytes()


# ── as strict as the local tier ─────────────────────────────────────────────
def test_a_sidecar_naming_another_file_is_a_miss(archived, monkeypatch):
    """#487's second test. Two sources in one directory can share a working-copy
    name, and reusing one for the other attaches a reading to bytes that never
    produced it — the reason the local tier checks, and so the same reason here.
    """
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    rel = PageCache(src.parent.parent, cache_dir).path_for(src).relative_to(cache_dir)
    cold_side = (archive / rel).with_name((archive / rel).name + SIDECAR_SUFFIX)
    cold_side.write_text(json.dumps({"name": "something-else.png", "bytes": 1,
                                     "sha256": "x"}), encoding="utf-8")
    cache = PageCache(src.parent.parent, cache_dir)

    _, record = cache.fetch(src)

    assert record["name"] == src.name
    assert (cache.cold_hits, cache.misses) == (0, 1)


def test_a_pdf_page_index_that_differs_is_a_miss(archived, monkeypatch):
    """The local tier compares it; so must this, or page 1 is served for page 3."""
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)

    assert cold_fetch(cache_dir / "x.jpg", cache_dir,
                      lambda rec: rec.get("pdf_page") == 2,
                      archive=archive) is None


def test_an_archived_sidecar_without_its_page_is_a_miss(archived, monkeypatch):
    """A half-moved pair. The job copies and verifies before deleting, but a
    crash in between is what leaves one of the two."""
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    rel = PageCache(src.parent.parent, cache_dir).path_for(src).relative_to(cache_dir)
    (archive / rel).unlink()
    cache = PageCache(src.parent.parent, cache_dir)

    cache.fetch(src)

    assert (cache.cold_hits, cache.misses) == (0, 1)


# ── nothing set, nothing mounted, nothing changes ──────────────────────────
def test_no_archive_configured_behaves_exactly_as_before(archived, monkeypatch):
    """The default. #487: "Vorgabe leer, dann ändert sich nichts"."""
    cache_dir, _, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", None)
    cache = PageCache(src.parent.parent, cache_dir)

    _, record = cache.fetch(src)

    assert record["name"] == src.name
    assert (cache.cold_hits, cache.misses) == (0, 1)


def test_an_archive_that_is_not_mounted_is_not_an_error(archived, monkeypatch, tmp_path):
    cache_dir, _, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", tmp_path / "not-mounted")
    cache = PageCache(src.parent.parent, cache_dir)

    _, record = cache.fetch(src)

    assert record["name"] == src.name
    assert cache.misses == 1


def test_a_destination_outside_the_cache_has_no_archive_path(tmp_path):
    """No matching relative path, so nothing to look for — and no exception."""
    assert cold_fetch(tmp_path / "elsewhere" / "x.jpg", tmp_path / "cache",
                      lambda _rec: True, archive=tmp_path / "archive") is None


def test_an_unreadable_archive_is_a_miss(archived, monkeypatch):
    """A share that is mounted and refuses. "Not available" must never stop the
    run — it only means the page gets made again."""
    cache_dir, archive, src, _ = archived
    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)

    # Only the archive refuses. Patching the reader outright would also break
    # the local sidecar, and then the test would pass for the wrong reason.
    real = images._read_sidecar

    def refuse_the_archive(path):
        if Path(path).is_relative_to(archive):
            raise OSError("stale handle")
        return real(path)

    monkeypatch.setattr(images, "_read_sidecar", refuse_the_archive)
    cache = PageCache(src.parent.parent, cache_dir)

    _, record = cache.fetch(src)

    assert record["name"] == src.name
    assert cache.misses == 1
    assert cache.cold_hits == 0


# ── the path that actually pays for the eviction ───────────────────────────
def test_the_webdav_source_looks_in_the_cold_tier_too(tmp_path, monkeypatch):
    """The Lassberg corpus is read through `WebdavPageSource`, so a moved
    working copy not looked for there is a 25 MB download and a re-encode."""
    from utils import nextcloud

    cache_dir = tmp_path / "page_cache"
    archive = tmp_path / "archive"
    remote = "Lassberg/letter-0001/001.png"

    source = nextcloud.WebdavPageSource.__new__(nextcloud.WebdavPageSource)
    source.cache_dir = cache_dir
    source.root = "Lassberg"
    source.quality = images.WORKING_QUALITY
    source.hits = source.cold_hits = source.misses = source.source_bytes = 0

    dest = source.path_for(remote)
    rel = dest.relative_to(cache_dir)
    record = {"name": "001.png", "bytes": 7, "sha256": "abc", "remote": remote}
    a_png(archive / rel)
    (archive / rel).with_name((archive / rel).name + SIDECAR_SUFFIX).write_text(
        json.dumps(record), encoding="utf-8")

    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", archive)
    monkeypatch.setattr(nextcloud.WebdavPageSource, "_download",
                        lambda self, r: (_ for _ in ()).throw(
                            AssertionError("the share was downloaded from")))

    got, back = source.fetch(remote)

    assert back == record
    assert (source.hits, source.cold_hits, source.misses) == (0, 1, 0)
    assert got.exists()


def test_the_webdav_source_still_downloads_when_the_tier_is_empty(tmp_path, monkeypatch):
    from utils import nextcloud

    cache_dir = tmp_path / "page_cache"
    source = nextcloud.WebdavPageSource.__new__(nextcloud.WebdavPageSource)
    source.cache_dir = cache_dir
    source.root = "Lassberg"
    source.quality = images.WORKING_QUALITY
    source.hits = source.cold_hits = source.misses = source.source_bytes = 0

    monkeypatch.setattr(images.config, "ATR_PAGE_CACHE_ARCHIVE", tmp_path / "empty")
    payload = a_png(tmp_path / "src.png").read_bytes()
    monkeypatch.setattr(nextcloud.WebdavPageSource, "_download",
                        lambda self, r: payload)

    _, record = source.fetch("Lassberg/letter-0001/001.png")

    assert record["name"] == "001.png"
    assert (source.cold_hits, source.misses) == (0, 1)


# ── the one rule that must not be written twice ────────────────────────────
def test_the_hit_test_is_the_callers_own():
    """Two copies of "is this the right file" would be two answers, and the
    cold tier has to be exactly as strict as the local one."""
    asked = []

    cold_fetch(Path("/nowhere/x.jpg"), Path("/nowhere"),
               lambda rec: asked.append(rec) or True, archive=None)

    assert asked == []          # no archive, so the question is never asked


def test_the_report_names_the_cold_tier_when_it_carried_something():
    """Folded into `hits`, the eviction's cost would be invisible."""
    source = (Path(__file__).resolve().parents[1] / "__main__.py").read_text(
        encoding="utf-8")

    assert "cold_hits" in source
    assert "from the archive" in source
