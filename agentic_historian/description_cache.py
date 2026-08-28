"""
description_cache.py — Agent B's description, keyed by what it described (#387).

Every run re-described the document from scratch. Besides the cost, each
re-description was a fresh chance to disagree with the last: the same manuscript
came back as "Kursive", then "Fraktur", then "Gothische Textura" across three runs
of identical images, and pass-2 model selection followed whatever the latest run
said (#379). Three script families, three different winning model pools, and no
downstream change measurable against that noise.

Keying on the IMAGE BYTES makes the description a property of the source rather
than of the run. Two runs of the same pages then produce the same criteria, which
is the precondition for any before/after comparison — independent of whether the
description is *right*.

That trade is worth naming: a cached description is a cached MISTAKE too. This is
why invalidation is narrow and explicit — a Gate-1 correction or an explicit
reprocess clears the entry, nothing else does — and why #388 lets a human
correction outrank the cache entirely rather than merely refreshing it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

import config


def _dir() -> Path:
    return config.DATA_DIR / "descriptions"


def content_key(paths: Iterable) -> str:
    """A stable key for a set of page images: hash of the sorted per-file hashes.

    Sorted, so page order in the caller cannot change the key; per-file, so adding
    a page changes it. Returns "" when nothing is readable — an unkeyable input
    must miss the cache rather than collide with another document.
    """
    digests = []
    for p in sorted(str(x) for x in (paths or [])):
        try:
            digests.append(hashlib.sha256(Path(p).read_bytes()).hexdigest())
        except OSError as e:
            logger.warning(f"[desc-cache] unreadable page {p}: {e}")
            return ""
    if not digests:
        return ""
    return hashlib.sha256("".join(sorted(digests)).encode("utf-8")).hexdigest()[:32]


def load(key: str) -> Optional[dict]:
    """The stored description for *key*, or None. A corrupt entry is a miss."""
    if not key or not getattr(config, "AGENT_B_CACHE", True):
        return None
    path = _dir() / f"{key}.json"
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning(f"[desc-cache] unreadable entry {key}: {e}")
        return None


def store(key: str, description: dict) -> None:
    """Best-effort write. A failed cache write must never fail the run."""
    if not key or not description or not getattr(config, "AGENT_B_CACHE", True):
        return
    try:
        _dir().mkdir(parents=True, exist_ok=True)
        tmp = _dir() / f"{key}.json.tmp"
        tmp.write_text(json.dumps(description, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(_dir() / f"{key}.json")
    except OSError as e:
        logger.warning(f"[desc-cache] could not store {key}: {e}")


def invalidate(key: str) -> bool:
    """Drop one entry. True when something was removed."""
    if not key:
        return False
    try:
        path = _dir() / f"{key}.json"
        if path.exists():
            path.unlink()
            logger.info(f"[desc-cache] invalidated {key}")
            return True
    except OSError as e:
        logger.warning(f"[desc-cache] could not invalidate {key}: {e}")
    return False
