"""
utils/switchdrive.py — pull image/PDF folders from SwitchDrive via WebDAV.

Auth: SwitchDrive username + an **app password** (drive.switch.ch → Settings →
Security → create app password), set in .env.gpustack as SWITCHDRIVE_USER /
SWITCHDRIVE_PASS. The folder to ingest lives under your SwitchDrive root
(SWITCHDRIVE_REMOTE_DIR, or passed per-call).

Used by the bot's /pull command to bring a whole folder of source images into the
local hot folder for processing.
"""

from pathlib import Path
from typing import Optional

from loguru import logger

import config

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
        local_dir:  destination (defaults to config.HOT_FOLDER).
        recursive:  also descend into subfolders.

    Returns the list of downloaded local paths.
    """
    if not is_configured():
        raise RuntimeError(
            "SwitchDrive not configured — set SWITCHDRIVE_USER and SWITCHDRIVE_PASS "
            "(app password) in .env.gpustack."
        )
    remote_dir = (remote_dir or config.SWITCHDRIVE_REMOTE_DIR).strip("/")
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
                dest = local_dir / Path(name).name
                client.download_file(name, str(dest))
                downloaded.append(dest)
                logger.info(f"[SwitchDrive] pulled {name} → {dest}")

    logger.info(f"[SwitchDrive] pulling '{remote_dir}' (recursive={recursive})")
    _walk(remote_dir)
    logger.info(f"[SwitchDrive] {len(downloaded)} file(s) downloaded")
    return downloaded
