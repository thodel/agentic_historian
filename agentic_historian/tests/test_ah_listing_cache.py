"""The share listing, cached (`WebdavPageSource.list_pages`).

Enumerating this share is one PROPFIND per folder: **24 minutes** for its ~1000
folders, measured three times on 2026-09-21. It runs before a batch reads a
single page, at every start — so a 25-page smoke run spent more time listing
than recognising, and a resumed run paid it again for a list it already had.

What the listing describes is a scanning project's output folder: pages arrive
in batches days apart. A twelve-hour cache is therefore cheap to be wrong about,
and being wrong is visible — a page added since is simply not read until the
cache expires, or until someone passes `--no-listing-cache`.
"""

import json
import sys
import time
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils import nextcloud                     # noqa: E402

SHARE = nextcloud.ShareRef(
    base_url="https://cloud.example.org/nextcloud", token="TOKEN", password="pw")
OTHER = nextcloud.ShareRef(
    base_url="https://cloud.example.org/nextcloud", token="OTHER", password="pw")


class CountingClient:
    """A share that records how often it was walked."""

    def __init__(self, files=None):
        self.files = files or {
            "Digitalisate/letter-0001/001.tif": 10,
            "Digitalisate/letter-0002/001.tif": 10,
        }
        self.walks = 0

    def ls(self, rdir, detail=True):
        rdir = (rdir or "").strip("/")
        if not rdir or rdir == "Digitalisate":
            self.walks += 1
        prefix = f"{rdir}/" if rdir else ""
        seen: dict[str, dict] = {}
        for path, size in self.files.items():
            if not path.startswith(prefix):
                continue
            head, _, tail = path[len(prefix):].partition("/")
            name = f"{prefix}{head}"
            seen[name] = {"name": name, "content_length": size,
                          "type": "directory" if tail else "file"}
        return list(seen.values())


def make(tmp_path, monkeypatch, *, share=SHARE, ttl=None, client=None, sub="cache"):
    src = nextcloud.WebdavPageSource(tmp_path / sub, share=share,
                                     root="Digitalisate", listing_ttl=ttl)
    fake = client or CountingClient()
    monkeypatch.setattr(type(src), "client", property(lambda self: fake))
    src.fake = fake
    return src


# ── the saving ───────────────────────────────────────────────────────────────

def test_the_second_listing_does_not_walk_the_share(tmp_path, monkeypatch):
    src = make(tmp_path, monkeypatch)

    first = src.list_pages()
    second = src.list_pages()

    assert first == second
    assert src.fake.walks == 1


def test_a_fresh_source_reuses_the_stored_listing(tmp_path, monkeypatch):
    """The saving that matters: a *new run* is a new process."""
    make(tmp_path, monkeypatch).list_pages()

    again = make(tmp_path, monkeypatch)
    paths = again.list_pages()

    assert again.fake.walks == 0
    assert paths == ["Digitalisate/letter-0001/001.tif",
                     "Digitalisate/letter-0002/001.tif"]


# ── when it must not be used ─────────────────────────────────────────────────

def test_a_stale_listing_is_walked_again(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch, ttl=3600).list_pages()
    src = make(tmp_path, monkeypatch, ttl=3600)
    stale = json.loads(src._listing_path().read_text(encoding="utf-8"))
    stale["at"] = time.time() - 7200
    src._listing_path().write_text(json.dumps(stale), encoding="utf-8")

    src.list_pages()

    assert src.fake.walks == 1


def test_ttl_zero_turns_it_off(tmp_path, monkeypatch):
    make(tmp_path, monkeypatch, ttl=0).list_pages()
    src = make(tmp_path, monkeypatch, ttl=0)

    src.list_pages()

    assert src.fake.walks == 1
    assert not list((tmp_path / "cache").glob(".listing-*.json"))


def test_a_limit_neither_reads_nor_writes_the_cache(tmp_path, monkeypatch):
    """A partial walk stored as the corpus would silently shorten every run."""
    src = make(tmp_path, monkeypatch)

    assert len(src.list_pages(limit=1)) == 1
    assert not list((tmp_path / "cache").glob(".listing-*.json"))

    src.list_pages()
    assert src.fake.walks == 2                   # the limit walk, then the full one


def test_another_share_does_not_get_this_ones_corpus(tmp_path, monkeypatch):
    """The two differ by a token in a URL — the kind of difference a filename
    hides, so the key is a digest of endpoint, token and root."""
    a = make(tmp_path, monkeypatch, share=SHARE)
    a.list_pages()

    b = make(tmp_path, monkeypatch, share=OTHER)
    b.list_pages()

    assert a._listing_path() != b._listing_path()
    assert b.fake.walks == 1


def test_a_different_folder_of_the_same_share_is_its_own_listing(tmp_path, monkeypatch):
    a = make(tmp_path, monkeypatch)
    a.list_pages()
    b = nextcloud.WebdavPageSource(tmp_path / "cache", share=SHARE, root="Andere")

    assert a._listing_path() != b._listing_path()


# ── a cache must never break a run ───────────────────────────────────────────

@pytest.mark.parametrize("content", [
    "{half",                                     # interrupted write
    '{"at": 123}',                               # no paths
    '{"paths": ["a"]}',                          # no timestamp
    '{"at": 1e9, "paths": "not a list"}',
    '{"at": 1e9, "paths": [1, 2, 3]}',           # not paths
])
def test_an_unusable_cache_is_a_miss_not_an_error(tmp_path, monkeypatch, content):
    src = make(tmp_path, monkeypatch)
    src._listing_path().parent.mkdir(parents=True, exist_ok=True)
    src._listing_path().write_text(content, encoding="utf-8")

    paths = src.list_pages()

    assert paths and src.fake.walks == 1


def test_an_unwritable_cache_dir_does_not_lose_the_listing(tmp_path, monkeypatch):
    src = make(tmp_path, monkeypatch)
    monkeypatch.setattr(nextcloud.images, "_write_atomic_bytes",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("read-only")))

    assert len(src.list_pages()) == 2
