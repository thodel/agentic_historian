"""
test_passage_index.py -- Q1a tests (#395).

Run with: python -m pytest agentic_historian/tests/test_passage_index.py
conftest.py at agentic_historian/tests/conftest.py sets the import path.
"""

import pathlib
import sys
import tempfile

import pytest

# conftest.py in the tests dir already fixes sys.path, but we duplicate
# the logic here so the file is self-contained when run directly.
PKG = str(pathlib.Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import passage_index


@pytest.fixture
def test_db(tmp_path):
    """Point passage_index at a temp DB; restore after."""
    db_path = tmp_path / "test_passages.db"
    passage_index._reset_test_db(db_path)
    yield db_path
    passage_index._teardown_test_db()


@pytest.fixture
def sample_records():
    return [
        {
            "doc_id":      "doc-A",
            "page":        1,
            "entity_type": "CARE_ACTOR",
            "text":        "Herr Muotert",
            "normalised":  "Herr Muoter",
            "char_start":  10,
            "char_end":    21,
            "context":     "... Herr Muotert ...",
        },
        {
            "doc_id":      "doc-A",
            "page":        1,
            "entity_type": "CARE_ACTION",
            "text":        "hat geholffen",
            "normalised":  "helfen",
            "char_start":  25,
            "char_end":    36,
            "context":     "... hat geholffen ...",
        },
        {
            "doc_id":      "doc-A",
            "page":        2,
            "entity_type": "ROLE",
            "text":        "der Vormund",
            "normalised":  "Vormund",
            "char_start":   5,
            "char_end":    15,
            "context":     "... der Vormund ...",
        },
        {
            "doc_id":      "doc-B",
            "page":        1,
            "entity_type": "CARE_ACTOR",
            "text":        "Frow Maller",
            "normalised":  "Frau Maller",
            "char_start":   0,
            "char_end":    11,
            "context":     "... Frow Maller ...",
        },
    ]


def test_write_and_read(test_db, sample_records):
    """Test 1: write three records for doc-A, read them back."""
    written = passage_index.upsert_passages("doc-A", sample_records[:3])
    assert written == 3, f"expected 3, got {written}"
    assert passage_index.passage_count("doc-A") == 3
    actors = passage_index.query_passages(entity_type="CARE_ACTOR")
    assert len(actors) == 1
    assert actors[0]["text"] == "Herr Muotert"


def test_idempotency(test_db, sample_records):
    """Test 2: calling upsert twice leaves count unchanged."""
    passage_index.upsert_passages("doc-A", sample_records[:3])
    passage_index.upsert_passages("doc-A", sample_records[:3])
    assert passage_index.passage_count("doc-A") == 3


def test_delete_then_insert(test_db, sample_records):
    """Test 3: second upsert with two of three records removes the third."""
    passage_index.upsert_passages("doc-A", sample_records[:3])
    assert passage_index.passage_count("doc-A") == 3
    passage_index.upsert_passages("doc-A", sample_records[:2])
    assert passage_index.passage_count("doc-A") == 2
    assert passage_index.passage_count() == 2


def test_filter_combined(test_db, sample_records):
    """Test 4: query by entity_type and normalised together."""
    passage_index.upsert_passages("doc-A", sample_records)
    result = passage_index.query_passages(
        entity_type="CARE_ACTOR", normalised="Herr Muoter"
    )
    assert len(result) == 1
    assert result[0]["text"] == "Herr Muotert"


def test_stable_sorting(test_db):
    """Test 5: two pages of a query with limit/offset are stable and complete."""
    docs = []
    for i in range(4):
        docs.append({
            "doc_id":      f"d{i}",
            "page":        1,
            "entity_type": "SOCIAL_GROUP",
            "text":        f"group_{i}",
            "normalised":  f"group_{i}",
            "char_start":  i * 10,
            "char_end":    i * 10 + 6,
            "context":     "...",
        })
    passage_index.upsert_passages("multi", docs)
    page1 = passage_index.query_passages(limit=2, offset=0)
    page2 = passage_index.query_passages(limit=2, offset=2)
    all_ids = [r["id"] for r in page1 + page2]
    assert len(set(all_ids)) == 4, "no duplicates expected"
    assert set(all_ids) == {r["id"] for r in page1 + page2}


def test_missing_required_field_raises(test_db):
    """Test 6: a record without a required field raises KeyError."""
    bad = [{
        "doc_id":      "bad",
        "page":        1,
        "entity_type": "CARE_ACTOR",
        # missing text
        "normalised":  "x",
        "char_start":  0,
        "char_end":    1,
    }]
    with pytest.raises(KeyError):
        passage_index.upsert_passages("bad-doc", bad)
    assert passage_index.passage_count() == 0


def test_reset_doc(test_db, sample_records):
    """reset_doc removes all passages for a doc and returns the count."""
    passage_index.upsert_passages("doc-A", sample_records[:3])
    deleted = passage_index.reset_doc("doc-A")
    assert deleted == 3
    assert passage_index.passage_count("doc-A") == 0


def test_passage_count_no_doc(test_db, sample_records):
    """passage_count without doc_id returns total across all docs."""
    passage_index.upsert_passages("doc-A", sample_records[:2])
    passage_index.upsert_passages("doc-B", sample_records[3:])
    assert passage_index.passage_count() == 3


def test_empty_records_returns_zero(test_db):
    """upsert_passages with empty list returns 0 and writes nothing."""
    result = passage_index.upsert_passages("any-doc", [])
    assert result == 0
    assert passage_index.passage_count() == 0


def test_empty_records_clear_a_doc_that_had_some(test_db, sample_records):
    """Zero is the extreme of "fewer than last time" (#467).

    This returned before the delete, so a document whose second pass found no
    passages kept every row from its first. The test above did not catch it
    because it only ever ran against a document that had none to begin with.
    """
    passage_index.upsert_passages("doc-A", sample_records[:2])
    assert passage_index.passage_count("doc-A") == 2

    assert passage_index.upsert_passages("doc-A", []) == 0
    assert passage_index.passage_count("doc-A") == 0


def test_a_malformed_batch_deletes_nothing(test_db, sample_records):
    """Validation before the database is touched: a batch that raises must not
    leave the document cleared."""
    passage_index.upsert_passages("doc-A", sample_records[:2])

    with pytest.raises(KeyError):
        passage_index.upsert_passages("doc-A", [{"doc_id": "doc-A"}])

    assert passage_index.passage_count("doc-A") == 2


# ── the bestand column, and reaching a database that predates it (#396) ────

def _legacy_db(path):
    """A passages table exactly as it shipped before `bestand` existed.

    Written by hand rather than by checking out the old module: what has to be
    migrated is a FILE, and the file is what this reproduces.
    """
    import sqlite3

    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE passages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id          TEXT    NOT NULL,
            page            INTEGER NOT NULL,
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
    """)
    db.execute(
        "INSERT INTO passages (doc_id, page, entity_type, text, normalised,"
        " char_start, char_end, context, created_at)"
        " VALUES ('old-doc', 1, 'ROLE', 'vogt', 'vogt', 0, 4, 'der vogt', 'then')")
    db.commit()
    db.close()


def test_an_existing_database_gains_the_column(tmp_path):
    """`CREATE TABLE IF NOT EXISTS` does nothing to a database that already
    exists, so a column added to the statement never reaches one — the only
    symptom being an OperationalError on the first query that names it."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    passage_index._reset_test_db(path)
    try:
        columns = {r[1] for r in
                   passage_index._get_db().execute("PRAGMA table_info(passages)")}
        assert "bestand" in columns
    finally:
        passage_index._teardown_test_db()


def test_the_rows_that_were_already_there_survive_the_migration(tmp_path):
    """ALTER TABLE ADD COLUMN on a nullable column rewrites no rows. The point
    of the migration is that a corpus somebody spent hours filling is still
    there afterwards."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    passage_index._reset_test_db(path)
    try:
        rows = passage_index.query_passages(doc_id="old-doc")
        assert len(rows) == 1
        assert rows[0]["normalised"] == "vogt"
        assert rows[0]["bestand"] is None, "nobody said which holding"
    finally:
        passage_index._teardown_test_db()


def test_migrating_twice_is_harmless(tmp_path):
    """It runs on every connect rather than behind a version counter nobody
    would remember to bump, so it has to be idempotent."""
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    for _ in range(3):
        passage_index._reset_test_db(path)
        passage_index._get_db()
        passage_index._teardown_test_db()

    passage_index._reset_test_db(path)
    try:
        assert passage_index.passage_count("old-doc") == 1
    finally:
        passage_index._teardown_test_db()


def test_a_migrated_database_accepts_a_bestand(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_db(path)

    passage_index._reset_test_db(path)
    try:
        passage_index.upsert_passages("new-doc", [{
            "doc_id": "new-doc", "page": 1, "bestand": "Marbach",
            "entity_type": "ROLE", "text": "vogt", "normalised": "vogt",
            "char_start": 0, "char_end": 4,
        }])
        assert passage_index.query_passages(bestand="Marbach")[0]["doc_id"] == "new-doc"
    finally:
        passage_index._teardown_test_db()


def test_a_holding_filter_matches_only_rows_that_declare_it(test_db):
    """A NULL `bestand` means nobody said which holding, so it is not a match —
    serving rows of unknown provenance under a holding's name is worse than not
    finding them."""
    def rec(doc, bestand):
        out = {"doc_id": doc, "page": 1, "entity_type": "ROLE", "text": "vogt",
               "normalised": "vogt", "char_start": 0, "char_end": 4}
        if bestand is not None:
            out["bestand"] = bestand
        return out

    passage_index.upsert_passages("a", [rec("a", "Marbach")])
    passage_index.upsert_passages("b", [rec("b", "Inzigkofen")])
    passage_index.upsert_passages("c", [rec("c", None)])

    assert [r["doc_id"] for r in passage_index.query_passages(bestand="Marbach")] == ["a"]
    assert len(passage_index.query_passages()) == 3


def test_an_empty_bestand_is_stored_as_absent(test_db):
    """"" and None both mean nobody said. Keeping both would make a holding
    filter depend on which one a caller happened to pass."""
    passage_index.upsert_passages("a", [{
        "doc_id": "a", "page": 1, "bestand": "", "entity_type": "ROLE",
        "text": "vogt", "normalised": "vogt", "char_start": 0, "char_end": 4,
    }])

    assert passage_index.query_passages()[0]["bestand"] is None


def test_bestand_is_not_a_required_field(test_db, sample_records):
    """Every caller that worked before must keep working: the column is an
    addition, not a new obligation."""
    assert passage_index.upsert_passages("doc-A", sample_records[:2]) == 2
    assert all(r["bestand"] is None for r in passage_index.query_passages())
