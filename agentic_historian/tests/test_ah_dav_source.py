"""Reading the corpus straight from the share, with no mirror and no mount.

The corpus outgrew tei's disk, so the plan was to mount the share. On
2026-09-18 two mount clients were tried: davfs2 and rclone both authenticate
against this share's endpoint and both are refused with 401, while `curl` and
`utils/nextcloud.py` are accepted against the *same URL* with the same token and
password. Whatever the difference is, it is not the credentials and not the
endpoint — and debugging somebody else's HTTP client is not what the corpus
needs.

So `WebdavPageSource` does the mount's job with the client that works: fetch one
page, keep its JPEG working copy, hand back a local path. It satisfies the same
`fetch(src) -> (Path, dict)` protocol as `PageCache`, so the batch runner cannot
tell the two apart — these tests pin that equivalence.
"""

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import atr_batch as batch                       # noqa: E402
from utils import images, nextcloud             # noqa: E402

SHARE = nextcloud.ShareRef(
    base_url="https://cloud.example.org/nextcloud", token="TOKEN", password="pw")


def _tif(colour=(10, 20, 30)) -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), colour).save(buf, format="TIFF")
    return buf.getvalue()


class FakeClient:
    """Just enough webdav4 to serve a two-letter share."""

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.downloads: list[str] = []

    def ls(self, rdir, detail=True):
        rdir = (rdir or "").strip("/")
        prefix = f"{rdir}/" if rdir else ""
        seen: dict[str, dict] = {}
        for path in self.files:
            if not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            head, _, tail = rest.partition("/")
            name = f"{prefix}{head}"
            seen[name] = {"name": name,
                          "type": "directory" if tail else "file",
                          "content_length": len(self.files.get(path, b""))}
        return list(seen.values()) if detail else list(seen)

    def download_file(self, remote, local):
        self.downloads.append(remote)
        Path(local).write_bytes(self.files[remote])


@pytest.fixture
def share_files():
    return {
        "Digitalisate/letter-0001/001.tif": _tif((10, 20, 30)),
        "Digitalisate/letter-0001/002.tif": _tif((40, 50, 60)),
        "Digitalisate/letter-0002/001.tif": _tif((200, 100, 50)),
        "Digitalisate/letter-0002/notes.txt": b"not a page",
        "atr_test_lassberg/report.md": b"# an earlier output folder",
    }


@pytest.fixture
def source(tmp_path, share_files, monkeypatch):
    src = nextcloud.WebdavPageSource(tmp_path / "cache", share=SHARE,
                                     root="Digitalisate")
    client = FakeClient(share_files)
    monkeypatch.setattr(type(src), "client", property(lambda self: client))
    src.fake = client
    return src


# ── naming the source ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("dav:Digitalisate", "Digitalisate"),
    ("dav:/Digitalisate/", "Digitalisate"),
    ("dav:", ""),
])
def test_a_dav_source_is_recognised_and_split(value, expected):
    assert nextcloud.is_remote_source(value)
    assert nextcloud.remote_source_root(value) == expected


@pytest.mark.parametrize("value", ["/mnt/gwdg", "data/nextcloud", "davfs:/x", ""])
def test_a_local_source_is_left_alone(value):
    assert not nextcloud.is_remote_source(value)


# ── discovery ────────────────────────────────────────────────────────────────

def test_only_pages_under_the_root_are_listed(source):
    paths = source.list_pages()

    assert paths == [
        "Digitalisate/letter-0001/001.tif",
        "Digitalisate/letter-0001/002.tif",
        "Digitalisate/letter-0002/001.tif",
    ]
    assert not any("atr_test_lassberg" in p for p in paths)   # not the corpus
    assert not any(p.endswith(".txt") for p in paths)


def test_the_keys_match_a_local_walk_of_the_same_tree(source, tmp_path):
    """The point of `pages_from_paths`: a page has the same key however it was
    read, so a corpus half-read from a mirror can be finished from the share."""
    local = tmp_path / "mirror" / "Digitalisate"
    for rel in ("letter-0001/001", "letter-0001/002", "letter-0002/001"):
        p = local / f"{rel}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")

    remote_keys = [p.key for p in
                   batch.pages_from_paths(source.list_pages(), "Digitalisate")]
    local_keys = [p.key for p in batch.discover_pages(local)]

    assert remote_keys == local_keys == [
        "letter-0001__001", "letter-0001__002", "letter-0002__001"]


def test_doc_id_survives_the_trip(source):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")

    assert [p.doc_id for p in pages] == ["letter-0001", "letter-0001", "letter-0002"]


def test_sampling_is_deterministic(source):
    paths = source.list_pages()
    first = [p.key for p in batch.pages_from_paths(paths, "Digitalisate", sample=2)]
    again = [p.key for p in batch.pages_from_paths(paths, "Digitalisate", sample=2)]

    assert first == again and len(first) == 2


def test_limit_and_sample_are_still_alternatives(source):
    with pytest.raises(ValueError, match="alternatives"):
        batch.pages_from_paths(source.list_pages(), "Digitalisate", limit=1, sample=1)


# ── fetching ─────────────────────────────────────────────────────────────────

def test_a_page_is_downloaded_once_and_cached(source, share_files):
    remote = "Digitalisate/letter-0001/001.tif"

    first, rec = source.fetch(remote)
    second, rec2 = source.fetch(remote)

    assert first == second and first.exists() and first.suffix == ".jpg"
    assert source.fake.downloads == [remote]          # not twice
    assert (source.misses, source.hits) == (1, 1)
    assert rec2 == rec


def test_the_record_digests_the_archival_bytes(source, share_files):
    remote = "Digitalisate/letter-0001/001.tif"

    dest, rec = source.fetch(remote)

    assert rec["sha256"] == hashlib.sha256(share_files[remote]).hexdigest()
    assert rec["bytes"] == len(share_files[remote])
    assert rec["name"] == "001.tif"
    assert rec["remote"] == remote
    # The kept file is a re-encoding, so it cannot vouch for the original itself.
    assert hashlib.sha256(dest.read_bytes()).hexdigest() != rec["sha256"]


def test_the_cache_mirrors_the_share_structure(source):
    a, _ = source.fetch("Digitalisate/letter-0001/001.tif")
    b, _ = source.fetch("Digitalisate/letter-0002/001.tif")

    assert a != b                                     # same filename, different letters
    assert a.parent.name == "letter-0001"


def test_no_temp_file_is_left_behind(source, tmp_path):
    source.fetch("Digitalisate/letter-0001/001.tif")

    assert not list((tmp_path / "cache").glob("*.part"))


def test_a_sidecar_for_another_file_is_not_trusted(source):
    dest, first = source.fetch("Digitalisate/letter-0001/001.tif")
    side = dest.with_name(dest.name + images.SIDECAR_SUFFIX)
    side.write_text(json.dumps({"name": "other.tif", "bytes": 1, "sha256": "dead"}),
                    encoding="utf-8")

    _, second = source.fetch("Digitalisate/letter-0001/001.tif")

    assert second["sha256"] == first["sha256"]
    assert source.misses == 2


# ── the runner cannot tell it from a local cache ─────────────────────────────

def _result(text="Euer Hochwohlgeboren"):
    return SimpleNamespace(text=text, lines=[], confidence=0.9, engine="trocr",
                           timing_ms=12, truncated=False, service_version="1")


def test_a_batch_runs_off_the_share(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    out = tmp_path / "out"
    seen: list[Path] = []

    outcome = batch.run_model(
        pages, "trocr-kurrent", "run", out,
        lambda p, m: (seen.append(Path(p)), _result())[1],
        retries=0, concurrency=1, cache=source)

    assert outcome.done == 3 and outcome.failed == 0
    assert all(p.is_relative_to(tmp_path / "cache") for p in seen)
    data = json.loads((out / "trocr-kurrent" / "letter-0001__001.json")
                      .read_text(encoding="utf-8"))
    assert data["source"]["name"] == "001.tif"
    assert data["source"]["working_copy"].endswith(".jpg")


def test_a_second_model_re_downloads_nothing(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    out = tmp_path / "out"

    for model in ("trocr-kurrent", "qwen3vl-german-xix-v2"):
        batch.run_model(pages, model, "run", out, lambda p, m: _result(),
                        retries=0, concurrency=1, cache=source)

    assert source.fake.downloads == source.list_pages()   # one each, not two
    assert source.hits == 3


def test_a_resumed_run_touches_the_share_not_at_all(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    out = tmp_path / "out"
    batch.run_model(pages, "trocr-kurrent", "run", out, lambda p, m: _result(),
                    retries=0, concurrency=1, cache=source)
    source.fake.downloads.clear()

    outcome = batch.run_model(pages, "trocr-kurrent", "run", out,
                              lambda p, m: _result(), retries=0, concurrency=1,
                              cache=source)

    assert outcome.skipped == 3
    assert source.fake.downloads == []


def test_a_page_that_cannot_be_fetched_fails_only_that_page(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    del source.fake.files["Digitalisate/letter-0001/002.tif"]

    outcome = batch.run_model(pages, "trocr-kurrent", "run", tmp_path / "out",
                              lambda p, m: _result(), retries=0, concurrency=1,
                              cache=source)

    assert (outcome.done, outcome.failed) == (2, 1)
    assert "letter-0001__002" in outcome.errors[0]
