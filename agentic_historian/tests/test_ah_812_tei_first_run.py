"""What the first Agent F run on tei hit, before it reached any model output (#812).

06.11.2026, ``python -m agentic_historian ask …`` as ``dh`` on tei:

* every full-text search: ``Permission denied: …/knowledge_hub/data/.hub.json.lock``.
  ``knowledge_hub/__init__`` imports ``hub``, and ``hub`` built its singleton at
  import — reading and seeding hub.json under a lock file that belongs to the
  service user. Importing ``mcp_registry`` was enough to take that lock.
* every search of the own passages: ``no such column: gnd_id`` — the database
  on tei lacks columns the query asked for.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_812_tei_first_run.py
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import passage_index

# ── the hub is built on first use, not at import ────────────────────────────

def test_importing_the_registry_touches_no_hub_file(tmp_path):
    """In a fresh interpreter, with the hub pointed at an empty directory:
    importing the registry and the federation client creates nothing there."""
    kh = tmp_path / "kh"
    kh.mkdir()
    code = f"""
import sys; sys.path.insert(0, {str(PKG)!r})
from pathlib import Path
import config
config.KH_DIR = Path({str(kh)!r})
from knowledge_hub import mcp_registry, hub
from utils import mcp_client
assert hub._hub is None, "the hub was built at import"
assert sorted(p.name for p in Path({str(kh)!r}).iterdir()) == [], "a file was touched"
store = hub.get_store()
assert hub._hub is store and hub.get_store() is store
print("ok")
"""
    done = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True, text=True,
                          cwd=PKG, env={**os.environ})
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


# ── an old passage database still answers ───────────────────────────────────

#: What the older code built: the indexes are there (on tei `passage_count`
#: worked, so opening the file wrote nothing), `gnd_id` and `hls_id` are not.
OLD_SCHEMA = """
CREATE TABLE passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL, page INTEGER NOT NULL, bestand TEXT,
    entity_type TEXT NOT NULL, text TEXT NOT NULL, normalised TEXT NOT NULL,
    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL, context TEXT,
    hub_id TEXT, created_at TEXT NOT NULL
);
CREATE INDEX ix_passages_doc_id ON passages(doc_id);
CREATE INDEX ix_passages_entity_type ON passages(entity_type);
CREATE INDEX ix_passages_normalised ON passages(normalised);
CREATE INDEX ix_passages_hub_id ON passages(hub_id);
CREATE INDEX ix_passages_bestand ON passages(bestand);
INSERT INTO passages (doc_id, page, bestand, entity_type, text, normalised,
                      char_start, char_end, context, hub_id, created_at)
VALUES ('kf__U-17_0597', 1, 'kf', 'CARE_ACTOR', 'spital', 'spital', 10, 16,
        'git och dem spital und den hoͤfen', NULL, '2026-10-01');
"""


@pytest.fixture
def old_db(tmp_path):
    path = tmp_path / "passages.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    con.commit()
    con.close()
    yield path
    passage_index._teardown_test_db()


def test_a_database_without_gnd_and_hls_is_brought_up_to_date(old_db):
    passage_index._reset_test_db(old_db)
    rows = passage_index.query_passages()
    assert [r["normalised"] for r in rows] == ["spital"]
    assert rows[0]["gnd_id"] is None and rows[0]["hls_id"] is None
    cols = {r[1] for r in sqlite3.connect(old_db).execute("PRAGMA table_info(passages)")}
    assert {"gnd_id", "hls_id"} <= cols


def test_a_read_only_database_is_read_without_the_missing_columns(old_db):
    """The CLI as `dh` may not write the service's file: the migration then
    cannot add a column, and the rows are still read."""
    ro = sqlite3.connect(f"file:{old_db}?mode=ro", uri=True, check_same_thread=False)
    passage_index._reset_test_db(old_db)
    passage_index._DB = ro
    passage_index._init_schema(ro)            # must not raise on a read-only file
    rows = passage_index.query_passages(bestand="kf")
    assert rows and rows[0]["context"].startswith("git och dem spital")
    assert rows[0]["gnd_id"] is None
    assert passage_index.passage_count() == 1
