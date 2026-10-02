"""The page cache as a source a batch may read (`jobs._roots`, `cache_dir_for`).

On 2026-10-02 the GWDG share answered **500 to every PROPFIND** — both
`public.php/webdav` and `public.php/dav/files/…` — and `ATR_MOUNT_DIR` was an
empty directory. Neither route to the pages worked. Some 6700 of them sat on tei's
disk as JPEG working copies and could not be named as a source, so an eight-model
comparison that needed five pages waited on somebody else's server.

The cache belongs here on its own merits, not only as a fallback. It is keyed by
each page's path relative to the corpus root, so a page read from it gets the same
key it would have from the share or the mount — the property that lets one corpus
be read partly one way and partly another. And like `VLM_TEST_ROOT` it is a
directory this stack wrote itself, so admitting it widens nothing: every page of
it was already readable through the runs it produced.

The containment rule is unchanged and is what these tests mostly assert: a caller
chooses among the roots, never a path outside them.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                              # noqa: E402
from mcp_atr import jobs                   # noqa: E402


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A staging dir, a comparison root, a mount and a page cache."""
    for name, attr in (("nextcloud", "NEXTCLOUD_STAGING_DIR"),
                       ("vlm_test", "VLM_TEST_ROOT"),
                       ("gwdg", "ATR_MOUNT_DIR"),
                       ("page_cache", "ATR_PAGE_CACHE")):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(config, attr, tmp_path / name)
    return tmp_path


# ── the cache is a root ──────────────────────────────────────────────────────

def test_the_page_cache_is_among_the_corpus_roots(roots):
    assert (roots / "page_cache").resolve() in jobs._roots()


def test_a_source_in_the_cache_resolves(roots):
    (roots / "page_cache" / "Briefe UB Freiburg").mkdir(parents=True)
    source = jobs.resolve_source(str(roots / "page_cache" / "Briefe UB Freiburg"))
    assert source == (roots / "page_cache" / "Briefe UB Freiburg").resolve()


def test_the_containment_rule_still_holds(roots):
    """The assertion that matters for a tool on the public internet. Adding a
    root must not become adding a file server."""
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_source(str(roots / "page_cache" / ".." / ".." / "etc"))
    assert "outside the corpus roots" in str(err.value)


def test_an_unset_cache_adds_no_root(roots, monkeypatch):
    """`ATR_PAGE_CACHE` is optional — empty means "read pages where they are"."""
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    assert len(jobs._roots()) == 3


# ── and never its own cache ──────────────────────────────────────────────────

def test_the_cache_is_not_its_own_cache_dir(roots):
    """Pointing one at itself would re-encode the JPEG a generation per model:
    read page X, write its "working copy" over page X, next model reads that."""
    assert jobs.cache_dir_for(roots / "page_cache") is None


def test_a_subdirectory_of_the_cache_gets_no_cache_either(roots):
    inner = roots / "page_cache" / "Aarau" / "upload"
    inner.mkdir(parents=True)
    assert jobs.cache_dir_for(inner) is None


def test_the_mount_still_gets_the_cache(roots):
    """The case the cache exists for: a page on the mount is a 25 MB transfer and
    a batch opens every page at least once per model."""
    assert jobs.cache_dir_for(roots / "gwdg") == Path(config.ATR_PAGE_CACHE)


def test_a_dav_source_still_gets_the_cache(roots):
    """It is the only copy that ever lands on this disk; the runner refuses the
    run without one."""
    assert jobs.cache_dir_for("dav:digitalisate") == Path(config.ATR_PAGE_CACHE)


def test_a_directory_whose_name_merely_starts_like_the_cache(roots, monkeypatch):
    """`page_cache2` is not inside `page_cache`. Prefix matching on strings is how
    `Digitalisate2` was once read as part of `Digitalisate`."""
    sibling = roots / "page_cache2"
    sibling.mkdir()
    monkeypatch.setattr(config, "NEXTCLOUD_STAGING_DIR", sibling)
    assert jobs.cache_dir_for(sibling) == Path(config.ATR_PAGE_CACHE)
