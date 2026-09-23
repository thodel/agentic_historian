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
