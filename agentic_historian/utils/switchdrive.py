"""
utils/switchdrive.py — pull image/PDF folders from SwitchDrive via WebDAV.

Auth: SwitchDrive username + an **app password** (drive.switch.ch → Settings →
Security → create app password), set in .env.gpustack as SWITCHDRIVE_USER /
SWITCHDRIVE_PASS. The folder to ingest lives under your SwitchDrive root
(SWITCHDRIVE_REMOTE_DIR, or passed per-call).

Used by the bot's /pull command to bring a whole folder of source images into the
local hot folder for processing.
"""

import json
from pathlib import Path
from typing import Optional

from loguru import logger

import config

# Tracks which orders (subfolders) have already been processed, so re-runs skip them.
_PROCESSED_FILE = config.DATA_DIR / "processed_orders.json"

#: ``.pdf`` belongs here and must stay. A PDF is not a page — ``atr_batch``
#: decides what each one is, skipping a derivative of the scans beside it and
#: rendering a lone one to a JPEG per sheet (#476) — but it has to be *listed*
#: first, or the folders where a PDF is the only digitisation vanish before
#: anything can look at them. Removing it here is the silent half of that bug.
INGEST_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".pdf"}


def is_configured() -> bool:
    """Whether the credentials are **present**.

    Not whether they are accepted — see :func:`preflight`. The distinction cost
    an afternoon on 2026-10-09: `/pull_folder missiven` answered
    `received 401 (Unauthorized)`, which is what this function cannot tell you,
    and the search went network → endpoint → path → credentials in that order.
    """
    return bool(
        config.SWITCHDRIVE_URL
        and config.SWITCHDRIVE_USER
        and config.SWITCHDRIVE_PASS
    )


def _secret_source() -> str:
    """Which file this process read ``SWITCHDRIVE_PASS`` from, or "" for none.

    "" is the interesting answer: it means the value came from the real process
    environment, where ``load_dotenv(override=False)`` cannot replace it. A
    service started with a stale password keeps using it however often the file
    is corrected — observed on the Nextcloud share on 2026-10-03, same shape.
    """
    source = config.ENV_SOURCE.get("SWITCHDRIVE_PASS")
    if source is None:
        return ""
    try:
        return str(Path(source).relative_to(config.REPO_ROOT))
    except (ValueError, TypeError):              # pragma: no cover — defensive
        return str(source)


def _advise(layer: str, status, found) -> str:
    """What to change, for an **account** mailbox. The share path says its own
    thing (``nextcloud``), which is why this is a hook and not a table in
    ``webdav_probe``."""
    if layer == "config":
        return ("`SWITCHDRIVE_USER` und `SWITCHDRIVE_PASS` (App-Passwort) in "
                "`.env.gpustack` setzen, dann den Bot neu starten — "
                "`config.py` liest die Datei beim Import.")
    if layer == "network":
        return (f"`{config.SWITCHDRIVE_URL}` war nicht erreichbar. Das ist der "
                f"Host, nicht der Ordner: DNS, Firewall oder ein Ausfall von "
                f"drive.switch.ch.")
    if layer == "auth":
        if status == 401:
            where = (f"aus `{found.secret_source}`" if found.secret_source
                     else "**aus der Prozess-Umgebung, nicht aus einer "
                          "`.env`-Datei** — `load_dotenv(override=False)` kann "
                          "einen dort gesetzten Wert nicht ersetzen, ein mit "
                          "veraltetem Passwort gestarteter Dienst benutzt ihn "
                          "weiter (`/proc/<pid>/environ` und `EnvironmentFile=` "
                          "prüfen)")
            return (f"Die Zugangsdaten wurden abgelehnt — nicht der Ordner, "
                    f"nicht das Netz. Dieser Prozess bot `{found.user}` mit "
                    f"{found.secret_chars} Zeichen {where}.\n"
                    f"SwitchDrive-App-Passwörter werden ungültig, wenn das "
                    f"edu-ID-Passwort wechselt oder 2FA neu eingerichtet wird: "
                    f"drive.switch.ch → Einstellungen → Sicherheit → "
                    f"App-Passwort erzeugen, `SWITCHDRIVE_PASS` ersetzen, Bot "
                    f"neu starten.\n"
                    f"Ob `{found.user}` überhaupt das richtige Konto ist, kann "
                    f"nur ein Mensch beurteilen — im Browser oben rechts "
                    f"nachsehen, als wer der Ordner dort sichtbar ist.")
        if status == 403:
            return ("Authentifiziert, aber die Wurzel ist für dieses Konto "
                    "gesperrt. Das ist eine Kontofrage, keine Pfadfrage.")
        return (f"Die Wurzel antwortete mit {status}. Ein Browser-Zugriff "
                f"beweist hier nichts: der Browser hat eine Session, dieser "
                f"Aufruf benutzt Basic-Auth.")
    if layer == "path":
        if status == 404:
            return (f"Zugangsdaten sind gut — `{found.target}` existiert nicht "
                    f"unterhalb der Wurzel **dieses** Kontos. Ein *geteilter* "
                    f"Ordner hängt im WebDAV-Baum anders als in der Web-UI; "
                    f"dann braucht es den Freigabe-Weg (`utils/nextcloud.py`), "
                    f"nicht diesen.")
        if status == 403:
            return (f"Authentifiziert, aber keine Leseberechtigung für "
                    f"`{found.target}`.")
        return f"`{found.target}` antwortete mit {status}."
    return ""


def preflight(remote_dir: Optional[str] = None, **kw):
    """Check configuration, host, credentials and path **separately** (#563).

    The auth layer asks about the WebDAV **root**, not the target folder, and
    that is the whole point: a ``PROPFIND`` on the target answers 401 before it
    looks at the path, so a dead app password and a missing folder arrive as one
    sentence. 401 at the root is the credentials; 207 at the root and 404 below
    it is the path.

    A layer after a failure reports ``unknown``, never ``failed`` — after a 401
    nothing has been learned about the folder, and saying "path: failed" there
    is what sent us looking at the folder first.
    """
    import webdav_probe
    return webdav_probe.probe(
        label="SwitchDrive",
        endpoint=config.SWITCHDRIVE_URL,
        user=config.SWITCHDRIVE_USER,
        secret=config.SWITCHDRIVE_PASS,
        secret_key="SWITCHDRIVE_PASS",
        secret_source=_secret_source(),
        target=_resolve_remote(remote_dir) if remote_dir else "",
        exts=INGEST_EXTS,
        advise=_advise,
        **kw)


def explain(exc: Exception, remote_dir: str = "") -> str:
    """One sentence naming the thing to change, for an error from a pull.

    `/pull` is the path a historian actually takes; the raw HTTP status is for
    whoever is debugging. A 401 here means the credentials were rejected, and
    saying so beats making them rule out the network and the folder first.
    """
    import webdav_probe
    status = webdav_probe.status_of(exc)
    found = webdav_probe.Probe(
        label="SwitchDrive", endpoint=config.SWITCHDRIVE_URL,
        target=_resolve_remote(remote_dir) if remote_dir else "",
        user=config.SWITCHDRIVE_USER,
        secret_chars=len(config.SWITCHDRIVE_PASS or ""),
        secret_source=_secret_source())
    if status == 401:
        return _advise("auth", status, found)
    if status in (403, 404):
        return _advise("path", status, found)
    return ""


def _client():
    """Build a webdav4 client rooted at the SwitchDrive WebDAV endpoint.

    SWITCHDRIVE_URL is the fixed endpoint https://drive.switch.ch/remote.php/webdav/
    (per help.switch.ch) — the username is NOT part of the path; it goes in auth.
    """
    from webdav4.client import Client  # lazy import; optional dependency

    base = config.SWITCHDRIVE_URL.rstrip("/") + "/"
    return Client(base, auth=(config.SWITCHDRIVE_USER, config.SWITCHDRIVE_PASS))


def _resolve_remote(remote_dir: str) -> str:
    """
    Resolve a remote path:
    - if it already contains SWITCHDRIVE_REMOTE_DIR, return as-is
    - otherwise prepend SWITCHDRIVE_REMOTE_DIR
    (handles 'test' → 'agentic_historian_hotfolder/test')
    """
    remote_dir = remote_dir.strip("/")
    base = config.SWITCHDRIVE_REMOTE_DIR.strip("/")
    if base in remote_dir:
        return remote_dir
    return f"{base}/{remote_dir}"


def pull_folder(
    remote_dir: Optional[str] = None,
    local_dir: Optional[Path] = None,
    recursive: bool = False,
) -> list[Path]:
    """
    Download all image/PDF files from a SwitchDrive folder into `local_dir`.

    Args:
        remote_dir: folder relative to your SwitchDrive root
                    (defaults to config.SWITCHDRIVE_REMOTE_DIR).
                    Can be a short name like 'test' (resolved to
                    SWITCHDRIVE_REMOTE_DIR/test) or a full sub-path.
        local_dir:  destination (defaults to config.HOT_FOLDER).
        recursive:  also descend into subfolders.

    Returns the list of downloaded local paths.
    """
    if not is_configured():
        raise RuntimeError(
            "SwitchDrive not configured — set SWITCHDRIVE_USER and SWITCHDRIVE_PASS "
            "(app password) in .env.gpustack."
        )
    remote_dir = _resolve_remote(remote_dir or config.SWITCHDRIVE_REMOTE_DIR)
    local_dir = Path(local_dir or config.HOT_FOLDER)
    local_dir.mkdir(parents=True, exist_ok=True)

    client = _client()
    downloaded: list[Path] = []

    def _walk(rdir: str) -> None:
        for entry in client.ls(rdir, detail=True):
            name = entry.get("name") or entry.get("href")  # path relative to base
            if not name:
                continue
            etype = entry.get("type", "file")
            if etype == "directory":
                if recursive and name.rstrip("/") != rdir.rstrip("/"):
                    _walk(name)
                continue
            if Path(name).suffix.lower() in INGEST_EXTS:
                # Collision-safe: encode the path relative to the pulled root into
                # the filename (saa-0428/001r.jpg → saa-0428__001r.jpg) so files in
                # different subfolders never overwrite each other.
                rel = name[len(remote_dir):].lstrip("/") if name.startswith(remote_dir) else Path(name).name
                safe = rel.replace("/", "__") or Path(name).name
                dest = local_dir / safe
                client.download_file(name, str(dest))
                downloaded.append(dest)
                logger.info(f"[SwitchDrive] pulled {name} → {dest}")

    logger.info(f"[SwitchDrive] pulling '{remote_dir}' (recursive={recursive})")
    _walk(remote_dir)
    logger.info(f"[SwitchDrive] {len(downloaded)} file(s) downloaded")
    return downloaded


def list_subdirs(remote_dir: Optional[str] = None) -> list[str]:
    """Immediate subfolders ('orders') under remote_dir (paths relative to base)."""
    if not is_configured():
        raise RuntimeError(
            "SwitchDrive not configured — set SWITCHDRIVE_USER and SWITCHDRIVE_PASS."
        )
    remote_dir = _resolve_remote(remote_dir or config.SWITCHDRIVE_REMOTE_DIR)
    client = _client()
    subs = []
    for entry in client.ls(remote_dir, detail=True):
        name = (entry.get("name") or entry.get("href") or "").rstrip("/")
        if entry.get("type") == "directory" and name and name != remote_dir:
            subs.append(name)
    # If no subdirs, return [remote_dir] so the caller can treat the folder
    # itself as a single order (e.g. /pull test on a flat folder of images).
    return sorted(subs) if subs else [remote_dir]


def load_processed() -> set:
    """Set of already-processed order ids."""
    try:
        return set(json.loads(_PROCESSED_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def mark_processed(order_id: str) -> None:
    """Record an order id as processed."""
    done = load_processed()
    done.add(order_id)
    _PROCESSED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PROCESSED_FILE.write_text(
        json.dumps(sorted(done), ensure_ascii=False, indent=2), encoding="utf-8"
    )