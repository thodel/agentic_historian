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


# ── the older column names, and the missing vectors ─────────────────────────

def _cli():
    """The package's ``__main__``, loaded by path — under pytest, ``import
    __main__`` is pytest's own (as in test_ah_392_batch_runner)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("ah_cli_812", PKG / "__main__.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LEGACY_SCHEMA = """
CREATE TABLE passages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL, page INTEGER NOT NULL, chunk_idx INTEGER,
    char_start INTEGER NOT NULL, char_end INTEGER NOT NULL,
    entity_type TEXT NOT NULL, text TEXT NOT NULL, normalised TEXT NOT NULL,
    context TEXT, hub_id TEXT, gnd TEXT, hls TEXT, wikidata TEXT,
    controlled_vocab TEXT, hub_confidence TEXT, link_method TEXT,
    created_at TEXT NOT NULL, content_hash TEXT, bestand TEXT
);
CREATE INDEX ix_passages_doc_id ON passages(doc_id);
CREATE INDEX ix_passages_entity_type ON passages(entity_type);
CREATE INDEX ix_passages_normalised ON passages(normalised);
CREATE INDEX ix_passages_hub_id ON passages(hub_id);
CREATE INDEX ix_passages_bestand ON passages(bestand);
INSERT INTO passages (doc_id, page, char_start, char_end, entity_type, text,
                      normalised, context, gnd, hls, created_at, bestand)
VALUES ('kf__U-17_0597', 1, 10, 16, 'CARE_ACTOR', 'spital', 'spital',
        'git och dem spital', '4056272-1', '007953', '2026-10-01', 'kf');
"""


@pytest.fixture
def legacy_db(tmp_path):
    """The column set tei's database printed on 06.11.2026, before gnd_id/hls_id."""
    path = tmp_path / "passages.db"
    con = sqlite3.connect(path)
    con.executescript(LEGACY_SCHEMA)
    con.commit()
    con.close()
    yield path
    passage_index._teardown_test_db()


def test_links_under_the_old_names_are_copied_to_the_new_columns(legacy_db):
    passage_index._reset_test_db(legacy_db)
    row = passage_index.query_passages()[0]
    assert (row["gnd_id"], row["hls_id"]) == ("4056272-1", "007953")
    on_disk = sqlite3.connect(legacy_db).execute(
        "SELECT gnd_id, hls_id, gnd, hls FROM passages").fetchone()
    assert on_disk == ("4056272-1", "007953", "4056272-1", "007953"), \
        "copied, and the old columns left as they were"


def test_a_value_already_in_the_new_column_is_never_overwritten(legacy_db):
    con = sqlite3.connect(legacy_db)
    con.executescript("ALTER TABLE passages ADD COLUMN gnd_id TEXT;"
                      "ALTER TABLE passages ADD COLUMN hls_id TEXT;"
                      "UPDATE passages SET gnd_id = 'newer';")
    con.commit()
    con.close()
    passage_index._reset_test_db(legacy_db)
    row = passage_index.query_passages()[0]
    assert row["gnd_id"] == "newer" and row["hls_id"] == "007953"


def test_a_read_only_legacy_database_answers_with_its_links(legacy_db):
    ro = sqlite3.connect(f"file:{legacy_db}?mode=ro", uri=True, check_same_thread=False)
    passage_index._reset_test_db(legacy_db)
    passage_index._DB = ro
    passage_index._init_schema(ro)
    row = passage_index.query_passages()[0]
    assert (row["gnd_id"], row["hls_id"]) == ("4056272-1", "007953")


def test_embed_passages_has_a_command(legacy_db, monkeypatch, capsys):
    import argparse

    import passage_embeddings as pe
    embed_passages_cmd = _cli().embed_passages_cmd

    passage_index._reset_test_db(legacy_db)
    seen = {}

    def fake(rows):
        seen["rows"] = rows
        return pe.Embedded(cached=0, embedded=len(rows), skipped=0)

    monkeypatch.setattr(pe, "embed_passages", fake)
    assert embed_passages_cmd(argparse.Namespace(doc_id=None)) == 0
    out = capsys.readouterr().out
    assert "passages  : 1" in out and "embedded  : 1 new" in out
    assert [r["normalised"] for r in seen["rows"]] == ["spital"]


def test_a_failed_embedding_is_said_not_raised(legacy_db, monkeypatch, capsys):
    import argparse

    import passage_embeddings as pe
    embed_passages_cmd = _cli().embed_passages_cmd

    passage_index._reset_test_db(legacy_db)

    def boom(rows):
        raise RuntimeError("GPUStack 403")

    monkeypatch.setattr(pe, "embed_passages", boom)
    assert embed_passages_cmd(argparse.Namespace(doc_id=None)) == 1
    assert "failed    : GPUStack 403" in capsys.readouterr().out


def test_the_empty_result_names_the_command():
    import passage_find

    f = passage_find.Found(query="almosen", indexed=1, candidates=1)
    assert "python -m agentic_historian embed-passages" in f.why_empty()
