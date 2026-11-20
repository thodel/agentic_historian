"""The tei database the #395 prototype built still refused an insert (#892).

2026-11-19, run `lassberg-proof-4` on tei: two of five letters died in
`agents.entity_agent:_index_passages` with
`NOT NULL constraint failed: passages.content_hash`, and the other three
logged `upserted 0 passage(s)`.

The column belongs to no schema this module creates. It is the prototype's
(commit 803d575, 2026-09-23): `content_hash TEXT NOT NULL`, the key of the
embedding cache that later moved to the `passage_vectors` table.
`CREATE TABLE IF NOT EXISTS` does nothing to a file that already exists and
the migration only adds columns, so tei's file still carries a NOT NULL
column the insert omitted — and failed on, for every document whose passage
list was not empty. The three clean logs are the same fact without the
insert: their published entities (0041, 0053, 0054) are PERSON, PLACE, DATE
and ORG only — none of `VOCAB_TYPES` — hence an empty list; the delete ran,
the insert never did, and zero is the true count ("none is a statement",
#467). (QA 0.00 belonged to the two letters that failed, 0036 and 0048, which
had 3 and 5 located vocabulary entities; `index-passages` writes them now.)

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_892_legacy_content_hash.py
"""

import hashlib
import json
import sqlite3
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest  # noqa: E402

import passage_index as pi  # noqa: E402
from agents import entity_agent as ec  # noqa: E402

# ── the database the prototype built ────────────────────────────────────────

#: Verbatim the table of commit 803d575 (2026-09-23) — `content_hash
#: TEXT NOT NULL`, and no `bestand`, which the #396 migration added later.
PROTOTYPE_SCHEMA = """
CREATE TABLE passages (
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
CREATE INDEX ix_passages_doc_id       ON passages(doc_id);
CREATE INDEX ix_passages_entity_type  ON passages(entity_type);
CREATE INDEX ix_passages_normalised   ON passages(normalised);
CREATE INDEX ix_passages_hub_id       ON passages(hub_id);
CREATE INDEX ix_passages_created_at   ON passages(created_at);
CREATE INDEX ix_passages_content_hash ON passages(content_hash);
"""


def _proto_hash(doc_id, start, end, text):
    """The prototype's recipe, computed here from first principles."""
    return hashlib.sha1(f"{doc_id}:{start}:{end}:{text}".encode()).hexdigest()[:16]


@pytest.fixture
def legacy_db(tmp_path, monkeypatch):
    """A passages.db as the prototype left it, with one of its rows in it."""
    path = tmp_path / "passages.db"
    con = sqlite3.connect(path)
    con.executescript(PROTOTYPE_SCHEMA)
    con.execute(
        "INSERT INTO passages (doc_id, page, char_start, char_end, entity_type,"
        " text, normalised, context, gnd, hls, created_at, content_hash)"
        " VALUES ('kf__U-17_0597', 1, 10, 16, 'CARE_ACTOR', 'spital', 'spital',"
        " 'git och dem spital', '4056272-1', '007953', '2026-10-01', ?)",
        (_proto_hash("kf__U-17_0597", 10, 16, "spital"),))
    con.commit()
    con.close()
    pi._reset_test_db(path)
    monkeypatch.setattr(ec.config, "OUTPUTS_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        pi._teardown_test_db()


def _record(doc_id="lassberg-letter-0036", text="vogt", start=0, end=4, **kw):
    return {
        "doc_id": doc_id, "page": 1, "bestand": "lassberg",
        "entity_type": "ROLE", "text": text, "normalised": text,
        "char_start": start, "char_end": end, "context": "",
        "hub_id": None, "gnd_id": None, "hls_id": None, **kw,
    }


# ── the insert the old file used to refuse ──────────────────────────────────

def test_a_prototype_database_accepts_the_current_insert(legacy_db):
    """The exact failure of #892, as a test: a non-empty upsert into a file
    that still declares `content_hash TEXT NOT NULL`."""
    written = pi.upsert_passages("lassberg-letter-0036", [_record()])
    assert written == 1

    # Idempotent there as anywhere: a second pass replaces, does not double.
    written = pi.upsert_passages("lassberg-letter-0036", [_record()])
    assert written == 1
    assert pi.passage_count("lassberg-letter-0036") == 1


def test_the_new_row_gets_the_prototypes_hash(legacy_db):
    """A new row must key the way the old rows key: same span and text, same
    16-hex key — a second recipe would split one passage across two keys in a
    file that carries both generations."""
    pi.upsert_passages("lassberg-letter-0036", [_record()])

    stored = sqlite3.connect(legacy_db).execute(
        "SELECT content_hash FROM passages"
        " WHERE doc_id = 'lassberg-letter-0036'").fetchone()[0]
    assert stored == _proto_hash("lassberg-letter-0036", 0, 4, "vogt")
    assert len(stored) == 16


def test_the_old_row_keeps_its_key_and_its_links(legacy_db):
    """The migration may copy gnd/hls across, but the row the prototype wrote
    keeps the key it has had since 2026-10-01."""
    pi.upsert_passages("lassberg-letter-0036", [_record()])

    con = sqlite3.connect(legacy_db)
    old = con.execute(
        "SELECT content_hash, gnd_id, hls_id FROM passages"
        " WHERE doc_id = 'kf__U-17_0597'").fetchone()
    con.close()
    assert old == (_proto_hash("kf__U-17_0597", 10, 16, "spital"),
                   "4056272-1", "007953")


# ── the offline reproduction the issue asks for ─────────────────────────────

class _Recorder:
    """A stand-in for loguru that keeps what `warning` was called with."""

    def __init__(self):
        self.warnings = []

    def warning(self, *args, **kwargs):
        self.warnings.append(" ".join(str(a) for a in args))

    def info(self, *args, **kwargs):
        pass


def test_agent_c_indexes_a_passage_in_a_prototype_database(legacy_db, monkeypatch):
    """One full Agent C pass — a transcription with a usable fragment — into
    the file that refused on tei: the row is written, the entities survive,
    and nothing is logged at warning."""
    rec = _Recorder()
    monkeypatch.setattr(ec, "logger", rec)
    monkeypatch.setattr(ec.gs, "chat_text",
                        lambda prompt, **kw: json.dumps(
                            {"entities": [{"text": "vogt", "type": "ROLE",
                                           "normalised": "Vogt",
                                           "context": "Der"}]}))
    monkeypatch.setattr(ec.hub, "match_vocabulary", lambda term: None)

    out = ec.extract_entities("lassberg-letter-0036",
                              "Der vogt von Kungsfelt gab almosen.",
                              bestand="lassberg")

    assert any(e["text"] == "vogt" for e in out["entities"])
    rows = pi.query_passages(doc_id="lassberg-letter-0036")
    assert len(rows) == 1 and rows[0]["text"] == "vogt"
    assert not [w for w in rec.warnings if "Passagen-Index" in w], rec.warnings


def test_zero_passages_is_a_true_statement_not_a_failure(legacy_db, monkeypatch):
    """The three clean letters: no entity of a vocabulary type, hence an
    empty list. The delete runs, the insert never
    does, nothing is logged at warning — and the first pass's row is gone,
    because "none" is a statement (#467)."""
    rec = _Recorder()
    monkeypatch.setattr(ec, "logger", rec)
    pi.upsert_passages("lassberg-letter-0039", [_record("lassberg-letter-0039")])
    assert pi.passage_count("lassberg-letter-0039") == 1

    written = ec._index_passages("lassberg-letter-0039", {"entities": [
        {"text": "Almosengeberei", "type": "CARE_ACTION",
         "char_start": None, "char_end": None}]})

    assert written == 0
    assert pi.passage_count("lassberg-letter-0039") == 0
    assert not rec.warnings, rec.warnings


# ── the column is an accommodation, not part of the schema ─────────────────

def test_a_new_database_gets_no_content_hash_column(tmp_path):
    """A file this module creates must not grow the legacy column: it is filled
    where an old file still has it, not propagated to every new one."""
    pi._reset_test_db(tmp_path / "passages.db")
    try:
        pi._get_db()
        cols = {r[1] for r in sqlite3.connect(tmp_path / "passages.db").execute(
            "PRAGMA table_info(passages)")}
        assert "content_hash" not in cols
    finally:
        pi._teardown_test_db()
