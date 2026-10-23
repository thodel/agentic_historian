"""transkribus_pages.py — the pages of a collection without segmentation, and the
way back: a new transcript version per page.

Built on :mod:`transkribus` (login, collections, ``fulldoc``). What that module
left open — writing into Transkribus — is settled here for the one write this
test needs: ``POST /collections/{colId}/{docId}/{pageNr}/text`` puts a PAGE XML
on an **existing** page as a new transcript version. No image is uploaded; the
Rümlang pages are already in Transkribus, which is also why "no segmentation"
is a question the collection can answer: the latest transcript's ``nrOfLines``.

Everything here is resumable by what is on disk and dry-runnable: the plan is a
JSON file, the upload reads the XML beside it, and nothing is posted twice —
a page with an ``uploaded.json`` is skipped.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests
from loguru import logger

import transkribus as tk

API = tk.API
_TIMEOUT = 120
#: account.readcoop.eu issues bearer tokens for the same REST API when the legacy
#: login is refused; the reformation project's sync script uses the same fallback.
OIDC_TOKEN_URL = ("https://account.readcoop.eu/auth/realms/readcoop/protocol/"
                  "openid-connect/token")
OIDC_CLIENT_ID = "transkribus-api-client"

#: Transcript statuses Transkribus knows, lowest first. A machine reading goes
#: in as IN_PROGRESS: it is a proposal for a human, never a DONE.
STATUSES = ("NEW", "IN_PROGRESS", "DONE", "FINAL", "GT")


@dataclass
class PageInfo:
    """One page as ``fulldoc`` describes it, reduced to what the pipeline needs."""

    colid: int
    docid: int
    doc_title: str
    page_nr: int
    page_id: Optional[int]
    image_url: str
    width: Optional[int]
    height: Optional[int]
    ts_id: Optional[int]
    status: Optional[str]
    nr_regions: int
    nr_lines: int
    nr_words: int
    xml_url: Optional[str]
    nr_transcribed_lines: int = 0

    @property
    def key(self) -> str:
        """Stable, sortable, filesystem-safe: ``doc<docId>_p<pageNr>``."""
        return f"doc{self.docid}_p{self.page_nr:04d}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["key"] = self.key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PageInfo":
        fields = {k: d.get(k) for k in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        for k in ("nr_regions", "nr_lines", "nr_words", "nr_transcribed_lines"):
            fields[k] = int(fields[k] or 0)
        return cls(**fields)


class TkSession:
    """A requests session that carries its Transkribus credential on every call.

    Three ways in, tried in this order: ``TRANSKRIBUS_ACCESS_TOKEN`` (a bearer
    token somebody already has), the legacy ``/auth/login`` (session id as the
    ``JSESSIONID`` query parameter), and an OIDC password grant at
    account.readcoop.eu (bearer header) — collections created in the new
    app.transkribus.org may be visible only through the latter. The password is
    ``TRANSKRIBUS_PASS`` (this project) or ``TRANSKRIBUS_PASSWORD`` (the
    reformation project); either name works so one ``.env`` serves both.
    """

    def __init__(self, session: Optional[requests.Session] = None):
        self.s = session or requests.Session()
        self.kind = ""          # "sid" | "bearer"
        self.value = ""
        self.source = ""

    @staticmethod
    def credentials() -> tuple[str, str]:
        import config
        user = config._get("TRANSKRIBUS_USER", "")
        password = config._get("TRANSKRIBUS_PASS", "") or config._get("TRANSKRIBUS_PASSWORD", "")
        return user, password

    def authenticate(self) -> "TkSession":
        import config
        token = config._get("TRANSKRIBUS_ACCESS_TOKEN", "")
        if token:
            self.kind, self.value, self.source = "bearer", token, "TRANSKRIBUS_ACCESS_TOKEN"
            return self
        user, password = self.credentials()
        if not user or not password:
            raise tk.TranskribusError(
                "TRANSKRIBUS_USER and TRANSKRIBUS_PASS (or TRANSKRIBUS_PASSWORD) must be set")
        try:
            self.kind, self.value, self.source = "sid", tk.login(user, password, session=self.s), "legacy /auth/login"
            return self
        except tk.TranskribusError as legacy:
            r = self.s.post(OIDC_TOKEN_URL, data={
                "grant_type": "password", "client_id": OIDC_CLIENT_ID,
                "username": user, "password": password}, timeout=_TIMEOUT)
            if r.status_code != 200 or not r.json().get("access_token"):
                raise tk.TranskribusError(
                    f"legacy login failed ({legacy}); OIDC token failed ({r.status_code})") from legacy
            self.kind, self.value, self.source = "bearer", r.json()["access_token"], "OIDC password grant"
            logger.info("[ruemlang] Transkribus: legacy login refused, using an OIDC bearer token")
            return self

    # ── requests with the credential attached ────────────────────────────────
    def _auth(self, kw: dict) -> dict:
        if self.kind == "sid":
            kw["params"] = {**(kw.get("params") or {}), "JSESSIONID": self.value}
        elif self.kind == "bearer":
            kw["headers"] = {**(kw.get("headers") or {}), "Authorization": f"Bearer {self.value}"}
        kw.setdefault("timeout", _TIMEOUT)
        return kw

    def get(self, url: str, **kw) -> requests.Response:
        return self.s.get(url, **self._auth(kw))

    def post(self, url: str, **kw) -> requests.Response:
        return self.s.post(url, **self._auth(kw))

    def get_json(self, url: str, what: str, **kw):
        r = self.get(url, **kw)
        if r.status_code != 200:
            raise tk.TranskribusError(f"{what}: {r.status_code} — {r.text[:200].strip()}")
        return r.json()


def _fold(s: str) -> str:
    """Case- and diacritic-insensitive: 'Rümlang', 'Ruemlang', 'RUMLANG' meet."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return s.replace("ue", "u").replace("oe", "o").replace("ae", "a")


def find_collection(ts: TkSession, name: str) -> dict:
    """The one collection whose name contains ``name`` — or its id, as digits.

    Ambiguity is an error that lists the candidates, never a silent first match:
    a test that writes into the wrong collection is somebody else's cleanup.
    """
    cols = ts.get_json(f"{API}/collections/list", "cannot list collections")
    if str(name).isdigit():
        hits = [c for c in cols if str(c.get("colId")) == str(name)]
    else:
        hits = [c for c in cols if _fold(name) in _fold(str(c.get("colName", "")))]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(f"{c.get('colId')}:{c.get('colName')}" for c in (hits or cols)[:40])
    if not hits:
        raise tk.TranskribusError(
            f"no collection matches {name!r}; the account sees {len(cols)}: {names}")
    raise tk.TranskribusError(f"{len(hits)} collections match {name!r}: {names}")


def pages_of_document(ts: TkSession, colid: int, docid: int) -> list[PageInfo]:
    """Every page of a document with its **latest** transcript's counts.

    Only ``transcripts[0]`` is read, for the same reason :func:`transkribus.corrected_pages`
    gives: an older version is not the page's current state.
    """
    content = ts.get_json(f"{API}/collections/{colid}/{docid}/fulldoc",
                          f"cannot read document {docid} of collection {colid}")
    title = str((content.get("md") or {}).get("title") or "")
    out: list[PageInfo] = []
    for page in (content.get("pageList") or {}).get("pages") or []:
        versions = (page.get("tsList") or {}).get("transcripts") or []
        latest = versions[0] if versions else {}
        out.append(PageInfo(
            colid=int(colid), docid=int(docid), doc_title=title,
            page_nr=int(page.get("pageNr") or 0), page_id=page.get("pageId"),
            image_url=str(page.get("url") or ""),
            width=page.get("width"), height=page.get("height"),
            ts_id=latest.get("tsId"), status=latest.get("status"),
            nr_regions=int(latest.get("nrOfRegions") or 0),
            nr_lines=int(latest.get("nrOfLines") or 0),
            nr_words=int(latest.get("nrOfWords") or 0),
            xml_url=latest.get("url"),
            nr_transcribed_lines=int(latest.get("nrOfTranscribedLines") or 0),
        ))
    out.sort(key=lambda p: p.page_nr)
    return out


def is_unsegmented(p: PageInfo) -> bool:
    """No text line in the latest transcript. A region without lines counts as
    unsegmented too: nothing a recogniser could read is drawn on the page."""
    return p.nr_lines == 0


def is_untranscribed(p: PageInfo) -> bool:
    """Lines drawn, but no text in any of them: a layout somebody ran and
    nobody read. Decided 2026-10-15 to treat these like unsegmented pages — the
    gateway's own segmentation replaces the empty one in a **new version**, the
    empty version stays as its parent."""
    return p.nr_lines > 0 and p.nr_transcribed_lines == 0 and p.nr_words == 0


def wanted(p: PageInfo, *, include_untranscribed: bool = True) -> bool:
    return is_unsegmented(p) or (include_untranscribed and is_untranscribed(p))


def collection_pages(ts: TkSession, colid: int, *,
                     docs: Optional[Iterable[int]] = None) -> list[PageInfo]:
    """All pages of a collection (or of the documents ``docs`` names)."""
    documents = ts.get_json(f"{API}/collections/{colid}/list",
                            f"cannot list collection {colid}")
    chosen = {int(d) for d in docs} if docs else None
    pages: list[PageInfo] = []
    for doc in documents:
        docid = doc.get("docId")
        if docid is None or (chosen is not None and int(docid) not in chosen):
            continue
        got = pages_of_document(ts, int(colid), int(docid))
        logger.info(f"[ruemlang] doc {docid} {doc.get('title', '')!r}: {len(got)} page(s), "
                    f"{sum(is_unsegmented(p) for p in got)} without lines, "
                    f"{sum(is_untranscribed(p) for p in got)} with lines but no text")
        pages.extend(got)
    return pages


def download_image(ts: TkSession, p: PageInfo, dest: Path) -> Path:
    """The page image as Transkribus serves it, through a temp file."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    r = ts.get(p.image_url, stream=True)
    if r.status_code != 200:
        raise tk.TranskribusError(f"{p.key}: image {r.status_code} from {p.image_url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with tmp.open("wb") as fh:
        for chunk in r.iter_content(1 << 16):
            fh.write(chunk)
    tmp.replace(dest)
    return dest


def download_pagexml(ts: TkSession, p: PageInfo) -> Optional[str]:
    """The latest transcript's PAGE XML, or None when the page has none."""
    if not p.xml_url:
        return None
    r = ts.get(p.xml_url)
    if r.status_code != 200:
        raise tk.TranskribusError(f"{p.key}: PAGE XML {r.status_code}")
    return r.text


def post_pagexml(ts: TkSession, colid: int, docid: int, page_nr: int, xml: str | bytes, *,
                 status: str = "IN_PROGRESS", note: str = "",
                 parent_ts: Optional[int] = None,
                 tool_name: str = "agentic_historian ruemlang") -> dict:
    """A new transcript version on an existing page.

    ``POST /collections/{colId}/{docId}/{pageNr}/text`` with the PAGE XML as the
    body; ``status`` becomes the version's status, ``note`` its edit note and
    ``parent`` the version it supersedes, so Transkribus shows the lineage.
    """
    if status.upper() not in STATUSES:
        raise tk.TranskribusError(f"unknown transcript status {status!r}; one of {STATUSES}")
    body = xml.encode("utf-8") if isinstance(xml, str) else xml
    params = {"status": status.upper(), "note": note[:500], "toolName": tool_name}
    if parent_ts is not None:
        params["parent"] = str(parent_ts)
    r = ts.post(f"{API}/collections/{colid}/{docid}/{page_nr}/text", params=params,
                data=body, headers={"Content-Type": "application/xml; charset=utf-8"})
    if r.status_code not in (200, 201):
        raise tk.TranskribusError(
            f"doc{docid}_p{page_nr:04d}: text upload {r.status_code} — {r.text[:300].strip()}")
    try:
        return r.json()
    except ValueError:
        return {"raw": r.text[:500]}


def write_plan(path: Path, collection: dict, pages: list[PageInfo]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({
        "collection": {"colId": collection.get("colId"), "colName": collection.get("colName")},
        "pages": [p.to_dict() for p in pages],
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def read_plan(path: Path) -> tuple[dict, list[PageInfo]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["collection"], [PageInfo.from_dict(d) for d in data["pages"]]
