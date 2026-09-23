"""
passage_index.py — Q1: passage store + query API (#395, passage-retrieval epic).

Persistent SQLite index of entity-bearing passages extracted during Agent C.
Each record: doc_id, page (always 1 for now), chunk_idx, char_start, char_end,
entity_type, text, normalised, context, hub_id, gnd, hls, controlled_vocab,
hub_confidence, link_method, created_at.

Q2 will embed these passages; Q3 will expose /find over them.

Schema: CREATE TABLE passages (...); CREATE INDEX on (doc_id), (entity_type),
(normalised), (hub_id), (created_at).
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

import config

_DB: Optional[sqlite3.Connection] = None


def _db_path() -> Path:
    return config.DATA_DIR / "passages.db"


def _get_db() -> sqlite3.Connection:
    """Lazy open + init."""
    global _DB
    if _DB is None:
        db_path = _db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _DB = sqlite3.connect(str(db_path), check_same_thread=False)
        _DB.execute("PRAGMA journal_mode=WAL")
        _init_schema(_DB)
    return _DB


def _init_schema(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS passages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id          TEXT    NOT NULL,
            page            INTEGER NOT NULL DEFAULT 1,
            chunk_idx       INTEGER NOT NULL DEFAULT 0,
            char_start      INTEGER NOT NULL,
            char_end        INTEGER NOT NULL,
            entity_type     TEXT    NOT NULL,
            text            TEXT    NOT NULL,
            normalised      TEXT    NOT NULL,
            context         TEXT,
            hub_id          TEXT,
            gnd             TEXT,
            hls             TEXT,
            wikidata        TEXT,
            controlled_vocab TEXT,
            hub_confidence  TEXT,
            link_method     TEXT,
            created_at      TEXT    NOT NULL,
            content_hash    TEXT    NOT NULL,
            UNIQUE(doc_id, char_start, char_end, normalised, entity_type)
        );
        CREATE INDEX IF NOT EXISTS ix_passages_doc_id       ON passages(doc_id);
        CREATE INDEX IF NOT EXISTS ix_passages_entity_type  ON passages(entity_type);
        CREATE INDEX IF NOT EXISTS ix_passages_normalised   ON passages(normalised);
        CREATE INDEX IF NOT EXISTS ix_passages_hub_id       ON passages(hub_id);
        CREATE INDEX IF NOT EXISTS ix_passages_created_at   ON passages(created_at);
        CREATE INDEX IF NOT EXISTS ix_passages_content_hash ON passages(content_hash);
    """)
    db.commit()


def _content_hash(doc_id: str, char_start: int, char_end: int, text: str) -> str:
    return hashlib.sha1(f"{doc_id}:{char_start}:{char_end}:{text}".encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def upsert_passages(passages: list[dict]) -> int:
    """Insert or replace passage records. Returns row count.

    Idempotent: UNIQUE constraint on (doc_id, char_start, char_end, normalised,
    entity_type) means a re-run of Agent C on the same doc replaces the record.
    """
    if not passages:
        return 0
    db = _get_db()
    now = _now()
    rows = 0
    for p in passages:
        ch = _content_hash(
            p["doc_id"], p["char_start"], p["char_end"], p["text"]
        )
        try:
            db.execute("""
                INSERT OR REPLACE INTO passages
                (doc_id, page, chunk_idx, char_start, char_end, entity_type,
                 text, normalised, context, hub_id, gnd, hls, wikidata,
                 controlled_vocab, hub_confidence, link_method, created_at,
                 content_hash)
                VALUES
                (:doc_id, :page, :chunk_idx, :char_start, :char_end,
                 :entity_type, :text, :normalised, :context, :hub_id,
                 :gnd, :hls, :wikidata, :controlled_vocab, :hub_confidence,
                 :link_method, :created_at, :content_hash)
            """, {
                "doc_id":         p["doc_id"],
                "page":           p.get("page", 1),
                "chunk_idx":      p.get("chunk_idx", 0),
                "char_start":     p["char_start"],
                "char_end":       p["char_end"],
                "entity_type":    p["entity_type"],
                "text":           p["text"],
                "normalised":     p["normalised"],
                "context":        p.get("context", ""),
                "hub_id":         p.get("hub_id", ""),
                "gnd":            p.get("gnd", ""),
                "hls":            p.get("hls", ""),
                "wikidata":       p.get("wikidata", ""),
                "controlled_vocab": p.get("controlled_vocab", ""),
                "hub_confidence": p.get("hub_confidence", ""),
                "link_method":    p.get("link_method", ""),
                "created_at":     now,
                "content_hash":   ch,
            })
            rows += 1
        except Exception as e:
            logger.warning(f"[passage_index] upsert error: {e}")
    db.commit()
    logger.info(f"[passage_index] upserted {rows} passage(s)")
    return rows


def query_passages(
    entity_type: Optional[str] = None,
    normalised: Optional[str] = None,
    hub_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Query the passage index by any combination of filters.

    Returns list[dict] sorted by created_at desc.
    """
    db = _get_db()
    where_parts = []
    params: dict = {}
    if entity_type:
        where_parts.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if normalised:
        where_parts.append("normalised = :normalised")
        params["normalised"] = normalised
    if hub_id:
        where_parts.append("hub_id = :hub_id")
        params["hub_id"] = hub_id
    if doc_id:
        where_parts.append("doc_id = :doc_id")
        params["doc_id"] = doc_id
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    params["limit"] = limit
    sql = ("SELECT id, doc_id, page, chunk_idx, char_start, char_end,"
           " entity_type, text, normalised, context, hub_id, gnd, hls,"
           " wikidata, controlled_vocab, hub_confidence, link_method,"
           " created_at FROM passages " + where_sql +
           " ORDER BY created_at DESC LIMIT :limit")
    rows = db.execute(sql, params).fetchall()
    cols = [
        "id", "doc_id", "page", "chunk_idx", "char_start", "char_end",
        "entity_type", "text", "normalised", "context", "hub_id", "gnd",
        "hls", "wikidata", "controlled_vocab", "hub_confidence",
        "link_method", "created_at",
    ]
    return [dict(zip(cols, r)) for r in rows]


def passage_count() -> int:
    """Total passage count."""
    db = _get_db()
    row = db.execute("SELECT COUNT(*) FROM passages").fetchone()
    return row[0] if row else 0


def reset_doc(doc_id: str) -> int:
    """Delete all passages for one doc (for re-runs). Returns count deleted."""
    db = _get_db()
    cur = db.execute(
        "DELETE FROM passages WHERE doc_id = ?", (doc_id,)
    )
    db.commit()
    logger.info(f"[passage_index] reset {doc_id}: {cur.rowcount} deleted")
    return cur.rowcount
