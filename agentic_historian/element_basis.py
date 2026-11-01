"""What an element of a source description rests on (#772).

Shared by Agent B, which stamps it, and by the publishers, which print it, so a
page that says "Schreiber: eine Hand" also says from how many pages of the
volume that was seen.
"""

from __future__ import annotations

from typing import Optional

#: Elements read off the page image, not out of the text.
IMAGE_ELEMENTS = ("Beschreibstoff", "Blaetter", "Format", "Lagen", "Schriftraum_Gliederung",
                  "Schrift", "Schreiber", "Ausstattung", "Einband")
#: Elements that are claims about the whole volume, which one page cannot carry.
VOLUME_ELEMENTS = ("Schreiber", "Blaetter", "Lagen", "Einband")


#: At most this many page names in the line; beyond that, the count says it.
_NAMES_SHOWN = 8


def basis_line(source_json: Optional[dict]) -> str:
    """One sentence for a reader: how many pages the description was made from,
    and which, when it was more than one (#771)."""
    if not isinstance(source_json, dict):
        return ""
    for elem in IMAGE_ELEMENTS:
        el = source_json.get(elem)
        basis = el.get("basis") if isinstance(el, dict) else None
        if isinstance(basis, dict) and "seiten" in basis:
            seen = list(basis["seiten"])
            n, of = len(seen), basis.get("von")
            line = f"Beschreibung aus {n} von {of if of else '?'} Seite(n)"
            if n > 1:
                names = ", ".join(seen[:_NAMES_SHOWN]) + (", …" if n > _NAMES_SHOWN else "")
                line += f": {names}"
            return line
    return ""
