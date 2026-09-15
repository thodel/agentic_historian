"""Nextcloud public-share ingestion (utils/nextcloud.py).

Offline: ``webdav4.client.Client`` is replaced by a fake, so nothing here touches
the network. Run from the repo root::

    pytest agentic_historian/tests/test_ah_nextcloud_share.py
"""

import sys
from itertools import islice
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                       # noqa: E402
from utils import nextcloud         # noqa: E402


# ── the share URL ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,base,token", [
    ("https://cloud.example.org/nextcloud/s/AbC123",
     "https://cloud.example.org/nextcloud", "AbC123"),
    ("https://cloud.example.org/s/AbC123",
     "https://cloud.example.org", "AbC123"),
    ("https://cloud.example.org/nextcloud/index.php/s/AbC123",
     "https://cloud.example.org/nextcloud", "AbC123"),
    # what a password-protected share redirects to — and therefore what gets pasted
    ("https://cloud.example.org/nextcloud/s/AbC123/authenticate/showshare",
     "https://cloud.example.org/nextcloud", "AbC123"),
    ("https://cloud.example.org/nextcloud/s/AbC123/",
     "https://cloud.example.org/nextcloud", "AbC123"),
    ("https://cloud.example.org/nextcloud/s/AbC123/download",
     "https://cloud.example.org/nextcloud", "AbC123"),
    ("https://cloud.example.org/nextcloud/s/A-b_C1/?path=/x",
     "https://cloud.example.org/nextcloud", "A-b_C1"),
])
def test_every_shape_nextcloud_hands_out_parses(url, base, token):
    """A person pastes whatever the browser showed them. Each of these is a real
    Nextcloud share URL shape, and picking the token out of them is this
    function's whole job — getting it wrong surfaces much later as an
    authentication failure against an endpoint nobody meant to call."""
    ref = nextcloud.parse_share_url(url)
    assert (ref.base_url, ref.token) == (base, token)


@pytest.mark.parametrize("bad", ["", "   ", "cloud.example.org/s/AbC", "not a url",
                                 "https://cloud.example.org/nextcloud/apps/files"])
def test_a_url_without_a_token_is_an_error_not_a_guess(bad):
    with pytest.raises(nextcloud.NextcloudError):
        nextcloud.parse_share_url(bad)


def test_endpoints_are_derived_from_the_share():
    ref = nextcloud.parse_share_url("https://cloud.example.org/nc/s/TOK", "pw")
    assert ref.webdav_url == "https://cloud.example.org/nc/public.php/webdav"
    assert ref.dav_url == "https://cloud.example.org/nc/public.php/dav/files/TOK"


def test_the_share_never_prints_its_password():
    """ShareRef ends up in log lines and error messages; a password in either is a
    password in a file somebody will paste into an issue."""
    ref = nextcloud.parse_share_url("https://cloud.example.org/nc/s/TOK", "hunter2")
    assert "hunter2" not in str(ref)
    assert "TOK" in str(ref)


# ── a fake WebDAV server ─────────────────────────────────────────────────────

class FakeClient:
    """Just enough webdav4.Client to exercise the walk + download paths."""

    def __init__(self, tree: dict[str, list[dict]], *, fail: set[str] = frozenset(),
                 serve: set[str] | None = None, url: str = ""):
        self.tree = tree
        self.fail = set(fail)
        self.url = url
        #: Endpoints this fake will answer at all; None = any.
        self.serve = serve
        self.downloaded: list[str] = []

    def ls(self, path, detail=True):
        if self.serve is not None and self.url not in self.serve:
            raise RuntimeError("404 Not Found")
        key = (path or "").strip("/")
        if key not in self.tree:
            raise RuntimeError(f"404 {key}")
        return self.tree[key] if detail else [e["name"] for e in self.tree[key]]

    def download_file(self, remote, local):
        if remote in self.fail:
            raise RuntimeError("connection reset")
        self.downloaded.append(remote)
        size = next(
            (e.get("content_length", 0)
             for entries in self.tree.values() for e in entries
             if e["name"] == remote),
            0,
        )
        Path(local).write_bytes(b"x" * size)


def _tree():
    """digitalisate/{a.jpg, notes.txt, letter-01/{001.jpg, 002.jpg}}"""
    return {
        "digitalisate": [
            {"name": "digitalisate/a.jpg", "type": "file", "content_length": 10},
            {"name": "digitalisate/notes.txt", "type": "file", "content_length": 5},
            {"name": "digitalisate/letter-01", "type": "directory"},
        ],
        "digitalisate/letter-01": [
            {"name": "digitalisate/letter-01/001.jpg", "type": "file", "content_length": 20},
            {"name": "digitalisate/letter-01/002.jpg", "type": "file", "content_length": 30},
        ],
    }


@pytest.fixture
def fake_dav(monkeypatch):
    """Patch the client factory; hand the test the fake it produced."""
    made: list[FakeClient] = []
    state: dict = {"tree": _tree(), "fail": set(), "serve": None}

    def _factory(share, endpoint=None):
        client = FakeClient(state["tree"], fail=state["fail"], serve=state["serve"],
                            url=(endpoint or share.webdav_url))
        made.append(client)
        return client

    monkeypatch.setattr(nextcloud, "_client", _factory)
    return {"made": made, "state": state}


SHARE = nextcloud.ShareRef(base_url="https://cloud.example.org/nc", token="TOK", password="pw")


# ── listing ──────────────────────────────────────────────────────────────────

def test_list_files_is_recursive_sorted_and_filtered(fake_dav):
    files = nextcloud.list_files("digitalisate", share=SHARE)
    assert [p for p, _ in files] == [
        "digitalisate/a.jpg",
        "digitalisate/letter-01/001.jpg",
        "digitalisate/letter-01/002.jpg",
    ], "notes.txt is not ingestable; order must be stable"
    assert dict(files)["digitalisate/letter-01/002.jpg"] == 30


def test_falls_back_to_the_newer_dav_endpoint(fake_dav):
    """Nextcloud 30 moved public shares to /public.php/dav/files/<token> while
    older servers serve only /public.php/webdav. Which one exists is the server's
    answer to give, not something configuration should have to know."""
    fake_dav["state"]["serve"] = {SHARE.dav_url}
    files = nextcloud.list_files("digitalisate", share=SHARE)
    assert len(files) == 3
    assert [c.url for c in fake_dav["made"]] == [SHARE.webdav_url, SHARE.dav_url]


def test_neither_endpoint_reports_both_failures(fake_dav):
    fake_dav["state"]["serve"] = set()
    with pytest.raises(nextcloud.NextcloudError) as err:
        nextcloud.list_files("digitalisate", share=SHARE)
    assert "public.php/webdav" in str(err.value)
    assert "public.php/dav/files" in str(err.value)


# ── mirroring ────────────────────────────────────────────────────────────────

def test_pull_preserves_the_folder_tree(fake_dav, tmp_path):
    out = nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE)
    assert sorted(p.relative_to(tmp_path).as_posix() for p in out) == [
        "a.jpg", "letter-01/001.jpg", "letter-01/002.jpg",
    ]
    assert (tmp_path / "letter-01" / "002.jpg").stat().st_size == 30


def test_rerunning_skips_what_is_already_there(fake_dav, tmp_path):
    """The point of resuming: a share pulled twice transfers nothing the second
    time, and still reports the whole corpus rather than an empty delta."""
    nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE)
    fake_dav["made"].clear()
    out = nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE)
    assert len(out) == 3
    assert all(c.downloaded == [] for c in fake_dav["made"]), "re-downloaded an existing file"


def test_a_truncated_file_is_fetched_again(fake_dav, tmp_path):
    """A half-written file is exactly what an interrupted transfer leaves behind.
    Trusting its existence would feed a truncated image to a recogniser, which
    produces a transcription rather than an error."""
    (tmp_path / "letter-01").mkdir(parents=True)
    (tmp_path / "letter-01" / "001.jpg").write_bytes(b"xx")      # remote size is 20
    nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE)
    assert (tmp_path / "letter-01" / "001.jpg").stat().st_size == 20


def test_one_failed_download_does_not_lose_the_rest(fake_dav, tmp_path):
    fake_dav["state"]["fail"] = {"digitalisate/letter-01/001.jpg"}
    out = nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE)
    assert sorted(p.name for p in out) == ["002.jpg", "a.jpg"]
    assert not (tmp_path / "letter-01" / "001.jpg").exists()
    assert not list(tmp_path.rglob("*.part")), "a failed transfer left a temp file behind"


def test_limit_takes_the_first_pages_in_corpus_order(fake_dav, tmp_path):
    out = nextcloud.pull_folder("digitalisate", tmp_path, share=SHARE, limit=2)
    assert [p.name for p in out] == ["a.jpg", "001.jpg"]


def test_is_configured_is_false_without_a_share_url(monkeypatch):
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_URL", "")
    assert nextcloud.is_configured() is False
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_URL", "https://c.example.org/nc/s/TOK")
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_PASS", "")
    assert nextcloud.is_configured() is True


def test_share_from_config_reads_url_and_password(monkeypatch):
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_URL",
                        "https://cloud.example.org/nc/s/TOK/authenticate/showshare")
    monkeypatch.setattr(config, "NEXTCLOUD_SHARE_PASS", "pw")
    ref = nextcloud.share_from_config()
    assert ref.token == "TOK" and ref.password == "pw"
    assert ref.base_url == "https://cloud.example.org/nc"


def test_auth_is_the_token_and_the_password(monkeypatch):
    """Public-share WebDAV has no user: the token IS the username. Getting this
    wrong is a 401 that looks like a wrong password."""
    seen = {}

    class _Client:
        def __init__(self, url, auth=None, **opts):
            seen["url"], seen["auth"], seen["opts"] = url, auth, opts

    module = type(sys)("webdav4.client")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "webdav4.client", module)
    nextcloud._client(SHARE)
    assert seen["auth"] == ("TOK", "pw")
    assert seen["url"].startswith("https://cloud.example.org/nc/public.php/webdav")


# ── the walk, after it died on a slow folder ─────────────────────────────────
#
# 2026-09-15: `pull-share --limit 10` ran for nine minutes against the Laßberg
# share and then raised `httpx.ReadTimeout` from a single PROPFIND. httpx
# defaults to five seconds and webdav4 inherits it; a listing of a few hundred
# scans on a server that stats each entry does not finish in five. Nine minutes
# of completed listings went with it, and the only log line in all that time was
# the one announcing which endpoint had been chosen.

class _Listing:
    """A client whose `ls` fails a given number of times before answering."""

    def __init__(self, failures=0, entries=None):
        self.failures = failures
        self.entries = entries if entries is not None else []
        self.calls = 0

    def ls(self, rdir, detail=True):
        import httpx

        self.calls += 1
        if self.calls <= self.failures:
            raise httpx.ReadTimeout("The read operation timed out")
        return self.entries


def test_the_client_is_given_a_timeout_at_all(monkeypatch):
    """The whole failure in one assertion: without this, httpx's five seconds
    apply and a large folder cannot be listed."""
    captured = {}

    class _Client:
        def __init__(self, url, auth=None, **opts):
            captured.update(opts)

    module = type(sys)("webdav4.client")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "webdav4.client", module)
    nextcloud._client(nextcloud.ShareRef("https://x/nc", "tok", "pw"))
    assert captured.get("timeout") == config.NEXTCLOUD_TIMEOUT
    assert captured["timeout"] > 5, "five is httpx's default and the reason this exists"


def test_a_slow_listing_is_retried_not_abandoned(monkeypatch):
    monkeypatch.setattr(config, "NEXTCLOUD_LS_ATTEMPTS", 3)
    client = _Listing(failures=2, entries=[{"name": "a/p.jpg", "type": "file",
                                            "content_length": 7}])
    assert nextcloud._ls(client, "a") == client.entries
    assert client.calls == 3


def test_a_folder_that_keeps_failing_stops_the_walk(monkeypatch):
    """Not skipped. A directory quietly missing from the walk is a corpus quietly
    missing pages — the one failure nobody would notice."""
    monkeypatch.setattr(config, "NEXTCLOUD_LS_ATTEMPTS", 2)
    client = _Listing(failures=99)
    with pytest.raises(nextcloud.NextcloudError) as err:
        nextcloud._ls(client, "letters/03")
    assert "letters/03" in str(err.value)
    assert "NEXTCLOUD_TIMEOUT" in str(err.value), "the message has to name the lever"
    assert client.calls == 2


def test_the_error_survives_as_a_nextcloud_error_not_an_httpx_one(monkeypatch):
    """So `pull_folder`'s caller sees a configured failure rather than a
    transport detail leaking out of a utility module."""
    monkeypatch.setattr(config, "NEXTCLOUD_LS_ATTEMPTS", 1)
    with pytest.raises(nextcloud.NextcloudError):
        nextcloud._ls(_Listing(failures=1), "x")


# ── the enumeration is the expensive part ────────────────────────────────────
#
# Measured against the Laßberg share, 2026-09-15: one PROPFIND per folder,
# 1.75 s each. `pull-share --limit 10` walked 100 folders and ran three minutes
# before fetching a single byte, because the limit applied to a list that had to
# be complete first.

class _Tree:
    """A share with counted listings, so a test can see what was not walked."""

    def __init__(self, tree):
        self.tree = tree
        self.listed: list[str] = []

    def ls(self, rdir, detail=True):
        self.listed.append(rdir)
        # Returned deliberately unsorted: the server's order is not the walk's.
        return list(reversed(self.tree.get(rdir, [])))


def _d(name):
    return {"name": name, "type": "directory"}


def _f(name, size=10):
    return {"name": name, "type": "file", "content_length": size}


TREE = {
    "d": [_d("d/a"), _d("d/b"), _d("d/c")],
    "d/a": [_f("d/a/1.jpg"), _f("d/a/2.jpg")],
    "d/b": [_f("d/b/1.jpg"), _f("d/b/2.jpg")],
    "d/c": [_f("d/c/1.jpg")],
}


def test_a_limited_walk_stops_before_the_last_folders():
    """The point: three minutes of PROPFINDs for ten files, and none of it needed."""
    client = _Tree(TREE)
    found = list(islice(nextcloud._walk(client, "d", True), 3))
    assert [p for p, _ in found] == ["d/a/1.jpg", "d/a/2.jpg", "d/b/1.jpg"]
    assert "d/c" not in client.listed, "the folder past the limit was never listed"


def test_an_unlimited_walk_still_sees_everything():
    client = _Tree(TREE)
    assert len(list(nextcloud._walk(client, "d", True))) == 5
    assert "d/c" in client.listed


def test_the_walk_sorts_each_level_whatever_the_server_returned():
    """`_Tree` hands entries back reversed. Without the sort the traversal order
    is the server's, and "the first ten" is a different ten on a re-run — which a
    runner that resumes from what is on disk cannot survive."""
    client = _Tree(TREE)
    paths = [p for p, _ in nextcloud._walk(client, "d", True)]
    assert paths == sorted(paths)


def test_the_same_first_files_every_time():
    a = [p for p, _ in islice(nextcloud._walk(_Tree(TREE), "d", True), 3)]
    b = [p for p, _ in islice(nextcloud._walk(_Tree(TREE), "d", True), 3)]
    assert a == b
