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

#: Directories a page cache never contains, and a source tree always does. The
#: first dry run walked `Path("")` — the working directory, because
#: ATR_PAGE_CACHE was unset — and offered to upload scikit-learn's astronaut.png
#: and networkx's test baselines as letters of the Laßberg correspondence. The
#: unset path is an error now (see `letters_from_cache`); this list is the second
#: line, for the day somebody points `--cache-dir` at a checkout by mistake.
SKIP_DIRS = {".venv", "venv", "site-packages", ".git", "__pycache__",
             "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


class TranskribusError(RuntimeError):
    """Anything that stops an upload, in the words the CLI should print."""


@dataclass
class Letter:
    """One letter: a document to be, with its pages in reading order.

    ``md`` is what the document is created with — title, author, description —
    and comes from the edition's register when the letter is grouped by it.
    """

    title: str
    folder: Path
    pages: list[Path] = field(default_factory=list)
    md: dict = field(default_factory=dict)

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
    # An empty path is not a default, it is a missing answer. `Path("")` is
    # `Path(".")`, so an unset ATR_PAGE_CACHE used to mean "walk the working
    # directory": the first dry run inventoried the repository, .venv included.
    if str(cache_dir) in {"", "."}:
        raise TranskribusError(
            "no page cache given — pass --cache-dir or set ATR_PAGE_CACHE. "
            "An empty value would walk the working directory, which on tei is "
            "the checkout and includes .venv")
    if not cache_dir.is_dir():
        raise TranskribusError(f"no page cache at {cache_dir}")
    wanted = {s.lower() for s in suffixes}

    by_folder: dict[Path, list[Path]] = {}
    for path in cache_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in wanted:
            continue
        if path.name.startswith("."):
            continue                      # sidecars and partial downloads
        rel = path.relative_to(cache_dir)
        if SKIP_DIRS.intersection(rel.parts):
            continue
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


# ── one document per letter, and only where the letter is known ──────────────

@dataclass
class Unassigned:
    """Pages that would not become a document, counted by why."""

    no_letter_segment: int = 0      # no lassberg-letter-NNNN in the path
    not_in_register: int = 0        # a letter id the edition has no file for
    letters_not_in_register: set = field(default_factory=set)
    #: The same file, byte for byte, under two folders of one letter: uploaded
    #: once. 25.10.: lassberg-letter-1209 came in twice from the cold tier and
    #: Transkribus refused the second copy ("Submitted files already exist").
    duplicate_copies: int = 0
    #: Letters with two *different* images under one file name: which one is
    #: the page is not settled, so the letter is named and left out.
    conflicting: dict = field(default_factory=dict)


def _same_file(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    import hashlib

    return hashlib.sha1(a.read_bytes()).digest() == hashlib.sha1(b.read_bytes()).digest()


def _one_copy_per_name(pages: list[Path]) -> tuple[list[Path], int, list[str]]:
    """``(pages, copies dropped, names that clash)`` for one letter.

    An upload names its pages by file name, and Transkribus refuses a name
    twice. Two folders holding the same scan give the same name twice; that is
    one page. Two folders holding *different* images under one name are two
    candidates for one page, and choosing would be a guess.
    """
    kept: dict[str, Path] = {}
    dropped, clashes = 0, []
    for page in sorted(pages, key=lambda p: str(p)):
        first = kept.get(page.name)
        if first is None:
            kept[page.name] = page
        elif _same_file(first, page):
            dropped += 1
        elif page.name not in clashes:
            clashes.append(page.name)
    return list(kept.values()), dropped, clashes


def letters_by_register(source_dir: Path, register, *,
                        suffixes: Iterable[str] = IMAGE_SUFFIXES,
                        ) -> tuple[list[Letter], Unassigned]:
    """One letter per ``lassberg-letter-NNNN``, for the letters the register has.

    The first plan made one document per *folder*, named after the folder —
    ``agentic_historian-page_cache-27f2…__Aarau__upload__lassberg-letter-0892``
    — which is the cache's structure, not the edition's. The edition's unit is
    the letter, named by its id, and a page belongs to one when its path carries
    the id (`hf_export.letter_of`, the same rule the dataset labels use) **and**
    the edition has a file for it under ``data/letters``. Both conditions, so
    that only digitisations with a settled letter assignment are uploaded
    (asked for on 2026-10-23); everything else is counted, by reason, and
    left where it is.

    A page's key is its path relative to ``source_dir`` with separators folded
    to ``__`` — the project's page key — so this works on the share mount, the
    page cache and its cold tier alike.
    """
    from hf_export import letter_of

    source_dir = Path(source_dir)
    if str(source_dir) in {"", "."} or not source_dir.is_dir():
        raise TranskribusError(f"no source directory at {source_dir!r}")
    wanted = {s.lower() for s in suffixes}
    by_letter: dict[str, list[Path]] = {}
    un = Unassigned()
    for path in source_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in wanted or path.name.startswith("."):
            continue
        rel = path.relative_to(source_dir)
        if SKIP_DIRS.intersection(rel.parts):
            continue
        key = "__".join(rel.with_suffix("").parts)
        group = letter_of(key)
        if group.basis != "letter":
            un.no_letter_segment += 1
            continue
        if group.name not in register:
            un.not_in_register += 1
            un.letters_not_in_register.add(group.name)
            continue
        by_letter.setdefault(group.name, []).append(path)

    letters: list[Letter] = []
    for letter_id in sorted(by_letter):
        rec = register.get(letter_id)
        pages, dropped, clashes = _one_copy_per_name(by_letter[letter_id])
        un.duplicate_copies += dropped
        if clashes:
            un.conflicting[letter_id] = clashes
            continue
        pages = sorted(pages, key=_page_sort_key)
        desc = "; ".join(x for x in (
            rec.title, f"{rec.repository}, {rec.signature}".strip(", ") if (rec.repository or rec.signature) else "",
            f"register {register.source.name}@{register.revision[:12]}" if register.revision else "",
            f"{len(pages)} page(s) from {pages[0].parent.relative_to(source_dir)}") if x)
        letters.append(Letter(
            title=letter_id, folder=pages[0].parent, pages=pages,
            md={"title": letter_id, "author": rec.sender or "",
                "writer": rec.sender or "", "genre": "Brief",
                "description": desc, "language": "German"}))
    return letters, un


def plan_letters(source_dir: Path, colid: str, register, sid: str,
                 session: Optional[requests.Session] = None,
                 limit: Optional[int] = None,
                 only: Optional[str] = None) -> tuple[Plan, Unassigned]:
    """The letter-wise plan: new letters, letters already there, and what was
    left out and why. ``only`` restricts the plan to one letter id — the first
    real upload is one document, checked in the interface before the rest."""
    documents = collection_documents(colid, sid, session=session)
    existing = {str(d.get("title") or "") for d in documents}
    plan = Plan(collection=str(colid), existing=sorted(e for e in existing if e))
    letters, un = letters_by_register(source_dir, register)
    for letter in letters:
        if only and letter.title != only:
            continue
        (plan.skipped if letter.title in existing else plan.letters).append(letter)
    if only and not plan.letters and not plan.skipped:
        raise TranskribusError(
            f"{only!r} is not among the letters with a settled assignment under "
            f"{source_dir}")
    if limit is not None:
        plan.letters = plan.letters[:max(0, limit)]
    return plan, un


def format_unassigned(un: Unassigned) -> str:
    lines = [f"left out: {un.no_letter_segment} page(s) whose path names no letter, "
             f"{un.not_in_register} page(s) of {len(un.letters_not_in_register)} letter(s) "
             f"the register has no file for"]
    if un.letters_not_in_register:
        some = sorted(un.letters_not_in_register)[:8]
        lines.append("  e.g. " + ", ".join(some) + (" …" if len(un.letters_not_in_register) > 8 else ""))
    if un.duplicate_copies:
        lines.append(f"identical copies under a second folder, uploaded once: {un.duplicate_copies} page(s)")
    if un.conflicting:
        lines.append(f"left out, two different images under one name: {len(un.conflicting)} letter(s)")
        for lid, names in sorted(un.conflicting.items())[:8]:
            lines.append(f"  {lid}: {', '.join(names[:3])}" + (" …" if len(names) > 3 else ""))
    return "\n".join(lines)


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


#: A K-shelfmark in a document title: the series, then numbers separated by
#: comma, dash, space or underscore. 36479 titles BLB objects
#: ``done_BLB_K 3123 8 4``; the BLB writes ``K 3123,8,4`` (#714).
SHELF_RE = re.compile(r"K[\s_]?(\d{4})(?:[,\s]\s*|_)(\d+(?:\s*[-,\s]\s*\d+)*)")


def shelf_key(shelf: str) -> str:
    """One key for every spelling: ``K 3123,8,4``, ``K 3123 8 4`` → ``K3123-8-4``."""
    return "K" + "-".join(re.findall(r"\d+", shelf or ""))


def shelfmark_of(title: str) -> Optional[str]:
    """The normalised shelfmark a document title carries, or None."""
    m = SHELF_RE.search(title or "")
    return shelf_key(m.group(0)) if m else None


def collections_overview(*, name: Optional[str] = None, collection: Optional[str] = None,
                         limit: int = 200, offset: int = 0,
                         session: Optional[requests.Session] = None) -> dict:
    """What the account sees, read-only (#714): collections filtered by
    ``name``, or one ``collection``'s documents with the shelfmark read from
    each title. Never page content."""
    sid = login(session=session)
    cols = list_collections(sid, session=session)
    if collection is None:
        pat = re.compile(re.escape(name), re.I) if name else None
        chosen = [c for c in cols if not pat or pat.search(str(c.get("colName", "")))]
        rows = [{"colId": c.get("colId"), "colName": c.get("colName"),
                 "documents": c.get("nrOfDocuments")} for c in chosen]
        return {"ok": True, "visible": len(cols), "matching": len(rows),
                "offset": offset, "collections": rows[offset:offset + limit]}
    known = {str(c.get("colId")): c for c in cols}
    if str(collection) not in known:
        raise TranskribusError(f"collection {collection} is not visible to this account "
                               f"({len(cols)} are)")
    docs = collection_documents(str(collection), sid, session=session)
    rows = [{"docId": d.get("docId"), "title": d.get("title"), "pages": d.get("nrOfPages"),
             "status": d.get("status"), "uploaded": d.get("uploadTimestamp"),
             "shelfmark": shelfmark_of(str(d.get("title") or ""))} for d in docs]
    with_shelf = sum(1 for r in rows if r["shelfmark"])
    return {"ok": True, "collection": str(collection),
            "colName": known[str(collection)].get("colName"),
            "documents": len(rows), "with_shelfmark": with_shelf,
            "pages": sum(int(r["pages"] or 0) for r in rows),
            "offset": offset, "rows": rows[offset:offset + limit]}


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


# ── harvesting ground truth out of a collection ──────────────────────────────
#
# A page corrected by hand in Transkribus is the only thing that can say which of
# our readings is *right* rather than merely different (#326, #416). Collection
# 36479 holds Laßberg pages at `status="FINAL"` — two of them arrived by hand on
# 2026-10-01, from docIds 1682295 and 1682311, both letters of Laßberg's own.
# Fetching all of them is the difference between one indicative page and a
# measurement.

#: Statuses that mean a human has been over the page — one definition, kept in
#: `gt_score` where "is this ground truth" is decided. DONE counts: this project's
#: collection never used the GT tag, and requiring FINAL would discard the larger
#: half of what has actually been corrected.
from gt_score import CORRECTED_STATUSES as GT_STATUSES  # noqa: E402


def document_content(colid: str, docid: str, sid: str,
                     session: Optional[requests.Session] = None) -> dict:
    """One document with its page list and every page's transcript versions."""
    s = session or requests.Session()
    r = s.get(f"{API}/collections/{colid}/{docid}/fulldoc",
              params={"JSESSIONID": sid}, timeout=_TIMEOUT)
    if r.status_code != 200:
        raise TranskribusError(
            f"cannot read document {docid} of collection {colid}: "
            f"{r.status_code} — {r.text[:200].strip()}")
    return r.json()


def corrected_pages(content: dict, statuses: Iterable[str] = GT_STATUSES
                    ) -> list[dict]:
    """``{page_nr, status, url, ts_id}`` for every page a human has signed off.

    Only ``transcripts[0]`` is looked at — the latest version. An older version
    that was once FINAL is not the page's current state, and harvesting it would
    score our readings against a transcription somebody has since corrected away.
    """
    wanted = {s.upper() for s in statuses}
    out: list[dict] = []
    for page in (content.get("pageList") or {}).get("pages") or []:
        versions = (page.get("tsList") or {}).get("transcripts") or []
        if not versions:
            continue
        latest = versions[0]
        status = str(latest.get("status") or "").upper()
        if status not in wanted:
            continue
        url = latest.get("url")
        if not url:
            continue
        out.append({"page_nr": page.get("pageNr"), "status": status,
                    "url": url, "ts_id": latest.get("tsId")})
    return out


def harvest_ground_truth(colid: str, sid: str, out_dir: Path, *,
                         statuses: Iterable[str] = GT_STATUSES,
                         session: Optional[requests.Session] = None,
                         limit: Optional[int] = None) -> list[Path]:
    """Download every hand-corrected page of a collection as PAGE XML.

    One file per page, named for the identities that are actually stable —
    ``doc<docId>_page<pageNr>_ts<tsId>.xml``. The transcript id is in the name
    because a page can be corrected again: two harvests of the same page then sit
    side by side instead of one silently overwriting the other, and which was
    scored stays answerable.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    s = session or requests.Session()

    written: list[Path] = []
    documents = collection_documents(colid, sid, session=s)
    logger.info(f"[transkribus] collection {colid}: {len(documents)} document(s)")
    for doc in documents:
        docid = doc.get("docId")
        if docid is None:
            continue
        pages = corrected_pages(document_content(colid, docid, sid, session=s),
                                statuses)
        if not pages:
            continue
        logger.info(f"[transkribus] doc {docid} ({doc.get('title', '')!r}): "
                    f"{len(pages)} corrected page(s)")
        for page in pages:
            dest = out_dir / (f"doc{docid}_page{page['page_nr']}"
                              f"_ts{page['ts_id']}.xml")
            if dest.exists():
                written.append(dest)
                continue           # resumable: already harvested
            r = s.get(page["url"], params={"JSESSIONID": sid}, timeout=_TIMEOUT)
            if r.status_code != 200:
                logger.warning(f"[transkribus] {dest.name}: {r.status_code}, skipped")
                continue
            dest.write_text(r.text, encoding="utf-8")
            written.append(dest)
            if limit is not None and len(written) >= limit:
                return written
    return written


# ── the seam: the one call this module cannot yet make ───────────────────────

def _upload_id(text: str) -> str:
    """The upload id out of the server's answer, JSON or XML — the legacy
    server answers in whichever the Accept header asked for, and an answer in
    a third shape is reported verbatim rather than guessed at."""
    import json as _json
    import xml.etree.ElementTree as et

    body = (text or "").strip()
    try:
        data = _json.loads(body)
        for k in ("uploadId", "id"):
            if isinstance(data, dict) and data.get(k) is not None:
                return str(data[k])
    except ValueError:
        pass
    try:
        root = et.fromstring(body)
        for tag in ("uploadId", "id"):
            el = root.find(f".//{tag}")
            if el is not None and (el.text or "").strip():
                return el.text.strip()
    except et.ParseError:
        pass
    raise TranskribusError(f"upload created, but the answer names no upload id: "
                           f"{body[:300]!r}")


def create_upload(colid: str, letter: Letter, sid: str,
                  session: Optional[requests.Session] = None) -> str:
    """Begin an upload for one letter and return its upload id.

    The legacy server's structured upload: ``POST /uploads?collId=`` with the
    document's metadata and the list of page file names, in order; the server
    answers with an upload id, and each page is then ``PUT`` against it
    (:func:`put_page`). The document is created when the last page has arrived.

    Left unimplemented until 2026-10-23 because the endpoint was remembered,
    not read; it is now exercised the only way that confirms it — one letter
    into the empty collection the upload was asked for, checked in the
    interface — which the CLI enforces by taking ``--letter`` before ``--all``.
    An answer of an unexpected shape stops here with the body in the error.
    """
    s = session or requests.Session()
    body = {"md": {k: v for k, v in (letter.md or {"title": letter.title}).items() if v},
            "pageList": {"pages": [{"fileName": p.name, "pageNr": i}
                                   for i, p in enumerate(letter.pages, 1)]}}
    r = s.post(f"{API}/uploads", params={"collId": str(colid), "JSESSIONID": sid},
               json=body, headers={"Accept": "application/json"}, timeout=_TIMEOUT)
    if r.status_code not in (200, 201):
        raise TranskribusError(
            f"cannot begin upload of {letter.title!r} into {colid}: {r.status_code} — "
            f"{r.text[:300].strip()}")
    return _upload_id(r.text)


def put_page(upload_id: str, page: Path, sid: str,
             session: Optional[requests.Session] = None,
             page_xml: Optional[Path] = None) -> dict:
    """Send one page image (and optionally its PAGE XML) into an upload.

    Multipart ``img`` and ``xml`` parts, the file names those the upload was
    begun with. The answer is the upload's state; when the last page lands it
    carries the ingest job's id, which is returned for the record."""
    s = session or requests.Session()
    files = {"img": (page.name, page.read_bytes(), "image/jpeg")}
    if page_xml is not None:
        files["xml"] = (page_xml.name, page_xml.read_bytes(), "application/xml")
    r = s.put(f"{API}/uploads/{upload_id}", params={"JSESSIONID": sid},
              files=files, headers={"Accept": "application/json"}, timeout=max(_TIMEOUT, 600))
    if r.status_code not in (200, 201):
        raise TranskribusError(
            f"page {page.name} refused by upload {upload_id}: {r.status_code} — "
            f"{r.text[:300].strip()}")
    try:
        data = r.json() if r.text.strip() else {}
    except ValueError:
        data = {"raw": r.text[:300]}
    return data if isinstance(data, dict) else {"raw": str(data)[:300]}


#: Every letter that went up, one JSON line each: collection, letter, upload
#: and job id, pages, when (#713). Not state — the collection is the state, and
#: a letter whose title is there is skipped — but the record a partial upload
#: is traced back with.
UPLOAD_LOG = config.DATA_DIR / "transkribus_uploads.jsonl"


def log_upload(colid: str, letter: Letter, result: dict, *, mode: str,
               path: Optional[Path] = None) -> None:
    import json
    from datetime import datetime, timezone

    path = Path(path or UPLOAD_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "collection": str(colid), "letter": letter.title, "mode": mode,
            "pages": result.get("pages"), "upload_id": result.get("upload_id"),
            "job_id": result.get("job_id")}, ensure_ascii=False) + "\n")


def upload_collections() -> list[str]:
    """Collections a letter-wise upload may write to (#713):
    ``TRANSKRIBUS_UPLOAD_COLLECTIONS`` (comma list), else
    ``TRANSKRIBUS_COLLECTION``. The ground-truth collection is never among them
    by default — a document in the wrong collection is visible to everybody
    working there at once."""
    raw = config._get("TRANSKRIBUS_UPLOAD_COLLECTIONS", "") or config._get("TRANSKRIBUS_COLLECTION", "")
    return [c.strip() for c in str(raw).split(",") if c.strip()]


def first_letter_confirmed(plan: "Plan") -> bool:
    """True when the collection already holds a letter of this plan — the one
    the protocol uploads first and a person looks at before the rest."""
    return bool(plan.skipped)


def upload_letter(colid: str, letter: Letter, sid: str,
                  session: Optional[requests.Session] = None,
                  progress=None) -> dict:
    """One letter, start to finish: begin, every page in order, the last answer."""
    upload_id = create_upload(colid, letter, sid, session=session)
    last: dict = {}
    for i, page in enumerate(letter.pages, 1):
        last = put_page(upload_id, page, sid, session=session)
        if progress:
            progress(letter, i, len(letter.pages))
    return {"upload_id": upload_id, "pages": len(letter.pages),
            "job_id": last.get("jobId") or last.get("jobID") or (last.get("upload") or {}).get("jobId"),
            "answer": {k: v for k, v in last.items() if k in ("uploadId", "jobId", "finished", "pageList")} or last}
