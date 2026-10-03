"""
passage_embeddings.py — semantic retrieval over passages (#396, Q2).

Keyword search over 0.22-CER transcriptions misses what it should find: the
scribe's orthography and the recogniser's errors defeat exact strings twice
over, and a historian searching for `almosen` does not find `almuosen`,
`almuesen` or `alnmsen`. Embeddings are robust to both because they compare
meaning rather than characters.

`semantic.py` already embeds documents, but on the fly and never persisted, and
a whole document is the wrong unit: a 2400-character page that mentions alms
once is mostly about something else. Q1 made passages addressable (#395, #466,
#467); this embeds them.

Three properties worth stating, because each is a decision:

**A vector is keyed by its text *and* its model.** The issue asks for a content
hash so a re-run does not re-embed unchanged text, which is right — but the same
text under a different embedding model is a different vector, and serving one for
the other would compare numbers from two different spaces and return a plausible
ranking. The key is therefore `(content_hash, model)`, and switching models
re-embeds rather than silently mixing.

**A passage says which text was embedded.** A passage row carries a short `text`
(a word or two) and a `context` window that may be empty. Embedding two words is
legal and weak; embedding the window is what makes the live check possible at
all. `basis` records which was used, so a thin passage is visible instead of
quietly ranking badly.

**The store is faithful to the model.** Vectors are kept as the model returned
them and normalised at compare time, rather than stored pre-normalised. A store
that holds something the model never produced cannot be checked against it.

Not here: `bestand` and date-range filters. #396 calls them "the Q1 dimensions",
but the #395 schema has neither column — see `FILTERABLE`.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

import numpy as np
from loguru import logger

import config
import passage_index

__all__ = [
    "BATCH",
    "FILTERABLE",
    "Embedded",
    "Hit",
    "content_hash",
    "embed_passages",
    "passage_text",
    "search",
    "store_vector",
    "vector_count",
    "vector_for",
]

#: Texts per embedding call. The API takes a list, and one call per passage over
#: a bestand of several thousand would be several thousand round trips for work
#: the model does in batches anyway (#396).
BATCH = 64

#: The passage columns a search can filter on today — `entity_type` and the
#: document, plus a `doc_id` prefix, which is how a holding is actually selected
#: when doc_ids carry one.
#:
#: #396 asks for "the Q1 dimensions (type, bestand, date range)". Only the first
#: exists: the #395 schema has no `bestand` column and no date at all, so those
#: two filters cannot be honoured without a migration, and inventing them from a
#: doc_id's shape would be a convention this store does not enforce. Named here
#: rather than silently dropped.
FILTERABLE = ("entity_type", "doc_id", "doc_prefix")


class Embedded:
    """What one embedding pass did. Three counts, because they answer different
    questions: whether the cache is working, whether anything was sent, and
    whether anything had nothing to embed."""

    __slots__ = ("cached", "embedded", "skipped")

    def __init__(self, cached: int = 0, embedded: int = 0, skipped: int = 0):
        self.cached, self.embedded, self.skipped = cached, embedded, skipped

    @property
    def total(self) -> int:
        return self.cached + self.embedded + self.skipped

    def __repr__(self) -> str:
        return (f"Embedded(cached={self.cached}, embedded={self.embedded}, "
                f"skipped={self.skipped})")

    def __eq__(self, other) -> bool:
        return (isinstance(other, Embedded)
                and (self.cached, self.embedded, self.skipped)
                == (other.cached, other.embedded, other.skipped))


class Hit:
    """One retrieved passage: the row, its score, and where the score came from."""

    __slots__ = ("passage", "score", "scored_by")

    def __init__(self, passage: dict, score: float, scored_by: str = "cosine"):
        self.passage, self.score, self.scored_by = passage, score, scored_by

    def __repr__(self) -> str:
        return (f"Hit({self.passage.get('normalised')!r}, "
                f"{self.score:.3f}, {self.scored_by})")


# ── the text that gets embedded ──────────────────────────────────────────────

def passage_text(row: dict) -> tuple[str, str]:
    """``(text to embed, which field it came from)``.

    The context window where there is one: a passage's own `text` is a word or
    two, and two words embed to something that will not surface for a
    natural-language query sharing none of their characters — which is exactly
    what #396's live check asks for. The term is prepended so the thing the
    passage is *about* is in the vector even when the window is mostly
    surrounding prose.

    Falls back to the term alone, and says so, rather than returning nothing: a
    passage with no window is still findable, just weakly, and `basis` is what
    makes that visible instead of a mystery about why it never ranks.
    """
    term = (row.get("normalised") or row.get("text") or "").strip()
    context = (row.get("context") or "").strip()
    if context:
        return (f"{term} — {context}" if term else context), "context"
    return term, "text"


def content_hash(text: str) -> str:
    """A stable key for a piece of text. Not the passage's id: two passages with
    the same window are one embedding, and a passage whose window changed is a
    different one."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── the store ────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    """The passage index's own connection, with the vector table ensured.

    Same database as the passages, not a sidecar: a vector whose passage was
    deleted is garbage, and keeping both in one file means one thing to copy,
    back up and point at a test path. `passage_index` already owns the
    connection and the test seam, so this borrows rather than opening a second
    handle to the same file.
    """
    db = passage_index._get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS passage_vectors (
            content_hash TEXT    NOT NULL,
            model        TEXT    NOT NULL,
            dim          INTEGER NOT NULL,
            vector       BLOB    NOT NULL,
            created_at   TEXT    NOT NULL,
            PRIMARY KEY (content_hash, model)
        );
    """)
    db.commit()
    return db


def _model() -> str:
    return config.GPUSTACK_MODEL_EMBEDDING


def store_vector(text_hash: str, vector: Iterable[float],
                 model: Optional[str] = None) -> None:
    """Persist one vector as the model returned it, in float32.

    float32 rather than float64: the models emit single precision and the store
    is a cache, so doubling the bytes would buy digits the embedder never had.
    `dim` is kept beside the blob so a truncated read is an error rather than a
    silently shorter vector.
    """
    arr = np.asarray(list(vector), dtype=np.float32)
    db = _db()
    db.execute(
        "INSERT OR REPLACE INTO passage_vectors"
        " (content_hash, model, dim, vector, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (text_hash, model or _model(), int(arr.size), arr.tobytes(),
         datetime.now(timezone.utc).isoformat()),
    )
    db.commit()


def vector_for(text_hash: str, model: Optional[str] = None):
    """The stored vector, or None. Raises nothing on a short blob — it reports
    the mismatch and treats the entry as absent, because a half-read vector
    ranks confidently and wrongly."""
    row = _db().execute(
        "SELECT dim, vector FROM passage_vectors"
        " WHERE content_hash = ? AND model = ?",
        (text_hash, model or _model()),
    ).fetchone()
    if not row:
        return None
    dim, blob = row
    arr = np.frombuffer(blob, dtype=np.float32)
    if arr.size != dim:
        logger.warning(f"[passage_embeddings] vector {text_hash[:8]} is "
                       f"{arr.size} long, {dim} recorded — treated as absent")
        return None
    return arr


def vector_count(model: Optional[str] = None) -> int:
    """How many vectors this model has in the store."""
    row = _db().execute(
        "SELECT COUNT(*) FROM passage_vectors WHERE model = ?",
        (model or _model(),),
    ).fetchone()
    return row[0] if row else 0


# ── embedding a batch ────────────────────────────────────────────────────────

def embed_passages(rows: Optional[list[dict]] = None, *,
                   doc_id: Optional[str] = None,
                   embedder: Optional[Callable] = None,
                   batch: int = BATCH,
                   model: Optional[str] = None) -> Embedded:
    """Embed every passage that has no vector yet. Returns the three counts.

    `rows` defaults to the whole index, or one document's with `doc_id`.

    Only the misses are sent, and they are sent in batches: the cache is checked
    first, the distinct texts are collected, and one call carries `batch` of
    them. Distinct, because a term repeated across a holding has one window per
    occurrence but often the same window text, and embedding it twenty times
    would be twenty times the cost for one vector.

    `embedder` is the seam. Defaults to the GPUStack client; tests pass a
    deterministic fake, which is what lets any of this be checked without a
    network.
    """
    model = model or _model()
    if rows is None:
        rows = passage_index.query_passages(doc_id=doc_id, limit=1_000_000)

    out = Embedded()
    pending: dict[str, str] = {}            # hash → text
    for row in rows:
        text, _basis = passage_text(row)
        if not text:
            out.skipped += 1
            continue
        key = content_hash(text)
        if key in pending:
            continue                        # already queued this exact text
        if vector_for(key, model) is not None:
            out.cached += 1
            continue
        pending[key] = text

    if not pending:
        return out

    if embedder is None:
        from utils import gpustack_client as gs
        embedder = gs.embed

    keys = list(pending)
    for start in range(0, len(keys), max(1, batch)):
        chunk = keys[start:start + max(1, batch)]
        try:
            vectors = embedder([pending[k] for k in chunk])
        except Exception as e:              # noqa: BLE001 — a batch is not the run
            logger.warning(f"[passage_embeddings] batch of {len(chunk)} failed: "
                           f"{e}")
            continue
        if len(vectors) != len(chunk):
            # The one way a batch can corrupt the store: vectors returned in a
            # different count cannot be matched to their texts by position, and
            # pairing them anyway would attach meanings to the wrong passages.
            logger.warning(f"[passage_embeddings] asked for {len(chunk)} "
                           f"vectors, got {len(vectors)} — batch dropped")
            continue
        for key, vec in zip(chunk, vectors):
            store_vector(key, vec, model)
            out.embedded += 1
    return out


# ── searching ────────────────────────────────────────────────────────────────

def _unit(arr) -> Optional[np.ndarray]:
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return None
    return np.asarray(arr, dtype=np.float32) / norm


def _filtered(entity_type: Optional[str], doc_id: Optional[str],
              doc_prefix: Optional[str]) -> list[dict]:
    rows = passage_index.query_passages(entity_type=entity_type, doc_id=doc_id,
                                        limit=1_000_000)
    if doc_prefix:
        rows = [r for r in rows if str(r.get("doc_id", "")).startswith(doc_prefix)]
    return rows


def search(query: str, *, top_k: int = 10,
           entity_type: Optional[str] = None,
           doc_id: Optional[str] = None,
           doc_prefix: Optional[str] = None,
           rerank: bool = False,
           embedder: Optional[Callable] = None,
           reranker: Optional[Callable] = None,
           model: Optional[str] = None) -> list[Hit]:
    """Passages most like `query`, filtered first and ranked second.

    Filtered first on purpose: the filters are exact and cheap, the cosine is
    neither, and a historian asking for care actions in one holding should not
    pay for the rest of the corpus. It also means `top_k` counts rows that
    passed the filter rather than rows that happened to rank well before it.

    A passage with no stored vector is **left out and counted**, not ranked at
    zero: zero is a similarity, and a row that was never embedded has none. Run
    `embed_passages` first.

    `rerank=True` sends the shortlist through the cross-encoder, which reads
    query and passage together and is better at it than a cosine over two
    independent vectors. It degrades to the cosine order on any failure, and the
    hits say which ranking they carry — a reranked list and a cosine list are
    not the same evidence.
    """
    model = model or _model()
    rows = _filtered(entity_type, doc_id, doc_prefix)
    if not rows or not (query or "").strip():
        return []

    if embedder is None:
        from utils import gpustack_client as gs
        embedder = gs.embed
    try:
        q_unit = _unit(np.asarray(embedder([query])[0], dtype=np.float32))
    except Exception as e:                  # noqa: BLE001 — a query is not the run
        logger.warning(f"[passage_embeddings] query embedding failed: {e}")
        return []
    if q_unit is None:
        return []

    scored: list[Hit] = []
    missing = 0
    for row in rows:
        text, _basis = passage_text(row)
        if not text:
            missing += 1
            continue
        vec = vector_for(content_hash(text), model)
        if vec is None:
            missing += 1
            continue
        unit = _unit(vec)
        if unit is None:
            missing += 1
            continue
        scored.append(Hit(row, float(np.dot(q_unit, unit))))

    if missing:
        logger.info(f"[passage_embeddings] {missing} of {len(rows)} passage(s) "
                    f"have no vector for {model} — not ranked; run "
                    f"embed_passages")
    scored.sort(key=lambda h: h.score, reverse=True)
    shortlist = scored[:max(0, top_k)]

    if rerank and shortlist:
        shortlist = _reranked(query, shortlist, reranker)
    return shortlist


def _reranked(query: str, shortlist: list[Hit],
              reranker: Optional[Callable]) -> list[Hit]:
    """The shortlist in the cross-encoder's order, or unchanged on failure."""
    if reranker is None:
        from utils import gpustack_client as gs
        reranker = gs.rerank
    docs = [passage_text(h.passage)[0] for h in shortlist]
    try:
        results = reranker(query, docs, top_n=len(docs))
    except Exception as e:                  # noqa: BLE001 — see docstring
        logger.warning(f"[passage_embeddings] rerank failed: {e}")
        return shortlist
    out: list[Hit] = []
    for r in results or []:
        idx = r.get("index")
        if idx is None or not (0 <= idx < len(shortlist)):
            continue
        out.append(Hit(shortlist[idx].passage,
                       float(r.get("score", 0.0)), "rerank"))
    return out or shortlist
