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
from xml.etree import ElementTree
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


# ── the share answers to two spellings of the same folder ────────────────────

@pytest.mark.parametrize("typed", ["Digitalisate", "digitalisate", "DIGITALISATE"])
def test_the_root_spelling_does_not_change_a_page_key(typed):
    """This share lists `Digitalisate/` at its root, while a walk requested as
    `digitalisate` comes back spelled that way throughout. An unstripped prefix
    is not an error — it is a different key for the same page, so a corpus
    already half-read would quietly be read again."""
    paths = [f"{typed}/letter-0001/001.tif", f"{typed}/letter-0002/001.tif"]

    pages = batch.pages_from_paths(paths, typed)

    assert [p.key for p in pages] == ["letter-0001__001", "letter-0002__001"]


@pytest.mark.parametrize("listed,typed", [
    ("digitalisate", "Digitalisate"),
    ("Digitalisate", "digitalisate"),
])
def test_a_root_typed_in_the_other_case_still_strips(listed, typed):
    pages = batch.pages_from_paths([f"{listed}/letter-0001/001.tif"], typed)

    assert [p.key for p in pages] == ["letter-0001__001"]
    assert pages[0].doc_id == "letter-0001"


def test_a_root_that_merely_shares_a_prefix_is_not_stripped(source):
    """`Digitalisate2` is not inside `Digitalisate`."""
    pages = batch.pages_from_paths(["Digitalisate2/001.tif"], "Digitalisate")

    assert pages[0].key == "Digitalisate2__001"


def test_the_cache_path_ignores_root_case(tmp_path):
    src = nextcloud.WebdavPageSource(tmp_path / "cache", share=SHARE,
                                     root="Digitalisate")

    where = src.path_for("digitalisate/letter-0001/001.tif")

    assert where == tmp_path / "cache" / "letter-0001" / "001.jpg"


# ── a flaky source must not look like a bad model ────────────────────────────

class Flaky:
    """A source that fails `fails` times per page before succeeding."""

    def __init__(self, inner, fails: int, exc=None):
        self.inner = inner
        self.fails = fails
        self.exc = exc or RuntimeError("500 Internal Server Error")
        self.attempts: dict[str, int] = {}

    def fetch(self, src):
        key = str(src)
        self.attempts[key] = self.attempts.get(key, 0) + 1
        if self.attempts[key] <= self.fails:
            raise self.exc
        return self.inner.fetch(src)


def test_a_transient_source_error_is_retried(source, tmp_path):
    """The bug this closes: on 2026-09-21 the share answered four concurrent
    25 MB downloads with 500s and unparsable XML, and a 6742-page run died after
    29 pages — with a report that blamed the model."""
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    flaky = Flaky(source, fails=2)

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=2, concurrency=1, cache=flaky,
                              sleep=lambda _s: None)

    assert (outcome.done, outcome.failed) == (3, 0)
    assert all(n == 3 for n in flaky.attempts.values())     # two failures, then through


def test_a_page_that_never_arrives_costs_only_that_page(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    one = pages[0].path

    class Gone:
        def fetch(self, src):
            if str(src) == str(one):
                raise RuntimeError("404 Not Found")
            return source.fetch(src)

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=1, concurrency=1, cache=Gone(),
                              sleep=lambda _s: None)

    assert (outcome.done, outcome.failed) == (2, 1)
    assert not outcome.aborted


def test_the_error_says_it_was_the_source(source, tmp_path):
    """`ABANDONED — … HTTPStatusError` read like the model's fault. It was not."""
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")

    class Broken:
        def fetch(self, src):
            raise RuntimeError("500 Internal Server Error")

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=0, concurrency=1, cache=Broken(),
                              sleep=lambda _s: None)

    assert outcome.errors and all(e.split(": ", 1)[1].startswith("source:")
                                  for e in outcome.errors)



def test_the_share_coming_back_after_the_pause_costs_no_page(source, tmp_path, monkeypatch):
    """#456: five source failures in a row pause the run and then retry. When the
    share is back, those pages are read and counted once — done, not failed, and
    out of the error list. The retry used to be handed a page *outcome* instead
    of the page, which raised AttributeError and ended the run."""
    monkeypatch.setattr(batch, "MAX_CONSECUTIVE_SOURCE_FAILURES", 2)
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")
    slept: list[float] = []

    class DownThenBack:
        def __init__(self) -> None:
            self.down = True

        def fetch(self, src):
            if self.down:
                raise RuntimeError("500 Internal Server Error")
            return source.fetch(src)

    share = DownThenBack()

    def wake(seconds: float) -> None:
        slept.append(seconds)
        share.down = False          # the share is back while the run waits

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=0, concurrency=1, cache=share,
                              sleep=wake, max_consecutive_failures=5)

    assert slept, "five source failures in a row have to pause the run"
    assert not outcome.aborted, outcome.aborted
    assert (outcome.done, outcome.failed) == (len(pages), 0)
    assert outcome.source_errors == 0 and outcome.errors == []

def test_recognition_is_not_retried_by_the_fetch_retry(source, tmp_path):
    """The two budgets stay separate: a page is fetched once and then read, and
    a recogniser retry must not re-download it."""
    pages = batch.pages_from_paths(source.list_pages()[:1], "Digitalisate")
    calls = {"n": 0}

    def flaky_recognise(path, model):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("gateway is loading the model")
        return _result()

    batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run", tmp_path / "out",
                    flaky_recognise, retries=2, concurrency=1, cache=source,
                    sleep=lambda _s: None)

    assert calls["n"] == 2                       # the recogniser was retried
    assert len(source.fake.downloads) == 1       # the page was not


# ── the cause, kept (#456 point 4) ───────────────────────────────────────────
#
# The run that died after 1920 pages recorded `ParseError: not well-formed
# (invalid token): line 2, column 131` — a parser complaining about a document
# nobody in this repo asked for, with no traceback and no status. Which webdav4
# call got a body that is not XML stayed open because of that.

def test_a_parse_error_is_reported_as_the_share_answering_badly():
    err = ElementTree.ParseError("not well-formed (invalid token): line 2, column 131")
    said = batch.describe_source_error(err)
    assert "not XML" in said
    assert "line 2, column 131" in said


def test_an_http_status_is_said_rather_than_reconstructed():
    class Response:
        status_code = 500
        reason_phrase = "Internal Server Error"

    exc = RuntimeError("boom")
    exc.response = Response()
    assert batch.describe_source_error(exc) == "share answered 500 Internal Server Error"


def test_an_ordinary_error_keeps_its_own_words():
    assert batch.describe_source_error(TimeoutError("timed out")) == "timed out"


def test_the_manifest_line_carries_the_description(source, tmp_path):
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")

    class NotXml:
        def fetch(self, src):
            raise ElementTree.ParseError("not well-formed (invalid token): line 2, column 131")

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=0, concurrency=1, cache=NotXml(),
                              sleep=lambda _s: None)

    assert outcome.errors
    assert all("source: ParseError: share answered with a body that is not XML" in e
               for e in outcome.errors)


def test_the_first_error_of_a_kind_is_logged_with_its_traceback():
    """One traceback per kind per run: the evidence that was missing, without
    a share that is down writing hundreds of them."""
    seen: set[str] = set()
    err = ElementTree.ParseError("not well-formed: line 2, column 131")
    assert batch.log_first_source_error(err, "page-1", seen) is True
    assert batch.log_first_source_error(err, "page-2", seen) is False
    assert batch.log_first_source_error(RuntimeError("500"), "page-3", seen) is True
    assert seen == {"ParseError", "RuntimeError"}


def test_without_a_set_nothing_is_logged():
    """`_recognise_page` may be called without the runner's set (a direct call in
    a test, say), and a missing set must not mean a traceback per page."""
    assert batch.log_first_source_error(RuntimeError("500"), "page-1", None) is False


# ── the report says which failures are whose (#456 point 5) ──────────────────

def _failed_model(model: str, errors: list[str]) -> batch.ModelOutcome:
    return batch.ModelOutcome(model=model, failed=len(errors), errors=list(errors))


def _report(models: list[batch.ModelOutcome], tmp_path) -> batch.BatchReport:
    return batch.BatchReport(run="run", out_root=tmp_path, pages=3, models=models)


def test_the_report_separates_a_share_outage_from_a_bad_reading(tmp_path):
    """31 of 48 failures in the run this comes from were the share. One list left
    the reader to sort out which of them meant "fetch again"."""
    text = batch.format_report(_report([_failed_model("qwen3.5-4b-german-xix-v2", [
        "Marbach_0001: source: ParseError: share answered with a body that is not XML",
        "Marbach_0002: source: HTTPStatusError: share answered 500 Internal Server Error",
        "Marbach_0009: KrakenClientError: Kraken service 502",
    ])], tmp_path))

    share, reading = "## Pages the share could not hand over", "## Pages that failed recognition"
    assert share in text and reading in text
    assert text.index(share) < text.index("Marbach_0001") < text.index(reading)
    assert text.index(reading) < text.index("Marbach_0009")
    assert "skips every page already on disk" in text


def test_a_model_with_only_reading_failures_gets_no_share_section(tmp_path):
    text = batch.format_report(_report([_failed_model("m", [
        "p1: KrakenClientError: Kraken service 502"])], tmp_path))
    assert "## Pages the share could not hand over" not in text
    assert "## Pages that failed recognition" in text


def test_a_failure_count_without_lines_is_still_reported(tmp_path):
    """A rebuilt report has counts and no error lines; it must not lose them."""
    text = batch.format_report(_report([batch.ModelOutcome(model="m", failed=3)], tmp_path))
    assert "## Pages that failed" in text and "3 page(s)" in text


def test_a_share_that_never_comes_back_ends_the_run_saying_so(source, tmp_path, monkeypatch):
    """#456's second acceptance case: past the budget the run ends — but with the
    share named, not with "too many consecutive failures", which sent the reader
    looking at a model that had done nothing wrong."""
    monkeypatch.setattr(batch, "MAX_CONSECUTIVE_SOURCE_FAILURES", 2)
    monkeypatch.setattr(batch, "SOURCE_PAUSE_BUDGET", 2)
    pages = batch.pages_from_paths(source.list_pages(), "Digitalisate")

    class Down:
        def fetch(self, src):
            raise ElementTree.ParseError("not well-formed (invalid token): line 2, column 131")

    outcome = batch.run_model(pages, "qwen3.5-4b-german-xix-v2", "run",
                              tmp_path / "out", lambda p, m: _result(),
                              retries=0, concurrency=1, cache=Down(),
                              sleep=lambda _s: None)

    assert "share unavailable" in outcome.aborted
    assert "nothing is wrong with the model" in outcome.aborted
    assert "consecutive failures" not in outcome.aborted
    assert outcome.source_errors == len(pages)
