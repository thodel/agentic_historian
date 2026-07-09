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

INGEST_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".pdf"}


def is_configured() -> bool:
    return bool(config.SWITCHDRIVE_USER and config.SWITCHDRIVE_PASS)


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
    return sorted(subs)


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

# ── Push processed files back to SwitchDrive ────────────────────────────────

def push_processed(
    remote_subfolder: str = "processed",
    local_dir: Optional[Path] = None,
    order_id: Optional[str] = None,
) -> list[str]:
    """
    Upload all files from the local processed/ folder to SwitchDrive.

    Args:
        remote_subfolder: subfolder name under SWITCHDRIVE_REMOTE_DIR
                          (default "processed")
        local_dir:         source directory (default PROCESSED_FOLDER)
        order_id:          if set, only upload files whose names start with
                          this order_id prefix (for per-order sync)

    Returns list of uploaded remote paths.
    """
    if not is_configured():
        raise RuntimeError(
            "SwitchDrive not configured — set SWITCHDRIVE_USER and SWITCHDRIVE_PASS."
        )

    local_dir = Path(local_dir or config.PROCESSED_FOLDER)
    remote_base = _resolve_remote(config.SWITCHDRIVE_REMOTE_DIR)
    remote_dir = f"{remote_base}/{remote_subfolder}".rstrip("/")

    if not local_dir.exists():
        logger.warning(f"[SwitchDrive] Local processed dir not found: {local_dir}")
        return []

    # Collect files
    files = []
    for fp in local_dir.iterdir():
        if fp.is_file() and fp.suffix.lower() in INGEST_EXTS:
            if order_id and not fp.name.startswith(order_id):
                continue
            files.append(fp)

    if not files:
        logger.info("[SwitchDrive] No new files to push.")
        return []

    # Ensure remote dir exists (MKCOL)
    _ensure_remote_dir(remote_dir)

    uploaded = []
    for fp in files:
        remote_path = f"{remote_dir}/{fp.name}"
        _put_file(str(fp), remote_path)
        uploaded.append(remote_path)
        logger.info(f"[SwitchDrive] pushed {fp.name} → {remote_path}")

    logger.info(f"[SwitchDrive] {len(uploaded)} file(s) pushed to {remote_dir}")
    return uploaded


def _ensure_remote_dir(remote_dir: str) -> None:
    """Create remote directory (and parents) via MKCOL if it doesn't exist."""
    base = config.SWITCHDRIVE_URL.rstrip("/") + "/"
    parts = remote_dir.strip("/").split("/")
    # skip the webdav root segment
    for i in range(1, len(parts) + 1):
        path = "/".join(parts[:i])
        r = requests.request(
            "MKCOL",
            base + path,
            auth=HTTPBasicAuth(config.SWITCHDRIVE_USER, config.SWITCHDRIVE_PASS),
            timeout=15,
        )
        # 405 = already exists (ok), 201 = created (ok), 207 = parent now has this child
        if r.status_code not in (201, 207, 405):
            logger.debug(f"MKCOL {path} → {r.status_code}")


def _put_file(local_path: str, remote_path: str) -> requests.Response:
    """PUT a local file to a remote WebDAV path."""
    base = config.SWITCHDRIVE_URL.rstrip("/") + "/"
    url = base + remote_path.lstrip("/")
    with open(local_path, "rb") as f:
        return requests.put(
            url,
            data=f,
            auth=HTTPBasicAuth(config.SWITCHDRIVE_USER, config.SWITCHDRIVE_PASS),
            timeout=60,
        )
