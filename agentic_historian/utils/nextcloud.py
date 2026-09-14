"""
utils/nextcloud.py — mirror a Nextcloud **public share** folder over WebDAV.

Why a second WebDAV client next to ``utils/switchdrive.py``: a SwitchDrive pull
authenticates as a *user* (username + app password) against that user's own root.
A public share has no user. It authenticates with the **share token as the
username** and the share password as the password, against a different endpoint,
and it is rooted at the share rather than at anybody's drive. The two overlap in
the protocol and in nothing else, so folding them together would mean a client
that takes credentials it does not use half the time.

Configured in ``.env.gpustack``::

    NEXTCLOUD_SHARE_URL=https://cloud.example.org/nextcloud/s/AbCdEf123456
    NEXTCLOUD_SHARE_PASS=…            # empty when the share has no password
    NEXTCLOUD_REMOTE_DIR=digitalisate # subfolder inside the share ("" = its root)

The share URL is the one you paste from a browser. Every shape Nextcloud hands
out is accepted — ``/s/<token>``, ``/index.php/s/<token>``, and the
``…/authenticate/showshare`` form a password-protected share redirects to — so
nobody has to know which part of it is the token.

**Mirror, don't mount.** The recogniser uploads bytes per page, so the files have
to be local anyway; a fuse mount only moves the same transfer to a place where a
dropped connection kills a multi-hour run instead of one download. ``pull_folder``
is resumable and safe to re-run: a file already present at the right size is
skipped, and every download lands through a temp file, so an interrupted transfer
can never be mistaken for a complete one on the next pass.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlsplit, urlunsplit

from loguru import logger

import config

__all__ = [
    "INGEST_EXTS",
    "ShareRef",
    "NextcloudError",
    "parse_share_url",
    "is_configured",
    "list_files",
    "pull_folder",
]

#: What counts as material to process. Same set as the SwitchDrive ingest, so a
#: folder pulled from either side yields the same files.
INGEST_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".pdf"}

#: Trailing path segments Nextcloud appends to a share URL that are part of the
#: *page*, never of the share identity. ``authenticate/showshare`` is what a
#: password-protected share redirects to, and it is what a person copies out of
#: the address bar — so it has to be stripped rather than rejected.
_SHARE_SUFFIXES = ("authenticate/showshare", "authenticate", "download", "preview")

_TOKEN_RE = re.compile(r"/(?:index\.php/)?s/([A-Za-z0-9_\-]+)")


class NextcloudError(RuntimeError):
    """Configuration or transfer problem talking to the share."""


@dataclass(frozen=True)
class ShareRef:
    """A public share, reduced to the three things a WebDAV call needs."""

    #: Nextcloud's root URL, e.g. ``https://cloud.example.org/nextcloud``
    base_url: str
    #: The share token — also the WebDAV *username*.
    token: str
    #: Share password; ``""`` for a share that has none.
    password: str = ""

    @property
    def webdav_url(self) -> str:
        """The legacy public-share endpoint, served by every Nextcloud version."""
        return f"{self.base_url}/public.php/webdav"

    @property
    def dav_url(self) -> str:
        """The newer per-token endpoint (Nextcloud 30+). Tried as a fallback: on
        older servers it 404s, and on newer ones the legacy path still works — so
        neither is safe to pick unconditionally."""
        return f"{self.base_url}/public.php/dav/files/{self.token}"

    def __str__(self) -> str:  # never print the password
        return f"{self.base_url}/s/{self.token}"


def parse_share_url(url: str, password: str = "") -> ShareRef:
    """Split a pasted share URL into ``ShareRef(base_url, token)``.

    Raises ``NextcloudError`` when no token can be found, rather than guessing:
    an unparsed URL would otherwise surface much later as an authentication
    failure against an endpoint nobody meant to call.
    """
    url = (url or "").strip()
    if not url:
        raise NextcloudError("empty share URL")
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise NextcloudError(f"not an absolute URL: {url!r}")

    path = parts.path.rstrip("/")
    for suffix in _SHARE_SUFFIXES:
        if path.endswith("/" + suffix):
            path = path[: -(len(suffix) + 1)]

    m = _TOKEN_RE.search(path)
    if not m:
        raise NextcloudError(
            f"no share token in {url!r} — expected a URL containing '/s/<token>'"
        )
    token = m.group(1)
    base_path = path[: m.start()].rstrip("/")
    base_url = urlunsplit((parts.scheme, parts.netloc, base_path, "", ""))
    return ShareRef(base_url=base_url, token=token, password=password)


def share_from_config() -> ShareRef:
    """The share described by the environment. Raises if it is not configured."""
    if not config.NEXTCLOUD_SHARE_URL:
        raise NextcloudError(
            "Nextcloud share not configured — set NEXTCLOUD_SHARE_URL (and "
            "NEXTCLOUD_SHARE_PASS if the share is password-protected)."
        )
    return parse_share_url(config.NEXTCLOUD_SHARE_URL, config.NEXTCLOUD_SHARE_PASS)


def is_configured() -> bool:
    """True when a share URL is set and parses. Never raises."""
    try:
        share_from_config()
        return True
    except NextcloudError:
        return False


def _client(share: ShareRef, endpoint: Optional[str] = None):
    """A webdav4 client rooted at the share.

    The share token is the username and the share password the password — that is
    the whole of public-share auth. A share without a password still authenticates
    (token + empty string); omitting auth entirely gets a 401.
    """
    from webdav4.client import Client  # lazy import; optional dependency

    url = (endpoint or share.webdav_url).rstrip("/") + "/"
    return Client(url, auth=(share.token, share.password))


def _connect(share: ShareRef, probe_dir: str = ""):
    """Return a client for whichever public endpoint this server actually serves.

    Nextcloud 30 moved public shares to ``/public.php/dav/files/<token>`` while
    keeping the legacy ``/public.php/webdav`` working; some hardened deployments
    serve only one. Probing once here — with the directory the caller is about to
    read — turns "which Nextcloud is this" into a question answered by the server
    instead of by configuration.
    """
    errors: list[str] = []
    for endpoint in (share.webdav_url, share.dav_url):
        client = _client(share, endpoint)
        try:
            client.ls(probe_dir or "", detail=False)
            logger.debug(f"[Nextcloud] endpoint {endpoint}")
            return client
        except Exception as exc:  # noqa: BLE001 — try the next endpoint, report both
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise NextcloudError(
        f"cannot open share {share} at {probe_dir or '/'} — tried:\n  " + "\n  ".join(errors)
    )


def _walk(client, rdir: str, recursive: bool) -> Iterator[tuple[str, int]]:
    """Yield ``(remote_path, size)`` for every ingestable file under ``rdir``."""
    for entry in client.ls(rdir, detail=True):
        name = (entry.get("name") or entry.get("href") or "").rstrip("/")
        if not name or name == rdir.rstrip("/"):
            continue
        if entry.get("type") == "directory":
            if recursive:
                yield from _walk(client, name, recursive)
            continue
        if Path(name).suffix.lower() in INGEST_EXTS:
            size = entry.get("content_length") or entry.get("size") or 0
            yield name, int(size or 0)


def list_files(remote_dir: Optional[str] = None, recursive: bool = True,
               share: Optional[ShareRef] = None) -> list[tuple[str, int]]:
    """``(remote path, size)`` for every ingestable file under ``remote_dir``.

    Sorted, so a run over the same share processes pages in the same order twice —
    which is what makes a resumed run's progress comparable to the first one's.
    """
    share = share or share_from_config()
    remote_dir = (remote_dir if remote_dir is not None else config.NEXTCLOUD_REMOTE_DIR).strip("/")
    client = _connect(share, remote_dir)
    return sorted(_walk(client, remote_dir, recursive))


def _relative(remote_path: str, root: str) -> str:
    rel = remote_path[len(root):] if root and remote_path.startswith(root) else remote_path
    return rel.strip("/") or Path(remote_path).name


def pull_folder(
    remote_dir: Optional[str] = None,
    local_dir: Optional[Path] = None,
    recursive: bool = True,
    share: Optional[ShareRef] = None,
    limit: Optional[int] = None,
) -> list[Path]:
    """Mirror ``remote_dir`` into ``local_dir``, preserving the folder tree.

    Idempotent and resumable, which a several-hundred-page share needs:

    * a file already on disk **at the remote size** is skipped, so re-running
      after an interruption transfers only what is missing;
    * every download goes to ``<name>.part`` and is renamed only once complete,
      so a transfer killed halfway cannot be mistaken for a finished file by the
      next pass (a truncated JPEG is the kind of input that produces a
      transcription rather than an error).

    The tree is preserved rather than flattened the way the SwitchDrive pull does:
    a share's subfolders are usually the documents, and that grouping is worth
    keeping. Output names downstream flatten it back with ``/`` → ``__``.

    Returns every local path belonging to the share — skipped files included, so
    the caller gets the corpus, not just the delta.
    """
    share = share or share_from_config()
    root = (remote_dir if remote_dir is not None else config.NEXTCLOUD_REMOTE_DIR).strip("/")
    local_dir = Path(local_dir or (config.NEXTCLOUD_STAGING_DIR / (root or "share")))
    local_dir.mkdir(parents=True, exist_ok=True)

    client = _connect(share, root)
    remote_files = sorted(_walk(client, root, recursive))
    if limit is not None:
        remote_files = remote_files[:limit]
    if not remote_files:
        logger.warning(f"[Nextcloud] no ingestable files under '{root or '/'}' in {share}")
        return []

    out: list[Path] = []
    fetched = skipped = 0
    for remote_path, size in remote_files:
        dest = local_dir / _relative(remote_path, root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and (size == 0 or dest.stat().st_size == size):
            skipped += 1
            out.append(dest)
            continue
        tmp = dest.with_name(dest.name + ".part")
        try:
            client.download_file(remote_path, str(tmp))
            os.replace(tmp, dest)
        except Exception as exc:  # noqa: BLE001 — one bad file must not lose the rest
            tmp.unlink(missing_ok=True)
            logger.error(f"[Nextcloud] {remote_path} failed: {exc}")
            continue
        fetched += 1
        out.append(dest)
        if fetched % 25 == 0:
            logger.info(f"[Nextcloud] {fetched} fetched, {skipped} already present …")

    logger.info(
        f"[Nextcloud] '{root or '/'}': {len(out)} file(s) local "
        f"({fetched} fetched, {skipped} already present) → {local_dir}"
    )
    return out
