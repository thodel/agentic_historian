"""
transkribus.py — put the Laßberg letters into a Transkribus collection.

The scans live in a Nextcloud share and their JPEG working copies in the page
cache on tei, one directory per letter under one directory per Bestand — the shape
the share has. A Transkribus *document* is one letter, which is the unit the
archive itself uses and the unit a historian reads.

**What this module does not know yet.** Transkribus' upload endpoint. The API
reference this was written against
(`history-unibas/economies-of-space-transkribus`) covers login, collections,
documents, PageXML, layout analysis and recognition — and not uploading an image
or creating a document. Everything up to that one call is here and testable;
:func:`create_upload` and :func:`put_page` are the seam, and they say so.

Everything else follows the rules the rest of this project already runs on:

* **Resumable by what is already there.** A letter whose document title is in the
  collection is skipped. No state file, nothing to reconcile — the same property
  that lets a 6742-page batch be interrupted and restarted.
* **The title is the page key's own scheme.** ``<Bestand>__<letter folder>``, the
  share's path with separators folded. A Transkribus document can then be matched
  back to our readings without a lookup table, and two Bestände that both hold a
  ``lassberg-letter-1173`` do not collide.
* **A dry run that answers before anything moves.** 4.7 GB is too much to send on
  the assumption that the inventory is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests
from loguru import logger

import config

#: The legacy REST server. Confirmed live on 2026-10-01: an unauthenticated
#: `collections/list` answers with a Tomcat 401, not a 404, so the host and path
#: are right even though the collection in question lives behind
#: app.transkribus.org.
API = "https://transkribus.eu/TrpServer/rest"

_TIMEOUT = 60
#: Suffixes the page cache writes. Everything it converts becomes `.jpg`; a page
#: that needed no conversion keeps its own.
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}


class TranskribusError(RuntimeError):
    """Anything that stops an upload, in the words the CLI should print."""


@dataclass
class Letter:
    """One letter: a document to be, with its pages in reading order."""

    title: str
    folder: Path
    pages: list[Path] = field(default_factory=list)

    @property
    def bytes(self) -> int:
        return sum(p.stat().st_size for p in self.pages if p.exists())


@dataclass
class Plan:
    """What an upload would do, before it does any of it."""

    collection: str = ""
    letters: list[Letter] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: list[Letter] = field(default_factory=list)

    @property
    def pages(self) -> int:
        return sum(len(ltr.pages) for ltr in self.letters)

    @property
    def bytes(self) -> int:
        return sum(ltr.bytes for ltr in self.letters)


def _page_sort_key(path: Path) -> tuple:
    """Order pages the way the archive numbered them, not the way ASCII does.

    ``…_0002`` must come before ``…_0010``, and ``Seite_9`` before ``Seite_10``.
    Splitting into digit and non-digit runs and comparing numbers as numbers is
    the whole trick; a plain sort puts page 10 before page 2 and silently
    reverses a letter.
    """
    parts = re.split(r"(\d+)", path.name)
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts if p != "")


def letters_from_cache(cache_dir: Path, *,
                       suffixes: Iterable[str] = IMAGE_SUFFIXES) -> list[Letter]:
    """Every letter directory under the page cache, with its pages in order.

    The cache mirrors the share: ``<cache>/<Bestand>/<letter>/<page>.jpg``. A
    directory that holds images *is* a letter; one that holds only directories is
    a Bestand. Deriving it from the tree rather than from a naming convention
    means a Bestand that numbers its folders differently still works.
    """
    cache_dir = Path(cache_dir)
    if not cache_dir.is_dir():
        raise TranskribusError(f"no page cache at {cache_dir}")
    wanted = {s.lower() for s in suffixes}

    by_folder: dict[Path, list[Path]] = {}
    for path in cache_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in wanted:
            continue
        if path.name.startswith("."):
            continue                      # sidecars and partial downloads
        by_folder.setdefault(path.parent, []).append(path)

    letters: list[Letter] = []
    for folder in sorted(by_folder):
        rel = folder.relative_to(cache_dir)
        if not rel.parts:
            logger.warning(f"[transkribus] images directly in {cache_dir} — "
                           f"no letter folder, skipped")
            continue
        letters.append(Letter(
            title="__".join(rel.parts),
            folder=folder,
            pages=sorted(by_folder[folder], key=_page_sort_key),
        ))
    return letters


# ── the API, as far as it is documented ──────────────────────────────────────

def login(user: Optional[str] = None, password: Optional[str] = None,
          session: Optional[requests.Session] = None) -> str:
    """A session id, or a ``TranskribusError`` that names what was missing.

    Credentials come from the environment, never from an argument on a command
    line: a password in argv is a password in ``ps`` and in the shell history.
    """
    import xml.etree.ElementTree as et

    user = user or config._get("TRANSKRIBUS_USER", "")
    password = password or config._get("TRANSKRIBUS_PASS", "")
    if not user or not password:
        raise TranskribusError(
            "TRANSKRIBUS_USER and TRANSKRIBUS_PASS must be set in the "
            "environment (see config.unfilled_secrets() for placeholders)")

    s = session or requests.Session()
    r = s.post(f"{API}/auth/login", data={"user": user, "pw": password},
               timeout=_TIMEOUT)
    if r.status_code != 200:
        raise TranskribusError(
            f"login failed for {user}: {r.status_code} — "
            f"{r.text[:200].strip()}")
    try:
        sid = et.fromstring(r.text).find("sessionId").text
    except Exception as exc:              # noqa: BLE001 — report, do not traceback
        raise TranskribusError(
            f"login answered 200 with no sessionId: {type(exc).__name__}") from exc
    if not sid:
        raise TranskribusError("login answered 200 with an empty sessionId")
    return sid


def list_collections(sid: str, session: Optional[requests.Session] = None) -> list[dict]:
    """Every collection the account can see."""
    s = session or requests.Session()
    r = s.get(f"{API}/collections/list", params={"JSESSIONID": sid},
              timeout=_TIMEOUT)
    if r.status_code != 200:
        raise TranskribusError(
            f"cannot list collections: {r.status_code} — {r.text[:200].strip()}")
    return r.json()


def collection_documents(colid: str, sid: str,
                         session: Optional[requests.Session] = None) -> list[dict]:
    """Every document in one collection.

    This is the resume mechanism: a letter whose title is already here is not
    uploaded again. An empty collection answers 200 with an empty body rather
    than a list, which is not an error and must not read as one.
    """
    s = session or requests.Session()
    r = s.get(f"{API}/collections/{colid}/list", params={"JSESSIONID": sid},
              timeout=_TIMEOUT)
    if r.status_code != 200:
        raise TranskribusError(
            f"cannot list documents of collection {colid}: {r.status_code} — "
            f"{r.text[:200].strip()}")
    if not r.text.strip():
        return []
    try:
        return r.json() or []
    except ValueError:
        return []


def plan_upload(cache_dir: Path, colid: str, sid: str,
                session: Optional[requests.Session] = None,
                limit: Optional[int] = None) -> Plan:
    """What an upload would do: which letters are new, which are already there.

    Reads the collection first and the cache second, so a letter already uploaded
    costs nothing but a string comparison.
    """
    documents = collection_documents(colid, sid, session=session)
    existing = {str(d.get("title") or "") for d in documents}
    plan = Plan(collection=str(colid), existing=sorted(e for e in existing if e))

    for letter in letters_from_cache(cache_dir):
        if letter.title in existing:
            plan.skipped.append(letter)
        else:
            plan.letters.append(letter)
    if limit is not None:
        plan.letters = plan.letters[:max(0, limit)]
    return plan


def format_plan(plan: Plan, *, show: int = 10) -> str:
    """The plan as something a human checks before 4.7 GB moves."""
    gb = plan.bytes / 1e9
    lines = [
        f"collection {plan.collection}: {len(plan.existing)} document(s) already there",
        f"to upload: {len(plan.letters)} letter(s), {plan.pages} page(s), {gb:.2f} GB",
        f"skipped (already uploaded): {len(plan.skipped)} letter(s)",
        "",
    ]
    for letter in plan.letters[:show]:
        lines.append(f"  {letter.title}  ({len(letter.pages)} pages)")
        for page in letter.pages[:3]:
            lines.append(f"      {page.name}")
        if len(letter.pages) > 3:
            lines.append(f"      … {len(letter.pages) - 3} more")
    if len(plan.letters) > show:
        lines.append(f"  … {len(plan.letters) - show} more letter(s)")
    return "\n".join(lines)


# ── the seam: the one call this module cannot yet make ───────────────────────

def create_upload(colid: str, letter: Letter, sid: str,
                  session: Optional[requests.Session] = None) -> str:
    """Begin an upload for one letter and return its upload id.

    **Not implemented, and deliberately not guessed.** The API reference this
    module was built from covers login, collections, documents, PageXML, layout
    analysis and recognition — every part of Transkribus except putting an image
    into it. Writing this against a remembered endpoint shape would be writing
    against a memory, and the first time anyone would find out is 1700 documents
    into a collection somebody else has to clean up.

    What has to be settled first: whether the legacy ``TrpServer`` API serves the
    collection at all. It is alive — an unauthenticated request answers 401, not
    404 — but the collection in question lives behind ``app.transkribus.org``,
    which authenticates through ``account.readcoop.eu`` and may not be reachable
    from this API at all. :func:`list_collections` answers that in one call.
    """
    raise NotImplementedError(
        "the Transkribus upload endpoint is not confirmed yet — run "
        "`upload-transkribus --dry-run` first: it logs in and reports whether "
        "this API can see the collection")


def put_page(upload_id: str, page: Path, sid: str,
             session: Optional[requests.Session] = None) -> None:
    """Send one page image into an upload begun by :func:`create_upload`."""
    raise NotImplementedError(
        "the Transkribus upload endpoint is not confirmed yet — see create_upload")
