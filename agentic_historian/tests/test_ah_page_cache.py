"""Reading a corpus that lives on a mounted share (page cache).

The share is no longer mirrored to tei: it is mounted and read in place. That
removes the 160 GB copy and introduces a cost the runner did not have before —
opening a page is a network transfer of an uncompressed 25 MB TIFF, and a batch
opens every page at least once per model. These tests pin the three properties
that make the mount workable: an original is read exactly once, the digest the
result carries belongs to the *original* rather than to the working copy, and a
resumed run does not touch the mount for pages it already has.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import atr_batch as batch
from utils import images


# ── fixtures ─────────────────────────────────────────────────────────────────

def _png(path: Path, colour=(10, 20, 30)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), colour).save(path, format="PNG")
    return path


@pytest.fixture
def share(tmp_path: Path) -> Path:
    """A two-page 'share' with the one-folder-per-letter shape of the real one."""
    root = tmp_path / "mount" / "digitalisate"
    _png(root / "letter-0001" / "001.png", (10, 20, 30))
    _png(root / "letter-0002" / "001.png", (200, 100, 50))
    return root


def _result(text="Euer Hochwohlgeboren"):
    return SimpleNamespace(text=text, lines=[], confidence=0.9, engine="trocr",
                           timing_ms=12, truncated=False, service_version="1")


# ── the cache itself ─────────────────────────────────────────────────────────

def test_fetch_converts_once_and_hits_thereafter(share, tmp_path):
    cache = images.PageCache(share, tmp_path / "cache")
    src = share / "letter-0001" / "001.png"

    first, rec = cache.fetch(src)
    second, rec2 = cache.fetch(src)

    assert first == second and first.exists()
    assert first.suffix == ".jpg"           # converted, not copied
    assert (cache.misses, cache.hits) == (1, 1)
    assert rec2 == rec


def test_record_digests_the_original_not_the_working_copy(share, tmp_path):
    src = share / "letter-0001" / "001.png"
    cache = images.PageCache(share, tmp_path / "cache")

    dest, rec = cache.fetch(src)

    assert rec["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()
    assert rec["bytes"] == src.stat().st_size
    assert rec["name"] == "001.png"
    # The point of the sidecar: the JPEG cannot vouch for the original itself.
    assert hashlib.sha256(dest.read_bytes()).hexdigest() != rec["sha256"]


def test_cache_mirrors_the_share_structure(share, tmp_path):
    cache = images.PageCache(share, tmp_path / "cache")
    a, _ = cache.fetch(share / "letter-0001" / "001.png")
    b, _ = cache.fetch(share / "letter-0002" / "001.png")

    assert a != b                            # same filename, different letters
    assert a.relative_to(tmp_path / "cache").parts[0] == "letter-0001"


def test_a_file_already_compressed_is_copied_not_re_encoded(tmp_path):
    root = tmp_path / "share"
    src = root / "page.jpg"
    root.mkdir()
    from PIL import Image
    Image.new("RGB", (40, 30), (1, 2, 3)).save(src, format="JPEG", quality=90)

    cache = images.PageCache(root, tmp_path / "cache")
    dest, rec = cache.fetch(src)

    assert dest.read_bytes() == src.read_bytes()
    assert rec["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


def test_a_sidecar_for_another_file_is_not_trusted(tmp_path):
    """``a.tif`` and ``a.png`` both want ``a.jpg``; reusing one for the other
    would attach a reading to bytes that never produced it."""
    root = tmp_path / "share"
    _png(root / "a.png", (1, 2, 3))
    cache = images.PageCache(root, tmp_path / "cache")
    dest, first = cache.fetch(root / "a.png")

    side = dest.with_name(dest.name + images.SIDECAR_SUFFIX)
    side.write_text(json.dumps({"name": "a.tif", "bytes": 1, "sha256": "dead"}),
                    encoding="utf-8")
    _, second = cache.fetch(root / "a.png")

    assert second["sha256"] == first["sha256"]
    assert cache.misses == 2                 # re-fetched rather than trusted


def test_an_unparsable_sidecar_is_a_miss(share, tmp_path):
    cache = images.PageCache(share, tmp_path / "cache")
    dest, _ = cache.fetch(share / "letter-0001" / "001.png")
    dest.with_name(dest.name + images.SIDECAR_SUFFIX).write_text("{half", encoding="utf-8")

    _, rec = cache.fetch(share / "letter-0001" / "001.png")

    assert rec["sha256"]
    assert cache.misses == 2


def test_a_page_outside_the_root_is_refused(share, tmp_path):
    cache = images.PageCache(share, tmp_path / "cache")
    stray = _png(tmp_path / "elsewhere" / "x.png")

    with pytest.raises(ValueError):
        cache.fetch(stray)


# ── the runner reading through it ────────────────────────────────────────────

def test_the_model_is_handed_the_working_copy(share, tmp_path):
    pages = batch.discover_pages(share)
    cache = images.PageCache(share, tmp_path / "cache")
    seen: list[Path] = []

    def recognise(path, model):
        seen.append(Path(path))
        return _result()

    batch.run_model(pages, "trocr-kurrent", "run", tmp_path / "out", recognise,
                    retries=0, concurrency=1, cache=cache)

    assert seen and all(p.is_relative_to(tmp_path / "cache") for p in seen)


def test_the_result_carries_the_originals_digest(share, tmp_path):
    pages = batch.discover_pages(share)
    cache = images.PageCache(share, tmp_path / "cache")
    out = tmp_path / "out"

    batch.run_model(pages, "trocr-kurrent", "run", out, lambda p, m: _result(),
                    retries=0, concurrency=1, cache=cache)

    key = pages[0].key
    data = json.loads((out / "trocr-kurrent" / f"{key}.json").read_text(encoding="utf-8"))
    original = pages[0].path
    assert data["source"]["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert data["source"]["name"] == original.name
    assert data["source"]["key"] == key
    assert data["source"]["working_copy"].endswith(".jpg")


def test_the_original_is_read_once_across_two_models(share, tmp_path):
    """The reason the cache exists: a second model must not re-pay the transfer."""
    pages = batch.discover_pages(share)
    cache = images.PageCache(share, tmp_path / "cache")
    out = tmp_path / "out"

    for model in ("trocr-kurrent", "kraken-dh"):
        batch.run_model(pages, model, "run", out, lambda p, m: _result(),
                        retries=0, concurrency=1, cache=cache)

    assert cache.misses == len(pages)        # one fetch per page, not per pass
    assert cache.hits == len(pages)


def test_a_resumed_run_does_not_touch_the_share(share, tmp_path):
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    batch.run_model(pages, "trocr-kurrent", "run", out, lambda p, m: _result(),
                    retries=0, concurrency=1,
                    cache=images.PageCache(share, tmp_path / "cache"))

    fresh = images.PageCache(share, tmp_path / "cache2")
    outcome = batch.run_model(pages, "trocr-kurrent", "run", out,
                              lambda p, m: _result(), retries=0, concurrency=1,
                              cache=fresh)

    assert outcome.skipped == len(pages)
    assert (fresh.hits, fresh.misses) == (0, 0)


def test_a_fetch_that_fails_once_is_recovered_by_the_second_pass(share, tmp_path):
    """A page the share refused once is read in the second pass (#456), and it
    is counted once: done, not failed, and out of the error list. It used to
    stay failed until somebody re-ran the batch by hand."""
    pages = batch.discover_pages(share)

    class Broken:
        def __init__(self):
            self.n = 0

        def fetch(self, src):
            self.n += 1
            if self.n == 1:
                raise OSError("mount went away")
            return src, {"name": Path(src).name, "bytes": 1, "sha256": "x"}

    outcome = batch.run_model(pages, "trocr-kurrent", "run", tmp_path / "out",
                              lambda p, m: _result(), retries=0, concurrency=1,
                              cache=Broken())

    assert (outcome.failed, outcome.done) == (0, len(pages))
    assert outcome.errors == [] and outcome.source_error_keys == []
    assert not outcome.aborted


def test_a_page_the_share_never_gives_up_stays_failed(share, tmp_path):
    """The other half: a fetch that fails every time costs that page and says
    why, in the error list a reader scans."""
    pages = batch.discover_pages(share)
    first = pages[0]

    class AlwaysBroken:
        def fetch(self, src):
            if str(src) == str(first.path):
                raise OSError("mount went away")
            return src, {"name": Path(src).name, "bytes": 1, "sha256": "x"}

    outcome = batch.run_model(pages, "trocr-kurrent", "run", tmp_path / "out",
                              lambda p, m: _result(), retries=0, concurrency=1,
                              cache=AlwaysBroken())

    assert (outcome.failed, outcome.done) == (1, len(pages) - 1)
    assert "mount went away" in outcome.errors[0]
    assert outcome.source_errors == 1 and outcome.source_error_keys == [first.key]
    assert not outcome.aborted


def test_without_a_cache_nothing_changes(share, tmp_path):
    """The mirror path still works: the page is read where it is, and hashed there."""
    pages = batch.discover_pages(share)
    out = tmp_path / "out"
    seen: list[Path] = []

    batch.run_model(pages, "trocr-kurrent", "run", out,
                    lambda p, m: (seen.append(Path(p)), _result())[1],
                    retries=0, concurrency=1)

    data = json.loads(
        (out / "trocr-kurrent" / f"{pages[0].key}.json").read_text(encoding="utf-8"))
    assert seen[0] == pages[0].path
    assert "working_copy" not in data["source"]
    assert data["source"]["sha256"] == hashlib.sha256(
        pages[0].path.read_bytes()).hexdigest()
