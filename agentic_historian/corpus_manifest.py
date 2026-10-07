"""
corpus_manifest.py — R1: a corpus run that survives being interrupted (#391).

Corpus-level state lives in ``processed_orders.json`` plus one RunState per
document. That is enough for a serial worker and useless for resuming a
5,000-page run: a flat set of finished ids cannot say what is in flight, what
failed and why, or how often something has been tried. This is the checkpoint
store R2 claims work from.

**The manifest does not replace RunState.** RunState stays authoritative for
gates and stages; an item here carries the ``doc_id`` and nothing about stages.
Two stores that both claimed to know how far a document had got would disagree
the first time one was written and the other was not.

Why a claim is a transaction, and specifically ``BEGIN IMMEDIATE``
──────────────────────────────────────────────────────────────────
SQLite's default is a *deferred* transaction: ``BEGIN`` takes no lock until the
first statement, and a read lock is shared. Two workers running
``SELECT … WHERE status='pending' LIMIT 1`` inside deferred transactions both
see the same row and both ``UPDATE`` it — the second write succeeds, so both
believe they own that document and it is processed twice. Measured here, not
assumed::

    BEGIN            A saw 1  B-also-claimable=True   (both saw the row)
    BEGIN IMMEDIATE  A saw 1  B-also-claimable=False  (database is locked)

So :func:`claim` opens with ``BEGIN IMMEDIATE``, which takes the write lock up
front, and the second worker blocks on ``busy_timeout`` instead of racing.

A connection per thread, keyed on the path
──────────────────────────────────────────
``passage_index`` keeps one module-level connection with
``check_same_thread=False``. That is fine for one worker and the wrong shape
here, because R2's workers are concurrent by design. Each thread gets its own
connection, cached against the database path — a cached connection that ignores
the path it was opened for is the same trap as a derived value that ignores what
it is derived from, and it is what makes a test that moves ``DATA_DIR`` silently
read the previous test's database.

``running`` is the status that can lie
──────────────────────────────────────
``running`` asserts that a worker is on this document. A worker killed mid-page
leaves that assertion behind for ever, and a resume then skips exactly the
document it should pick up — which defeats the one thing #391 exists for. So a
claim carries a heartbeat and :func:`reclaim_stale` makes the recovery
**explicit**:

* staleness is measured from the last sign of life, not from the claim, because
  a legitimately slow document (a 42 s fusion page, a twenty-minute order) would
  otherwise be reclaimed and run twice;
* a timestamp that cannot be parsed is **not** reclaimed. We cannot tell how old
  it is, and of the two errors available — stranding one document an operator
  can see as ``running``, or silently running it twice — only the first is
  recoverable by a human;
* a reclaim counts as the failed attempt it was, so a document that kills its
  worker every time eventually reaches ``dead`` instead of looping.

The retry *policy* belongs to R2; ``max_attempts`` is a parameter here because
the store holds ``attempts`` and the transition has to be atomic with it.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from loguru import logger

import config

__all__ = [
    "DEAD",
    "DONE",
    "FAILED",
    "Item",
    "MAX_ATTEMPTS",
    "PENDING",
    "Progress",
    "RUNNING",
    "STALE_AFTER",
    "STATUSES",
    "claim",
    "close",
    "db_path",
    "heartbeat",
    "items",
    "mark_done",
    "mark_failed",
    "progress",
    "reclaim_stale",
    "register",
    "requeue",
    "runs",
]

MANIFEST_SCHEMA = "ah-manifest/1"

PENDING, RUNNING, DONE, FAILED, DEAD = (
    "pending", "running", "done", "failed", "dead")

#: Every status an item can hold. :class:`Progress` counts all of them, so a
#: status added without a counter shows up as a total that does not add up
#: rather than as a quietly missing document.
STATUSES = (PENDING, RUNNING, DONE, FAILED, DEAD)

#: Statuses a worker may take. ``failed`` is claimable — that *is* retrying.
#: ``dead`` is not, or the dead-letter is not one.
CLAIMABLE = (PENDING, FAILED)

#: Default attempt ceiling. R2 owns the policy and passes its own.
MAX_ATTEMPTS = 3

#: Seconds without a sign of life before a claim may be reclaimed. Generous on
#: purpose: a page whose fusion arbitration takes 42 s (#406) and a multi-page
#: order are both normal, and reclaiming live work runs a document twice.
STALE_AFTER = 1800.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_path() -> Path:
    """Where the manifest lives.

    A function rather than a constant: a constant is computed once at import and
    then stops reading ``DATA_DIR``, which is the trap ``config.page_cache_dir``
    was turned into a function for.
    """
    return config.DATA_DIR / "corpus_manifest.db"


_local = threading.local()


def connect() -> sqlite3.Connection:
    """This thread's connection to the manifest, opening it if needed.

    ``isolation_level=None`` turns off Python's implicit transaction handling so
    :func:`claim` can say ``BEGIN IMMEDIATE`` and mean it. WAL lets a reader
    (``/status``) run while a worker writes, and ``busy_timeout`` makes a second
    writer wait for the lock instead of raising at once.
    """
    path = db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == path:
        return conn
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:                    # pragma: no cover — defensive
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    _init_schema(conn)
    _local.conn = conn
    _local.path = path
    return conn


def close() -> None:
    """Drop this thread's connection. For tests and for a clean shutdown."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except sqlite3.Error:                    # pragma: no cover — defensive
            pass
    _local.conn = None
    _local.path = None


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS manifest_runs (
            run_id         TEXT PRIMARY KEY,
            source         TEXT,
            label          TEXT,
            registered_at  TEXT NOT NULL,
            schema_version TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS manifest_items (
            run_id        TEXT    NOT NULL,
            doc_id        TEXT    NOT NULL,
            status        TEXT    NOT NULL,
            attempts      INTEGER NOT NULL DEFAULT 0,
            worker        TEXT,
            error         TEXT,
            registered_at TEXT    NOT NULL,
            claimed_at    TEXT,
            heartbeat_at  TEXT,
            started_at    TEXT,
            finished_at   TEXT,
            PRIMARY KEY (run_id, doc_id)
        );
        CREATE INDEX IF NOT EXISTS ix_manifest_items_status
            ON manifest_items(run_id, status);
    """)


# ── rows ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Run:
    run_id: str
    source: str = ""
    label: str = ""
    registered_at: str = ""
    schema_version: str = MANIFEST_SCHEMA


@dataclass(frozen=True)
class Item:
    """One document in one run."""

    run_id: str
    doc_id: str
    status: str = PENDING
    #: How often this document has been **started**, not how often it failed. A
    #: row at ``attempts=3, status=pending`` has been handed out three times.
    attempts: int = 0
    worker: str = ""
    #: The last failure's message, or ``None`` when nothing has failed. Never
    #: ``""`` — "no failure recorded" and "failed with an empty message" are
    #: different facts, and only the first is common.
    error: Optional[str] = None
    registered_at: str = ""
    claimed_at: Optional[str] = None
    heartbeat_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @property
    def last_seen(self) -> Optional[str]:
        """The latest sign of life: a heartbeat if one arrived, else the claim."""
        return self.heartbeat_at or self.claimed_at

    @property
    def duration(self) -> Optional[float]:
        """Seconds the **last** attempt took, or None when it has not finished.

        Only the latest attempt is timed: a per-attempt history would be another
        table and nothing in R2 asks for one. None rather than 0.0, because a
        document that has not finished has no duration — it does not have a
        duration of nothing.
        """
        start, end = _parse(self.started_at), _parse(self.finished_at)
        if start is None or end is None:
            return None
        return (end - start).total_seconds()


def _parse(stamp: Optional[str]) -> Optional[datetime]:
    """An ISO-8601 timestamp, or None when it is absent or unreadable."""
    if not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        logger.warning(f"[manifest] unparsable timestamp {stamp!r}")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _item(row: sqlite3.Row) -> Item:
    return Item(run_id=row["run_id"], doc_id=row["doc_id"], status=row["status"],
                attempts=int(row["attempts"] or 0), worker=row["worker"] or "",
                error=row["error"], registered_at=row["registered_at"] or "",
                claimed_at=row["claimed_at"], heartbeat_at=row["heartbeat_at"],
                started_at=row["started_at"], finished_at=row["finished_at"])


# ── registering ──────────────────────────────────────────────────────────────

def register(run_id: str, doc_ids: Iterable[str], *, source: str = "",
             label: str = "") -> Run:
    """Register a run and its documents. Idempotent and additive.

    Re-registering is the normal case, not an error: a run is re-registered to
    resume it, and a holding may have grown since. ``INSERT OR IGNORE`` means a
    document already in the manifest keeps its status, attempts and error — the
    alternative resets finished work to pending and reprocesses a corpus
    somebody waited hours for.

    Duplicates and blanks in ``doc_ids`` are dropped rather than stored: the
    source is a directory listing, and a listing that yields the same name twice
    must not become two rows that both get claimed.
    """
    if not run_id:
        raise ValueError("run_id is required")
    conn = connect()
    stamp = _now()
    seen, fresh = set(), []
    for doc_id in doc_ids or ():
        doc_id = (doc_id or "").strip()
        if not doc_id or doc_id in seen:
            continue
        seen.add(doc_id)
        fresh.append(doc_id)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO manifest_runs "
            "(run_id, source, label, registered_at, schema_version) "
            "VALUES (?,?,?,?,?)",
            (run_id, source, label, stamp, MANIFEST_SCHEMA))
        conn.executemany(
            "INSERT OR IGNORE INTO manifest_items "
            "(run_id, doc_id, status, attempts, registered_at) "
            "VALUES (?,?,?,0,?)",
            [(run_id, doc_id, PENDING, stamp) for doc_id in fresh])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    logger.info(f"[manifest] {run_id}: registered, {len(fresh)} document(s) "
                f"offered, {progress(run_id).total} in the manifest")
    return run(run_id) or Run(run_id=run_id, source=source, label=label,
                              registered_at=stamp)


def run(run_id: str) -> Optional[Run]:
    row = connect().execute(
        "SELECT * FROM manifest_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return None
    return Run(run_id=row["run_id"], source=row["source"] or "",
               label=row["label"] or "", registered_at=row["registered_at"] or "",
               schema_version=row["schema_version"] or MANIFEST_SCHEMA)


def runs() -> list[Run]:
    """Every registered run, newest first."""
    return [Run(run_id=r["run_id"], source=r["source"] or "",
                label=r["label"] or "", registered_at=r["registered_at"] or "",
                schema_version=r["schema_version"] or MANIFEST_SCHEMA)
            for r in connect().execute(
                "SELECT * FROM manifest_runs ORDER BY registered_at DESC, run_id")]


def items(run_id: str, *, status: Optional[str] = None,
          limit: Optional[int] = None) -> list[Item]:
    sql = "SELECT * FROM manifest_items WHERE run_id=?"
    args: list = [run_id]
    if status is not None:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY doc_id"
    if limit is not None:
        sql += " LIMIT ?"
        args.append(int(limit))
    return [_item(r) for r in connect().execute(sql, args)]


def item(run_id: str, doc_id: str) -> Optional[Item]:
    row = connect().execute(
        "SELECT * FROM manifest_items WHERE run_id=? AND doc_id=?",
        (run_id, doc_id)).fetchone()
    return _item(row) if row else None


# ── progress ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Progress:
    """"N done / M failed / K pending" — with every status counted.

    ``counted`` exists so the parts can be checked against the whole. A progress
    report whose parts do not sum to its total is quietly losing documents, and
    that is the defect a reader would never spot by looking at the numbers.
    """

    run_id: str
    total: int = 0
    pending: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    dead: int = 0

    @property
    def counted(self) -> int:
        return self.pending + self.running + self.done + self.failed + self.dead

    @property
    def terminal(self) -> int:
        """Documents nothing will touch again: finished, or given up on."""
        return self.done + self.dead

    @property
    def outstanding(self) -> int:
        return self.total - self.terminal

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.outstanding == 0

    @property
    def percent(self) -> Optional[float]:
        """Share of the run that is terminal, or None for an empty manifest.

        None rather than 0.0: "nothing is registered" and "nothing is finished"
        are different answers, and 0 % of nothing reads as a stalled run.
        """
        return (self.terminal / self.total) if self.total else None


def progress(run_id: str) -> Progress:
    counts = {s: 0 for s in STATUSES}
    unknown = {}
    for row in connect().execute(
            "SELECT status, COUNT(*) AS n FROM manifest_items "
            "WHERE run_id=? GROUP BY status", (run_id,)):
        if row["status"] in counts:
            counts[row["status"]] = int(row["n"])
        else:
            # A status the store does not know about would otherwise vanish from
            # the report while still being in the total.
            unknown[row["status"]] = int(row["n"])
            logger.warning(f"[manifest] {run_id}: {row['n']} item(s) with "
                           f"unknown status {row['status']!r}")
    total = sum(counts.values()) + sum(unknown.values())
    return Progress(run_id=run_id, total=total, **counts)


# ── claiming ─────────────────────────────────────────────────────────────────

def claim(run_id: str, *, worker: str = "", limit: int = 1) -> list[Item]:
    """Take up to ``limit`` claimable documents, atomically.

    ``BEGIN IMMEDIATE`` rather than a plain ``BEGIN``: with a deferred
    transaction both workers read the same pending row and both update it, so
    the document runs twice (see the module docstring for the measurement).

    Ordered by ``attempts`` first, so a fresh document is preferred over a
    retry. Without that one poisoned document is retried ahead of untouched work
    for the rest of the run.
    """
    limit = max(1, int(limit or 1))
    conn = connect()
    stamp = _now()
    claimed: list[Item] = []
    placeholders = ",".join("?" for _ in CLAIMABLE)
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            f"SELECT * FROM manifest_items WHERE run_id=? "
            f"AND status IN ({placeholders}) "
            f"ORDER BY attempts ASC, registered_at ASC, doc_id ASC LIMIT ?",
            (run_id, *CLAIMABLE, limit)).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE manifest_items SET status=?, attempts=attempts+1, "
                "worker=?, claimed_at=?, heartbeat_at=?, started_at=?, "
                "finished_at=NULL WHERE run_id=? AND doc_id=?",
                (RUNNING, worker, stamp, stamp, stamp, run_id, row["doc_id"]))
            claimed.append(_item(conn.execute(
                "SELECT * FROM manifest_items WHERE run_id=? AND doc_id=?",
                (run_id, row["doc_id"])).fetchone()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return claimed


def heartbeat(run_id: str, doc_id: str, *, worker: str = "") -> bool:
    """Say the worker is still alive on this document.

    Returns False when the row is no longer ``running`` or no longer this
    worker's — which is how a worker finds out its claim was reclaimed while it
    was busy, instead of finishing over somebody else's work.
    """
    conn = connect()
    sql = ("UPDATE manifest_items SET heartbeat_at=? "
           "WHERE run_id=? AND doc_id=? AND status=?")
    args: list = [_now(), run_id, doc_id, RUNNING]
    if worker:
        sql += " AND worker=?"
        args.append(worker)
    return conn.execute(sql, args).rowcount > 0


def mark_done(run_id: str, doc_id: str) -> Optional[Item]:
    """Record a finished document. Clears the error: it succeeded."""
    conn = connect()
    stamp = _now()
    conn.execute(
        "UPDATE manifest_items SET status=?, finished_at=?, error=NULL, "
        "heartbeat_at=NULL WHERE run_id=? AND doc_id=?",
        (DONE, stamp, run_id, doc_id))
    return item(run_id, doc_id)


def mark_failed(run_id: str, doc_id: str, error: str = "", *,
                max_attempts: int = MAX_ATTEMPTS) -> Optional[Item]:
    """Record a failed attempt, and dead-letter it once the attempts run out.

    The ceiling is a parameter because the retry policy is R2's; the transition
    lives here because it has to be atomic with ``attempts``, which this store
    owns. An item at or over the ceiling becomes ``dead`` and is no longer
    claimable; below it, ``failed``, which is.

    ``error`` is stored as given, or ``None`` when it is empty — a row that says
    it failed with no message at all is a row nobody can act on, and the absence
    should look like absence.
    """
    conn = connect()
    stamp = _now()
    row = item(run_id, doc_id)
    if row is None:
        return None
    status = DEAD if row.attempts >= max(1, int(max_attempts)) else FAILED
    conn.execute(
        "UPDATE manifest_items SET status=?, finished_at=?, error=?, "
        "heartbeat_at=NULL WHERE run_id=? AND doc_id=?",
        (status, stamp, (error or "").strip() or None, run_id, doc_id))
    if status == DEAD:
        logger.warning(f"[manifest] {run_id}/{doc_id}: dead after "
                       f"{row.attempts} attempt(s): {error or 'no message'}")
    return item(run_id, doc_id)


def reclaim_stale(run_id: Optional[str] = None, *,
                  older_than: float = STALE_AFTER,
                  max_attempts: int = MAX_ATTEMPTS,
                  now: Optional[datetime] = None) -> list[Item]:
    """Hand back claims that have shown no sign of life, explicitly.

    Without this, a worker killed mid-page leaves a ``running`` row that no
    resume will ever pick up — which defeats the one thing #391 exists for.

    Three decisions worth keeping:

    * staleness counts from the **last heartbeat**, not from the claim, so a
      legitimately slow document is not reclaimed and run twice;
    * a row whose timestamp cannot be parsed is left alone. We cannot tell how
      old it is, and of the two available errors — one document stranded as
      ``running`` where an operator can see it, or the same document silently
      running twice — only the first is recoverable by a human;
    * the reclaim counts as the attempt it was, so a document that kills its
      worker every time reaches ``dead`` instead of looping for ever.
    """
    conn = connect()
    reference = now or datetime.now(timezone.utc)
    sql = "SELECT * FROM manifest_items WHERE status=?"
    args: list = [RUNNING]
    if run_id is not None:
        sql += " AND run_id=?"
        args.append(run_id)

    reclaimed: list[Item] = []
    for row in conn.execute(sql, args).fetchall():
        candidate = _item(row)
        seen = _parse(candidate.last_seen)
        if seen is None:
            logger.warning(f"[manifest] {candidate.run_id}/{candidate.doc_id}: "
                           f"running with no readable timestamp — left alone "
                           f"rather than risking a second run")
            continue
        if (reference - seen).total_seconds() < older_than:
            continue
        status = (DEAD if candidate.attempts >= max(1, int(max_attempts))
                  else FAILED)
        conn.execute(
            "UPDATE manifest_items SET status=?, error=?, heartbeat_at=NULL, "
            "finished_at=? WHERE run_id=? AND doc_id=?",
            (status, f"claim went stale (last sign of life {candidate.last_seen})",
             _now(), candidate.run_id, candidate.doc_id))
        logger.info(f"[manifest] {candidate.run_id}/{candidate.doc_id}: stale "
                    f"claim reclaimed as {status}")
        got = item(candidate.run_id, candidate.doc_id)
        if got is not None:
            reclaimed.append(got)
    return reclaimed


def requeue(run_id: str, doc_ids: Optional[Iterable[str]] = None) -> list[Item]:
    """Put failed and dead documents back in the queue, attempts reset.

    The way back from the dead letter. Without it a ``dead`` row needs hand-typed
    SQL, and "resume after fixing the bug" is the most ordinary thing an operator
    will want to do.

    Deliberately leaves ``done`` alone, whatever is asked: re-running a finished
    document is a different request, and quietly granting it here would let a
    typo cost a corpus.

    ``None`` means "everything"; ``[]`` means "these, of which there are none".
    The early return keeps those apart explicitly. SQLite happens to give the
    same answer for ``IN ()`` — it is the one engine that accepts an empty list
    there — so the guard looks redundant against this backend and is not: it is
    the only thing stating the distinction.
    """
    conn = connect()
    stamp = _now()
    sql = ("UPDATE manifest_items SET status=?, attempts=0, error=NULL, "
           "worker=NULL, claimed_at=NULL, heartbeat_at=NULL, started_at=NULL, "
           "finished_at=NULL, registered_at=? "
           "WHERE run_id=? AND status IN (?,?)")
    args: list = [PENDING, stamp, run_id, FAILED, DEAD]
    wanted = [d for d in (doc_ids or ()) if d]
    if doc_ids is not None:
        if not wanted:
            return []
        sql += f" AND doc_id IN ({','.join('?' for _ in wanted)})"
        args.extend(wanted)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(sql, args)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return [i for i in items(run_id, status=PENDING)
            if doc_ids is None or i.doc_id in set(wanted)]
