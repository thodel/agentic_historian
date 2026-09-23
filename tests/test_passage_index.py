"""Tests for passage_index.py (Q1, #395)."""

import pytest
import sys
from pathlib import Path

_ORIG_DB = None


def test_passage_index_basic(tmp_path, monkeypatch):
    """Insert passages, query them, verify idempotent re-run."""
    # Patch config before importing passage_index
    sys.path.insert(0, str(Path(__file__).parent.parent / "agentic_historian"))
    import agentic_historian.config as _config
    monkeypatch.setattr(_config, "DATA_DIR", tmp_path)

    from agentic_historian import passage_index
    import agentic_historian.passage_index as pi
    pi._DB = None  # force re-init with new DATA_DIR

    passages = [
        {
            "doc_id": "doc1", "page": 1, "chunk_idx": 0,
            "char_start": 10, "char_end": 15,
            "entity_type": "PERSON", "text": "Hans",
            "normalised": "hans", "context": "Hans von Bern",
            "hub_id": "gnd:123", "gnd": "123",
            "hls": "", "wikidata": "",
            "controlled_vocab": "", "hub_confidence": "high",
            "link_method": "hub_exact",
        },
        {
            "doc_id": "doc1", "page": 1, "chunk_idx": 0,
            "char_start": 20, "char_end": 28,
            "entity_type": "PLACE", "text": "Bern",
            "normalised": "bern", "context": "in Bern",
            "hub_id": "", "gnd": "",
            "hls": "s001234", "wikidata": "",
            "controlled_vocab": "", "hub_confidence": "low",
            "link_method": "hls_lookup",
        },
        {
            "doc_id": "doc2", "page": 1, "chunk_idx": 0,
            "char_start": 0, "char_end": 6,
            "entity_type": "SOCIAL_GROUP", "text": "arme luet",
            "normalised": "arme luet", "context": "die arme luet",
            "hub_id": "", "gnd": "",
            "hls": "", "wikidata": "",
            "controlled_vocab": "arme_lüt", "hub_confidence": "medium",
            "link_method": "controlled_vocab",
        },
    ]

    n = passage_index.upsert_passages(passages)
    assert n == 3, f"expected 3, got {n}"

    results = passage_index.query_passages(entity_type="PERSON")
    assert len(results) == 1
    assert results[0]["text"] == "Hans"

    results = passage_index.query_passages(doc_id="doc1")
    assert len(results) == 2

    n2 = passage_index.upsert_passages(passages)
    assert n2 == 3  # idempotent
    assert passage_index.passage_count() == 3

    deleted = passage_index.reset_doc("doc1")
    assert deleted == 2
    assert passage_index.passage_count() == 1


def test_content_hash_deterministic(tmp_path, monkeypatch):
    """Same passage produces same hash (Q2 cache key)."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "agentic_historian"))
    import agentic_historian.config as _config
    monkeypatch.setattr(_config, "DATA_DIR", tmp_path)
    import agentic_historian.passage_index as pi
    pi._DB = None

    h1 = pi._content_hash("doc", 10, 20, "Hans")
    h2 = pi._content_hash("doc", 10, 20, "Hans")
    h3 = pi._content_hash("doc", 10, 20, "Peter")
    assert h1 == h2
    assert h1 != h3