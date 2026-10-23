"""The page cache's cold tier as a source a batch may read (#686).

A daily job moves working copies older than two days from the page cache to the
research share, paths preserved (#487). So on any given day the cache holds the
last two days and the cold tier holds the corpus — on 2026-10-22, 0 pages against
6783. That day a dry run over `dav:` began the full walk of the share and died
twelve minutes in on a folder that answered 404, while every page it was going
to read sat in the cold tier under the same relative path, hence the same key.
The run went ahead from the CLI, which has no containment check; from a session
`_roots()` refused it.

Same case as the cache (`test_ah_page_cache_is_a_corpus.py`): a directory this
stack wrote, keyed like the share, readable through the runs already. The
containment rule is unchanged and is what these tests mostly assert.
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
    """A staging dir, a comparison root, a mount, a page cache and its cold tier."""
    for name, attr in (("nextcloud", "NEXTCLOUD_STAGING_DIR"),
                       ("vlm_test", "VLM_TEST_ROOT"),
                       ("gwdg", "ATR_MOUNT_DIR"),
                       ("page_cache", "ATR_PAGE_CACHE"),
                       ("archive", "ATR_PAGE_CACHE_ARCHIVE")):
        (tmp_path / name).mkdir()
        monkeypatch.setattr(config, attr, tmp_path / name)
    return tmp_path


# ── the cold tier is a root ──────────────────────────────────────────────────

def test_the_cold_tier_is_among_the_corpus_roots(roots):
    assert (roots / "archive").resolve() in jobs._roots()


def test_the_cold_tier_resolves_as_a_source(roots):
    """The 2026-10-22 call that was refused: the archive itself, with
    `keys_from` doing the selecting."""
    assert jobs.resolve_source(str(roots / "archive")) == (roots / "archive").resolve()


def test_a_holding_inside_the_cold_tier_resolves(roots):
    (roots / "archive" / "Briefe UB Freiburg").mkdir(parents=True)
    source = jobs.resolve_source(str(roots / "archive" / "Briefe UB Freiburg"))
    assert source == (roots / "archive" / "Briefe UB Freiburg").resolve()


def test_the_containment_rule_still_holds(roots):
    """Adding a root must not become adding a file server — the research share
    around the archive is exactly the thing a caller must not be able to name."""
    (roots / "tei-archive-neighbour").mkdir()
    with pytest.raises(jobs.JobError) as err:
        jobs.resolve_source(str(roots / "tei-archive-neighbour"))
    assert "outside the corpus roots" in str(err.value)
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(str(roots / "archive" / ".." / "tei-archive-neighbour"))


def test_an_unset_cold_tier_adds_no_root(roots, monkeypatch):
    """Empty is the default and right for any host but tei."""
    monkeypatch.setattr(config, "ATR_PAGE_CACHE_ARCHIVE", None)
    assert (roots / "archive").resolve() not in jobs._roots()
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(str(roots / "archive"))


# ── and it gets a cache, because it is on the share ──────────────────────────

def test_the_cold_tier_gets_the_configured_cache(roots):
    """1.5 MB a page over CIFS, opened once per model: the same reason the mount
    gets one. Not the re-encoding case — a JPEG source is byte-copied."""
    source = jobs.resolve_source(str(roots / "archive"))
    assert jobs.cache_dir_for(source) == roots / "page_cache"


def test_the_cold_tier_gets_the_default_cache_when_none_is_configured(roots, monkeypatch):
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    source = jobs.resolve_source(str(roots / "archive"))
    assert jobs.cache_dir_for(source) == Path(config.page_cache_dir())


def test_the_cache_is_still_not_its_own_cache(roots):
    """The rule #663-time added must survive the new root next to it."""
    source = jobs.resolve_source(str(roots / "page_cache"))
    assert jobs.cache_dir_for(source) is None


def test_a_plain_local_directory_still_gets_no_cache(roots, monkeypatch):
    """The clause is for the cold tier, not for every directory under the sun."""
    monkeypatch.setattr(config, "ATR_PAGE_CACHE", None)
    (roots / "vlm_test" / "some-run").mkdir()
    source = jobs.resolve_source(str(roots / "vlm_test" / "some-run"))
    assert jobs.cache_dir_for(source) is None


def test_the_tool_says_so():
    src = (PKG / "mcp_atr" / "server.py").read_text(encoding="utf-8")
    head = src[src.index("def start_batch("):src.index("def start_batch(") + 4000]
    assert "cold tier" in head and "ATR_PAGE_CACHE_ARCHIVE" in head
