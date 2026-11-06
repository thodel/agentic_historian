"""
passage_index.py -- Q1a: Speicher fuer Passagen-Datensaetze (#395).

Persistent SQLite index of entity-bearing passages extracted during Agent C.
Each record: doc_id, page, entity_type, text, normalised,
char_start, char_end, context, hub_id, gnd_id, hls_id, created_at.

Q1b (Offsets) and Q1c (Agent C writes) are separate issues.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

import config

_DB: Optional[sqlite3.Connection] = None
_TEST_DBPATH: Path | None = None


def _db_path() -> Path:
    if _TEST_DBPATH is not None:
        return _TEST_DBPATH
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
            page            INTEGER NOT NULL,
            bestand         TEXT,
            entity_type     TEXT    NOT NULL,
            text            TEXT    NOT NULL,
            normalised      TEXT    NOT NULL,
            char_start      INTEGER NOT NULL,
            char_end        INTEGER NOT NULL,
            context         TEXT,
            hub_id          TEXT,
            gnd_id          TEXT,
            hls_id          TEXT,
            created_at      TEXT    NOT NULL,
            UNIQUE(doc_id, char_start, char_end, normalised, entity_type)
        );
        CREATE INDEX IF NOT EXISTS ix_passages_doc_id
            ON passages(doc_id);
        CREATE INDEX IF NOT EXISTS ix_passages_entity_type
            ON passages(entity_type);
        CREATE INDEX IF NOT EXISTS ix_passages_normalised
            ON passages(normalised);
        CREATE INDEX IF NOT EXISTS ix_passages_hub_id
            ON passages(hub_id);
    """)
    # Columns first, then the indexes that need them. An index on an added
    # column cannot live in the script above: on a database that predates the
    # column, `CREATE TABLE IF NOT EXISTS` does nothing and the CREATE INDEX
    # then names a column that is not there yet — `no such column: bestand`,
    # on every connect, for every existing install.
    _add_missing_columns(db)
    db.executescript("""
        CREATE INDEX IF NOT EXISTS ix_passages_bestand
            ON passages(bestand);
    """)
    db.commit()


#: Columns added after the table was first shipped, with their declarations.
#: `CREATE TABLE IF NOT EXISTS` does nothing to a database that already exists,
#: so a column added to the statement above never reaches one — the only
#: symptom being an `OperationalError` on the first query that names it.
_ADDED_COLUMNS = {
    # The holding a passage belongs to (#396 asked to filter by it). Nullable
    # on purpose: NULL means "nobody said which holding", which is the honest
    # reading of every row written before the column existed and of every
    # caller that does not know. It is not "no holding".
    "bestand": "TEXT",
    # In the first schema, and still missing from the database on tei
    # (06.11.2026: `no such column: gnd_id` on the first Agent F run, #812) —
    # whatever built that file, a row without them is a row nobody linked.
    "gnd_id": "TEXT",
    "hls_id": "TEXT",
}

#: Columns an older schema used for the same thing. The database on tei
#: (06.11.2026) has ``gnd`` and ``hls`` beside the new, empty ``gnd_id`` and
#: ``hls_id``; a link it recorded sits under the old name. The migration copies
#: it across where the new column is empty, and a read falls back to it — so a
#: database the CLI cannot write still answers with its links (#812).
_LEGACY_COLUMNS = {"gnd_id": "gnd", "hls_id": "hls"}

#: The columns `query_passages` returns, in order.
_ROW_COLUMNS = (
    "id", "doc_id", "page", "bestand", "entity_type", "text", "normalised",
    "char_start", "char_end", "context",
    "hub_id", "gnd_id", "hls_id", "created_at",
)


def _columns(db: sqlite3.Connection) -> set[str]:
    return {row[1] for row in db.execute("PRAGMA table_info(passages)")}


def _add_missing_columns(db: sqlite3.Connection) -> None:
    """Bring an existing database up to the current column set.

    SQLite's `ALTER TABLE ... ADD COLUMN` is cheap for a nullable column with
    no default — it rewrites no rows — so this runs on every connect rather
    than behind a version counter nobody would remember to bump. Idempotent by
    construction: the columns that are already there are not added again.

    Deliberately only ever **adds**. A migration that could drop or retype a
    column would need the rows read, and this is the wrong place to be clever
    about a corpus somebody spent hours filling.
    """
    have = _columns(db)
    for name, decl in _ADDED_COLUMNS.items():
        if name in have:
            continue
        try:
            db.execute(f"ALTER TABLE passages ADD COLUMN {name} {decl}")
        except sqlite3.OperationalError as e:
            # A reader without write access — the CLI as `dh` on a database
            # the service owns. Reading must still work: `query_passages`
            # reads only the columns that exist (#812).
            logger.warning(f"[passage_index] cannot add column {name} ({e}); "
                           f"rows are read without it")
            continue
        logger.info(f"[passage_index] added column {name} {decl}")
    _copy_legacy_columns(db)


def _copy_legacy_columns(db: sqlite3.Connection) -> None:
    """Fill an empty new column from its older name, once, never overwriting."""
    have = _columns(db)
    for new, old in _LEGACY_COLUMNS.items():
        if new not in have or old not in have:
            continue
        try:
            cur = db.execute(
                f"UPDATE passages SET {new} = {old} "
                f"WHERE ({new} IS NULL OR {new} = '') AND {old} IS NOT NULL AND {old} != ''")
        except sqlite3.OperationalError as e:
            logger.warning(f"[passage_index] cannot copy {old} → {new} ({e}); "
                           f"reads fall back to {old}")
            continue
        if cur.rowcount:
            logger.info(f"[passage_index] copied {cur.rowcount} value(s) {old} → {new}")


def _select_expr(column: str, have: set[str]) -> str:
    """How `query_passages` reads one column from this file."""
    old = _LEGACY_COLUMNS.get(column)
    if column in have and old in have:
        return f"COALESCE(NULLIF({column}, ''), NULLIF({old}, '')) AS {column}"
    if column in have:
        return column
    if old in have:
        return f"{old} AS {column}"
    return f"NULL AS {column}"


def upsert_passages(doc_id: str, records: list[dict]) -> int:
    """
    Delete then re-insert all passages for one doc (idempotent).
    Returns the number of rows actually written (counted from DB).
    Raises KeyError if a record is missing a required field.

    **An empty `records` clears the document.** This used to return before the
    delete, so a document whose second pass found no passages kept every row
    from its first — and zero is the extreme of "fewer hits than last time",
    which #467 requires to leave no corpses behind. It also made the delete
    conditional on the thing being deleted, which is the wrong way round: the
    caller is stating this document's passages, and "none" is a statement.

    Validation still runs before the database is touched, so a malformed batch
    raises without having deleted anything.
    """
    # Validate required fields before touching the DB.
    required = {"doc_id", "page", "entity_type", "text", "normalised",
                "char_start", "char_end"}
    for i, rec in enumerate(records):
        missing = required - set(rec.keys())
        if missing:
            raise KeyError(f"record {i} missing required fields: {missing}")
    
    db = _get_db()
    now = datetime.now(timezone.utc).isoformat()
    
    # Delete existing for this doc.
    db.execute("DELETE FROM passages WHERE doc_id = ?", (doc_id,))
    
    for rec in records:
        db.execute("""
            INSERT INTO passages
                (doc_id, page, bestand, entity_type, text, normalised,
                 char_start, char_end, context,
                 hub_id, gnd_id, hls_id, created_at)
            VALUES
                (:doc_id, :page, :bestand, :entity_type, :text, :normalised,
                 :char_start, :char_end, :context,
                 :hub_id, :gnd_id, :hls_id, :created_at)
        """, {
            "doc_id":      rec["doc_id"],
            "page":        rec["page"],
            # Absent rather than empty when the caller does not know (#396).
            "bestand":     rec.get("bestand") or None,
            "entity_type": rec["entity_type"],
            "text":        rec["text"],
            "normalised":  rec["normalised"],
            "char_start":  rec["char_start"],
            "char_end":    rec["char_end"],
            "context":     rec.get("context", ""),
            "hub_id":      rec.get("hub_id"),
            "gnd_id":      rec.get("gnd_id"),
            "hls_id":      rec.get("hls_id"),
            "created_at":  now,
        })
    db.commit()
    rows_written = db.execute(
        "SELECT COUNT(*) FROM passages WHERE doc_id = ?",
        (doc_id,)
    ).fetchone()[0]
    logger.info(f"[passage_index] upserted {rows_written} passage(s) for {doc_id}")
    return rows_written


def query_passages(
    *,
    entity_type: Optional[str] = None,
    normalised: Optional[str] = None,
    doc_id: Optional[str] = None,
    bestand: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Query the passage index by any combination of filters.
    Returns list[dict] sorted by doc_id, char_start, id (stable).
    """
    db = _get_db()
    where_parts = []
    params: dict = {}
    if entity_type is not None:
        where_parts.append("entity_type = :entity_type")
        params["entity_type"] = entity_type
    if normalised is not None:
        where_parts.append("normalised = :normalised")
        params["normalised"] = normalised
    if doc_id is not None:
        where_parts.append("doc_id = :doc_id")
        params["doc_id"] = doc_id
    if bestand is not None:
        # Rows that DECLARE this holding. A NULL `bestand` means nobody said
        # which one, so it is not a match — the alternative would be serving
        # rows of unknown provenance under a holding's name (#396).
        where_parts.append("bestand = :bestand")
        params["bestand"] = bestand
    where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    params["limit"] = limit
    params["offset"] = offset
    # Only the columns the file has: one the migration could not add reads
    # as None rather than failing the whole query (#812).
    have = _columns(db)
    select = ", ".join(_select_expr(c, have) for c in _ROW_COLUMNS)
    sql = (
        f"SELECT {select}"
        " FROM passages " + where_sql +
        " ORDER BY doc_id, char_start, id"
        " LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(sql, params).fetchall()
    return [dict(zip(_ROW_COLUMNS, r)) for r in rows]


def passage_count(doc_id: str | None = None) -> int:
    """Total passage count, optionally scoped to one doc."""
    db = _get_db()
    if doc_id is None:
        row = db.execute("SELECT COUNT(*) FROM passages").fetchone()
    else:
        row = db.execute(
            "SELECT COUNT(*) FROM passages WHERE doc_id = ?",
            (doc_id,)
        ).fetchone()
    return row[0] if row else 0


def reset_doc(doc_id: str) -> int:
    """Delete all passages for one doc. Returns count deleted."""
    db = _get_db()
    cur = db.execute(
        "DELETE FROM passages WHERE doc_id = ?", (doc_id,)
    )
    db.commit()
    logger.info(f"[passage_index] reset {doc_id}: {cur.rowcount} deleted")
    return cur.rowcount


def _reset_test_db(db_path: Path) -> None:
    """Point module at a test DB path and re-init the connection.
    Call this in a monkeypatch fixture.
    """
    global _DB, _TEST_DBPATH
    if _DB is not None:
        _DB.close()
        _DB = None
    _TEST_DBPATH = db_path


def _teardown_test_db() -> None:
    """Close test connection and restore for next test."""
    global _DB, _TEST_DBPATH
    if _DB is not None:
        _DB.close()
        _DB = None
    _TEST_DBPATH = None
