"""Two corpora, the same filenames, one cache directory (#663).

On 14 October 2026 four batches read four manuscripts of the Berner Disputation,
ten pages each, one after another through the default cache directory. Every
manuscript numbers its pages ``page_N.jpg``. The first run filled the cache; the
next three read it back, and three of the four transcribed the wrong manuscript
while reporting success on every page.

What made it invisible is worth keeping in mind while reading these tests: the
readings were *plausible* — early modern German in a chancery hand, filed under
a manuscript that looks like that — and the only evidence was the ``sha256`` the
pipeline records for each source file. So the tests below assert on the bytes
that come back, not on paths or counters: a cache that hands over the wrong file
is wrong however tidy its bookkeeping looks.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from utils import images


def _png(path: Path, colour) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), colour).save(path, format="PNG")
    return path


@pytest.fixture
def two_manuscripts(tmp_path: Path) -> tuple[Path, Path]:
    """Two corpora whose pages are numbered identically, as the real ones are."""
    a = tmp_path / "A_V_1445_Schoeni"
    b = tmp_path / "A_V_1444_Cyro"
    _png(a / "page_114.png", (10, 20, 30))
    _png(b / "page_114.png", (200, 100, 50))
    return a, b


def test_the_second_corpus_does_not_get_the_first_ones_page(two_manuscripts, tmp_path):
    """The bug itself: same name, one cache, two different manuscripts."""
    a, b = two_manuscripts
    cache_dir = tmp_path / "cache"

    _, rec_a = images.PageCache(a, cache_dir).fetch(a / "page_114.png")
    _, rec_b = images.PageCache(b, cache_dir).fetch(b / "page_114.png")

    want_a = hashlib.sha256((a / "page_114.png").read_bytes()).hexdigest()
    want_b = hashlib.sha256((b / "page_114.png").read_bytes()).hexdigest()
    assert rec_a["sha256"] == want_a
    assert rec_b["sha256"] == want_b, (
        "the second corpus was served the first corpus's working copy — this is "
        "#663, and it is what made three runs read the wrong manuscript")


def test_the_working_copies_are_different_files(two_manuscripts, tmp_path):
    """Not just different records: different bytes on disk.

    A record that is right while the file beside it is wrong would still send a
    model the wrong page, so the digest check above is not sufficient on its own.
    """
    a, b = two_manuscripts
    cache_dir = tmp_path / "cache"

    dest_a, _ = images.PageCache(a, cache_dir).fetch(a / "page_114.png")
    dest_b, _ = images.PageCache(b, cache_dir).fetch(b / "page_114.png")

    assert dest_a != dest_b
    assert dest_a.read_bytes() != dest_b.read_bytes()


def test_two_roots_of_the_same_name_do_not_collide(tmp_path):
    """`A/pages` and `B/pages` are two corpora, not one.

    The readable half of the key is the root's basename, so this is the case a
    basename alone would get wrong.
    """
    a = tmp_path / "archive_one" / "pages"
    b = tmp_path / "archive_two" / "pages"
    _png(a / "page_1.png", (1, 2, 3))
    _png(b / "page_1.png", (250, 250, 250))
    cache_dir = tmp_path / "cache"

    _, rec_a = images.PageCache(a, cache_dir).fetch(a / "page_1.png")
    _, rec_b = images.PageCache(b, cache_dir).fetch(b / "page_1.png")

    assert rec_a["sha256"] != rec_b["sha256"]


def test_the_same_corpus_still_hits_its_own_cache(two_manuscripts, tmp_path):
    """The fix must not turn every second read into a miss.

    The cache exists to stop a 25 MB original crossing the network once per
    model; a key that changed between runs would be a correctness fix that costs
    the entire point of the class.
    """
    a, _ = two_manuscripts
    cache_dir = tmp_path / "cache"

    first = images.PageCache(a, cache_dir)
    first.fetch(a / "page_114.png")
    assert first.misses == 1 and first.hits == 0

    second = images.PageCache(a, cache_dir)          # a later run, same corpus
    second.fetch(a / "page_114.png")
    assert second.hits == 1, "a fresh PageCache over the same root must hit"


def test_a_file_replaced_under_its_own_name_is_a_miss(tmp_path):
    """The second net: same corpus, same name, different bytes.

    The root in the key cannot catch this one — only comparing something about
    the file itself can. Size is what is compared, because a digest would mean
    reading the original, which is the cost the cache exists to avoid.
    """
    root = tmp_path / "share"
    src = root / "page_1.png"
    _png(src, (10, 20, 30))
    cache_dir = tmp_path / "cache"

    cache = images.PageCache(root, cache_dir)
    _, first = cache.fetch(src)

    src.write_bytes(src.read_bytes() + b"\x00" * 4096)   # same name, new file
    _, second = cache.fetch(src)

    assert second["bytes"] != first["bytes"]
    assert second["sha256"] == hashlib.sha256(src.read_bytes()).hexdigest()


def test_the_root_tag_is_a_usable_directory_name(tmp_path):
    """Readable for a person, unique for the filesystem, safe as one component."""
    root = tmp_path / "A V 1445 (Schöni)"
    root.mkdir()
    tag = images.PageCache(root, tmp_path / "cache").root_tag

    assert "/" not in tag and " " not in tag
    assert tag.startswith("A_V_1445")          # still recognisable at a glance
    assert len(tag.rsplit("-", 1)[1]) == 12    # and unique by digest
