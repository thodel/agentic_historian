"""
passage_find.py — Q3: reaching the passage index where the historians work (#397).

Q1 made passages addressable and Q2 made them searchable by meaning. Both are
useless if the only way in is a Python prompt on tei. This is the query path as
a pure function plus a renderer, so the Discord command, the NL orchestrator and
any later surface all ask the same question and get the same answer.

Two things in here are contracts rather than conveniences.

**The anchor.** `/find` has to deep-link into the catalogue, and the catalogue
build lives in another repository. If each side computed its own anchor they
would agree until the day they did not, and the symptom would be a link that
scrolls to the top of a long page — which looks like a working link. So the
anchor is defined once, here, and the build adopts this function's output.

**The scope of an empty result.** #397 asks that "nichts gefunden" state what
was searched. A historian who filtered to a holding and got nothing needs to
know whether the holding is empty, the index is empty, or nothing was embedded
yet — those want three different next actions, and silence suggests the corpus
has no such passages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from loguru import logger

import config
import passage_embeddings as pe

__all__ = [
    "ANCHOR_PREFIX",
    "Found",
    "PER_PAGE",
    "anchor_for",
    "catalogue_base",
    "catalogue_url",
    "find",
    "format_found",
]

#: Hits per message. Five context windows plus their links is already a long
#: Discord message; more and the useful part scrolls away.
PER_PAGE = 5

#: The anchor contract, shared with the catalogue build (#397).
#:
#: ``passage-<char_start>-<char_end>``. Two entities on the same span — a role
#: and a social group over the same words — collapse to one anchor, which is
#: right: the anchor names a *place in the text*, and that is what a reader
#: following a link wants to see. It is not an entity id.
ANCHOR_PREFIX = "passage-"


def anchor_for(row: dict) -> Optional[str]:
    """The in-page anchor for one passage, or None without offsets.

    None rather than a guess: a passage with no verbatim position (#466) has no
    place in the text to point at, and an anchor that resolves to nothing is a
    link that silently lands at the top of the page. The caller renders the
    document link without a fragment instead.
    """
    start, end = row.get("char_start"), row.get("char_end")
    if start is None or end is None:
        return None
    return f"{ANCHOR_PREFIX}{start}-{end}"


def catalogue_base(base: Optional[str] = None) -> str:
    """Where the published catalogue lives, without a trailing slash.

    Derived from ``GITHUB_OUTPUT_REPO`` by the GitHub Pages convention, and
    overridable with ``CATALOGUE_BASE_URL`` because a custom domain breaks that
    convention and a wrong base makes every link wrong at once.
    """
    explicit = (base or getattr(config, "CATALOGUE_BASE_URL", "") or "").rstrip("/")
    if explicit:
        return explicit
    repo = (getattr(config, "GITHUB_OUTPUT_REPO", "") or "").strip("/")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}"


def catalogue_url(doc_id: str, *, anchor: Optional[str] = None,
                  base: Optional[str] = None) -> Optional[str]:
    """The catalogue page for a document, with an optional anchor.

    None when no base is configured — a relative link in a Discord message is
    not a link.

    Whether that page *exists* is not knowable from the passage index: it says
    what was recognised, not what was published. `find` takes an optional
    ``published`` set for a caller that knows, and `format_found` says plainly
    that the links are unverified when it does not.
    """
    root = catalogue_base(base)
    if not root or not doc_id:
        return None
    url = f"{root}/{doc_id}/"
    return f"{url}#{anchor}" if anchor else url


# ── the result ───────────────────────────────────────────────────────────────

@dataclass
class Found:
    """One search, with enough about itself to explain an empty answer."""

    query: str
    hits: list = field(default_factory=list)
    entity_type: Optional[str] = None
    bestand: Optional[str] = None
    page: int = 1
    per_page: int = PER_PAGE
    #: Passages that passed the filters. Apart from `len(hits)` because a
    #: filter that matched rows none of which are embedded is a different
    #: situation from a filter that matched nothing, and they want different
    #: next actions (#397's empty-result wording).
    candidates: int = 0
    #: Rows in the index at all, ignoring every filter.
    indexed: int = 0

    @property
    def pages(self) -> int:
        if not self.hits:
            return 0
        return (len(self.hits) + self.per_page - 1) // self.per_page

    @property
    def on_page(self) -> list:
        start = (max(1, self.page) - 1) * self.per_page
        return self.hits[start:start + self.per_page]

    def scope(self) -> str:
        """What was searched, in words, for the message that found nothing."""
        parts = []
        if self.entity_type:
            parts.append(f"Typ `{self.entity_type}`")
        if self.bestand:
            parts.append(f"Bestand `{self.bestand}`")
        return ", ".join(parts) if parts else "das ganze Korpus"

    def why_empty(self) -> str:
        """The reason there is nothing, as far as it can be established.

        Three situations that look identical as silence and want three
        different next actions: an index nobody has filled, a filter that
        selected no passages, and passages that exist but were never embedded.
        """
        if not self.indexed:
            return ("Der Passagen-Index ist leer — es wurde noch kein Dokument "
                    "indiziert (Agent C schreibt ihn, #467).")
        if not self.candidates:
            return (f"Im Index liegen {self.indexed} Passage(n), aber keine in "
                    f"{self.scope()}.")
        return (f"{self.candidates} Passage(n) in {self.scope()} haben noch "
                f"keinen Vektor — `passage_embeddings.embed_passages()` "
                f"ausführen (#396).")


# ── the search ───────────────────────────────────────────────────────────────

def find(query: str, *, entity_type: Optional[str] = None,
         bestand: Optional[str] = None, top_k: int = 25,
         page: int = 1, per_page: int = PER_PAGE,
         rerank: bool = False,
         embedder: Optional[Callable] = None,
         reranker: Optional[Callable] = None) -> Found:
    """Ranked passages for a natural-language query.

    Never raises: a surface that answers a historian must not hand them a
    traceback, and every way of coming back empty is a sentence in
    `why_empty`.
    """
    import passage_index

    found = Found(query=query, entity_type=entity_type, bestand=bestand,
                  page=max(1, page), per_page=max(1, per_page))
    try:
        found.indexed = passage_index.passage_count()
        found.candidates = len(passage_index.query_passages(
            entity_type=entity_type, bestand=bestand, limit=1_000_000))
        found.hits = pe.search(query, top_k=top_k, entity_type=entity_type,
                               bestand=bestand, rerank=rerank,
                               embedder=embedder, reranker=reranker)
    except Exception as e:                  # noqa: BLE001 — see docstring
        logger.warning(f"[find] {query!r} failed: {e}")
    return found


# ── rendering ────────────────────────────────────────────────────────────────

def _window(row: dict, limit: int = 240) -> str:
    text = (row.get("context") or row.get("text") or "").strip()
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _ids(row: dict) -> str:
    """The authority ids a passage carries, or nothing. Named so a reader can
    tell "no link was found" from "this surface does not show links"."""
    pairs = [("hub", row.get("hub_id")), ("GND", row.get("gnd_id")),
             ("HLS", row.get("hls_id"))]
    named = [f"{label} `{value}`" for label, value in pairs if value]
    return " · ".join(named)


def format_hit(hit, *, base: Optional[str] = None,
               published: Optional[set] = None) -> str:
    """One passage as a Discord line block."""
    row = hit.passage
    doc_id = str(row.get("doc_id", "?"))
    anchor = anchor_for(row)
    url = catalogue_url(doc_id, anchor=anchor, base=base)
    if published is not None and doc_id not in published:
        url = None

    where = f"[{doc_id}]({url})" if url else f"`{doc_id}`"
    if row.get("bestand"):
        where += f" · {row['bestand']}"
    head = (f"**{row.get('normalised') or row.get('text')}** "
            f"({row.get('entity_type', '?')}) — {hit.score:.3f} "
            f"{hit.scored_by}")
    lines = [head, f"> {_window(row)}", where]
    ids = _ids(row)
    if ids:
        lines.append(ids)
    if anchor is None:
        # Said rather than silently linking to the page top (#466).
        lines.append("_ohne Fundstelle im Text — kein Anker_")
    return "\n".join(lines)


def format_found(found: Found, *, base: Optional[str] = None,
                 published: Optional[set] = None) -> list[str]:
    """The result as Discord messages, one per call to send.

    A list rather than one string, following `atr_status.format_gpu_views`:
    Discord caps a message, and a formatter that returns messages keeps the
    splitting testable without a Discord client.
    """
    if not found.hits:
        return [f"**/find** `{found.query}` — nichts gefunden.\n"
                f"{found.why_empty()}"]

    shown = found.on_page
    if not shown:
        return [f"**/find** `{found.query}` — Seite {found.page} von "
                f"{found.pages} ist leer ({len(found.hits)} Treffer)."]

    first = (max(1, found.page) - 1) * found.per_page + 1
    header = (f"**/find** `{found.query}` — {len(found.hits)} Treffer in "
              f"{found.scope()}, {first}–{first + len(shown) - 1} "
              f"(Seite {found.page}/{found.pages})")
    messages = [header]
    for hit in shown:
        messages.append(format_hit(hit, base=base, published=published))
    if published is None and catalogue_base(base):
        messages.append("_Links zeigen in den Katalog; ein nicht "
                        "veröffentlichtes Dokument hat dort keine Seite._")
    return messages
