"""restricted.py — material the project may hold but must not read or pass on (#481).

The share holds what archives delivered, and not everything delivered may be
used the same way. On 25.03.2025 the Staatsarchiv Basel-Stadt sent
``PA 82a G 1`` as a *Reproduktion zur privaten Verwendung*: reproduction,
publication, passing on to third parties and alteration are not allowed; it is
a private archive with its own terms (the order letter, read 17.11.2026, #481).
Since #476 the batch runner reads PDFs page by page, so the next run over the
Basel folder would have transcribed it.

This module is the one register of such material and the one test against it.
Every path that takes from the share or gives to the outside asks
:func:`restriction`: the batch listing (``atr_batch``), the publisher
(``utils.publish_github.collect_tree``), the Transkribus upload and the
HuggingFace export. A restricted file is **skipped and said so** — with the
entry's reason — never dropped in silence, because a silent omission and a lost
folder look the same from outside.

An entry names its source document. A restriction nobody can trace back to a
letter or a rule is a rumour, and a rumour cannot be lifted when the archive
answers.

Host-local additions, without a commit: ``AH_RESTRICTED_FILE`` names a file
with one regular expression per line, optionally followed by ``  # reason``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePath
from typing import Optional, Union

__all__ = ["Restriction", "REGISTER", "restriction", "normalise", "reload"]


@dataclass(frozen=True)
class Restriction:
    pattern: str          # regular expression, searched in the normalised path
    reason: str
    source: str           # the document that says so
    since: str            # when it was recorded here

    def matches(self, normalised: str) -> bool:
        return re.search(self.pattern, normalised, flags=re.IGNORECASE) is not None

    def describe(self) -> str:
        return f"restricted: {self.reason} ({self.source})"


#: The register. Patterns are searched in the path as :func:`normalise` writes
#: it: forward slashes, a page key's ``__`` folded back to ``/``, underscores
#: inside a shelfmark kept. Keep each pattern as narrow as the document is.
REGISTER: tuple[Restriction, ...] = (
    Restriction(
        # The shelfmark as it is written on the share and in page keys —
        # "PA 82a G 1_003.pdf", "PA_82a_G_1" — but never "PA 82a G 10" or
        # "PA 82a G 12" (the digit guard).
        pattern=r"PA[ _]82a[ _]G[ _]1(?![0-9])",
        reason="private use only — no reproduction, publication, passing on or alteration; "
               "private archive with its own terms",
        source="StABS, Reprobestellung Schonhardt 25.03.2025 (Auslieferung 26.03.2025)",
        since="2026-11-18",
    ),
    Restriction(
        # The letter folder that holds it, in the Basel holding only: another
        # holding may number a letter 1173 too.
        pattern=r"(^|/)Basel/lassberg-letter-1173(/|$)",
        reason="the folder of PA 82a G 1 — private use only",
        source="StABS, Reprobestellung Schonhardt 25.03.2025 (Auslieferung 26.03.2025)",
        since="2026-11-18",
    ),
)


def normalise(path: Union[str, PurePath]) -> str:
    """One spelling for a share path, a relative path and a page key:
    ``Basel__lassberg-letter-1173__PA 82a G 1_003`` and
    ``Basel/lassberg-letter-1173/PA 82a G 1_003.pdf`` both become
    ``Basel/lassberg-letter-1173/PA 82a G 1_003…``."""
    s = str(path).replace("\\", "/")
    return s.replace("__", "/")


def _local_entries(path: Optional[str]) -> tuple[Restriction, ...]:
    if not path:
        return ()
    p = Path(path)
    if not p.is_file():
        return ()
    out = []
    for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        body, _, reason = line.partition("#")
        body = body.strip()
        if not body:
            continue
        try:
            re.compile(body)
        except re.error as exc:              # a broken pattern fails loudly, never open
            raise ValueError(f"{p}:{i}: not a regular expression: {exc}") from exc
        out.append(Restriction(body, reason.strip() or "host-local restriction",
                               f"{p}:{i}", ""))
    return tuple(out)


@lru_cache(maxsize=1)
def _entries(local: Optional[str]) -> tuple[Restriction, ...]:
    return REGISTER + _local_entries(local)


def reload() -> None:
    """Forget the cached host-local file (tests, or after editing it)."""
    _entries.cache_clear()


def restriction(path: Union[str, PurePath]) -> Optional[Restriction]:
    """The entry that forbids ``path``, or None. ``path`` may be a share path,
    a path relative to the corpus root, or a page key."""
    n = normalise(path)
    for entry in _entries(os.environ.get("AH_RESTRICTED_FILE") or None):
        if entry.matches(n):
            return entry
    return None
