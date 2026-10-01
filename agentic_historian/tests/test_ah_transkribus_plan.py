"""Planning a Transkribus upload from the page cache.

One letter is one document — the unit the archive uses and the unit a historian
reads. The cache mirrors the share, `<Bestand>/<letter>/<page>.jpg`, so the letter
is a directory that holds images and the Bestand is one that holds directories.

Offline: no request leaves these tests.
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                                   # noqa: E402
import transkribus as tk                        # noqa: E402


@pytest.fixture
def cache(tmp_path):
    """A page cache shaped like the real one, including the awkward names."""
    pages = {
        "Kantonsbibliothek Appenzell/lassberg-letter-0905": [
            "40C_Ms-321-04-01_0514.jpg", "40C_Ms-321-04-01_0515.jpg",
            "40C_Ms-321-04-01_0516.jpg", "40C_Ms-321-04-01_0517.jpg",
        ],
        # Two digits against ten: ASCII order would reverse this letter.
        "Basel/lassberg-letter-1209": [
            "PA 82a B 9_Seite_2.jpg", "PA 82a B 9_Seite_10.jpg",
            "PA 82a B 9_Seite_9.jpg",
        ],
        # The same letter number in a second Bestand, and a folder name with
        # spaces and parentheses — both real.
        "Marbach/lassberg-letter-1209": ["HS00926795_0007.jpg"],
        "Basel/lassberg-letter- - Kopie (41)": ["PA 82a B 9_Seite_127.jpg"],
    }
    for folder, names in pages.items():
        d = tmp_path / folder
        d.mkdir(parents=True)
        for name in names:
            (d / name).write_bytes(b"\xff\xd8\xff" + b"x" * 100)
    # Things the walk must not mistake for pages.
    (tmp_path / "Basel" / "lassberg-letter-1209"
     / "PA 82a B 9_Seite_2.jpg.src.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".listing-abc.json").write_text("{}", encoding="utf-8")
    return tmp_path


# ── the inventory ────────────────────────────────────────────────────────────

def test_a_letter_is_a_directory_that_holds_images(cache):
    titles = [ltr.title for ltr in tk.letters_from_cache(cache)]
    assert titles == [
        "Basel__lassberg-letter- - Kopie (41)",
        "Basel__lassberg-letter-1209",
        "Kantonsbibliothek Appenzell__lassberg-letter-0905",
        "Marbach__lassberg-letter-1209",
    ]


def test_the_title_carries_the_bestand_so_two_1209s_do_not_collide(cache):
    """Basel and Marbach both hold a lassberg-letter-1209. A title of just the
    letter folder would make one document overwrite the other's identity."""
    titles = [ltr.title for ltr in tk.letters_from_cache(cache)]
    assert "Basel__lassberg-letter-1209" in titles
    assert "Marbach__lassberg-letter-1209" in titles
    assert len(set(titles)) == len(titles)


def test_pages_are_ordered_by_number_not_by_ascii(cache):
    """`Seite_10` sorts before `Seite_2` in ASCII, which silently reverses a
    letter — the one error nobody would notice in a collection of 1700."""
    letter = next(ltr for ltr in tk.letters_from_cache(cache)
                  if ltr.title == "Basel__lassberg-letter-1209")
    assert [p.name for p in letter.pages] == [
        "PA 82a B 9_Seite_2.jpg",
        "PA 82a B 9_Seite_9.jpg",
        "PA 82a B 9_Seite_10.jpg",
    ]


def test_sidecars_and_dotfiles_are_not_pages(cache):
    """The cache keeps a `.src.json` beside every page and a `.listing-*.json`
    at its root. Neither is an image."""
    everything = [p.name for ltr in tk.letters_from_cache(cache) for p in ltr.pages]
    assert not any(n.endswith(".src.json") for n in everything)
    assert not any(n.startswith(".") for n in everything)


def test_a_missing_cache_is_an_error_that_names_the_path(tmp_path):
    with pytest.raises(tk.TranskribusError) as err:
        tk.letters_from_cache(tmp_path / "nope")
    assert "nope" in str(err.value)


# ── resuming from what the collection already holds ──────────────────────────

class _Fake:
    """Just enough of the API to plan against."""

    def __init__(self, documents=None, collections=None, status=200):
        self.documents = documents if documents is not None else []
        self.collections = collections or [{"colId": 2536466, "colName": "Lassberg"}]
        self.status = status

    def get(self, url, params=None, timeout=None):
        if url.endswith("/collections/list"):
            return _Resp(self.status, self.collections)
        if "/collections/" in url and url.endswith("/list"):
            return _Resp(self.status, self.documents)
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, data=None, timeout=None):
        if url.endswith("/auth/login"):
            return _Resp(200, None,
                         text="<trpUserLogin><sessionId>SID123</sessionId>"
                              "</trpUserLogin>")
        raise AssertionError(f"unexpected POST {url}")


class _Resp:
    def __init__(self, status_code, payload, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else "[]"

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_a_letter_already_in_the_collection_is_skipped(cache):
    api = _Fake(documents=[{"docId": 1, "title": "Basel__lassberg-letter-1209"}])
    plan = tk.plan_upload(cache, "2536466", "SID", session=api)
    assert [ltr.title for ltr in plan.skipped] == ["Basel__lassberg-letter-1209"]
    assert "Basel__lassberg-letter-1209" not in [ltr.title for ltr in plan.letters]
    assert len(plan.letters) == 3


def test_an_empty_collection_is_not_an_error(cache):
    """A collection with nothing in it answers 200 with an empty body rather than
    a list, and that must not read as a failure."""
    api = _Fake(documents=[])
    api.get = lambda url, params=None, timeout=None: _Resp(200, None, text="")
    assert tk.collection_documents("2536466", "SID", session=api) == []


def test_the_plan_counts_pages_and_bytes(cache):
    plan = tk.plan_upload(cache, "2536466", "SID", session=_Fake())
    assert len(plan.letters) == 4
    assert plan.pages == 9
    assert plan.bytes == 9 * 103


def test_a_limit_plans_only_the_first_letters(cache):
    plan = tk.plan_upload(cache, "2536466", "SID", session=_Fake(), limit=2)
    assert len(plan.letters) == 2


def test_the_plan_reads_as_something_a_human_checks(cache):
    plan = tk.plan_upload(cache, "2536466", "SID", session=_Fake())
    text = tk.format_plan(plan)
    assert "4 letter(s), 9 page(s)" in text
    assert "Kantonsbibliothek Appenzell__lassberg-letter-0905  (4 pages)" in text


# ── login ────────────────────────────────────────────────────────────────────

def test_login_returns_the_session_id(monkeypatch):
    monkeypatch.setenv("TRANSKRIBUS_USER", "tobias.hodel@unibe.ch")
    monkeypatch.setenv("TRANSKRIBUS_PASS", "s3cret")
    assert tk.login(session=_Fake()) == "SID123"


def test_missing_credentials_name_the_variables(monkeypatch):
    monkeypatch.setattr(config, "_get", lambda key, default="": default)
    with pytest.raises(tk.TranskribusError) as err:
        tk.login(session=_Fake())
    assert "TRANSKRIBUS_USER" in str(err.value)
    assert "TRANSKRIBUS_PASS" in str(err.value)


def test_a_failed_login_reports_the_status_not_a_traceback(monkeypatch):
    monkeypatch.setenv("TRANSKRIBUS_USER", "u")
    monkeypatch.setenv("TRANSKRIBUS_PASS", "p")

    api = _Fake()
    api.post = lambda url, data=None, timeout=None: _Resp(
        401, None, text="HTTP Status 401 – Unauthorized")
    with pytest.raises(tk.TranskribusError) as err:
        tk.login(session=api)
    assert "401" in str(err.value)


# ── the seam ─────────────────────────────────────────────────────────────────

def test_uploading_refuses_rather_than_guessing_the_endpoint():
    """The API reference covers every part of Transkribus except putting an image
    into it. A remembered endpoint shape would be found out 1700 documents into
    somebody else's collection."""
    with pytest.raises(NotImplementedError) as err:
        tk.create_upload("2536466", tk.Letter(title="t", folder=Path(".")), "SID")
    assert "not confirmed" in str(err.value)
