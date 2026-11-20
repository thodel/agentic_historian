"""`index-passages`: rebuild passages from the saved entities, without a model (#892).

#895 made the insert work against tei's #395-prototype database, but the two
letters whose write had failed stayed out of the index: `lassberg-letter-0036`
and `-0048` lost 3 and 5 passages (2 ROLE + 1 SOCIAL_GROUP; 4 ROLE + 1
SOCIAL_GROUP, all located). Their `_entities.json` holds everything a passage
row needs, so re-running the pipeline — and the model — is not needed.

The three letters that logged `upserted 0 passage(s)` (0041, 0053, 0054) were
right to: their published entities are PERSON, PLACE, DATE and ORG only, none
of `VOCAB_TYPES` (counted 20.11.2026 in agentic-historian-outputs). Not «QA
0.00», which belonged to the two failing letters.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_892_index_passages_cmd.py
"""

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest

import passage_index as pi
from agents import entity_agent as ec

PROTO = importlib.util.spec_from_file_location(
    "t892_proto", Path(__file__).parent / "test_ah_892_legacy_content_hash.py")
_proto = importlib.util.module_from_spec(PROTO)
PROTO.loader.exec_module(_proto)

_spec = importlib.util.spec_from_file_location("ah_cli_892", PKG / "__main__.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)


def _ent(text, typ, start):
    return {"text": text, "type": typ, "normalised": text.capitalize(),
            "char_start": start, "char_end": start + len(text), "context": f"… {text} …"}


#: Shaped like the published letters: 0036 with three located vocabulary
#: entities, 0041 with persons and places only.
LETTERS = {
    "lassberg-letter-0036": {"entities": [
        _ent("Pfarrer", "ROLE", 10), _ent("Bibliothekar", "ROLE", 40),
        _ent("Mönche", "SOCIAL_GROUP", 80), _ent("Laßberg", "PERSON", 0),
        _ent("Meersburg", "PLACE", 120),
        {"text": "Abt", "type": "ROLE", "normalised": "Abt"},   # not located: no row
    ]},
    "lassberg-letter-0041": {"entities": [
        _ent("Uhland", "PERSON", 5), _ent("Tübingen", "PLACE", 30),
        _ent("1821", "DATE", 50)]},
}


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    """tei's prototype passages.db, and an OUTPUTS_DIR with the letters' entities."""
    db = tmp_path / "passages.db"
    con = sqlite3.connect(db)
    con.executescript(_proto.PROTOTYPE_SCHEMA)
    con.close()
    pi._reset_test_db(db)
    out = tmp_path / "out"
    out.mkdir()
    for doc_id, data in LETTERS.items():
        (out / f"{doc_id}_entities.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cli.config, "OUTPUTS_DIR", out)
    monkeypatch.setattr(ec.config, "OUTPUTS_DIR", out)
    try:
        yield out
    finally:
        pi._teardown_test_db()


def _run(*doc_ids, all=False):
    return cli.index_passages_cmd(argparse.Namespace(doc_ids=list(doc_ids), all=all))


def test_a_letter_whose_write_failed_gets_its_passages(outputs, capsys):
    assert _run("lassberg-letter-0036") == 0
    rows = pi.query_passages(doc_id="lassberg-letter-0036")
    assert sorted(r["text"] for r in rows) == ["Bibliothekar", "Mönche", "Pfarrer"]
    assert {r["page"] for r in rows} == {1} and {r["bestand"] for r in rows} == {None}
    out = capsys.readouterr().out
    assert "lassberg-letter-0036: 3 passage(s)" in out
    assert "embed-passages" in out


def test_the_prototype_hash_is_filled_like_a_pipeline_write(outputs):
    _run("lassberg-letter-0036")
    con = sqlite3.connect(outputs.parent / "passages.db")
    hashes = dict(con.execute("SELECT text, content_hash FROM passages").fetchall())
    con.close()
    e = LETTERS["lassberg-letter-0036"]["entities"][0]
    assert hashes["Pfarrer"] == _proto._proto_hash("lassberg-letter-0036", e["char_start"],
                                                   e["char_end"], "Pfarrer")


def test_a_letter_without_vocabulary_entities_has_zero_and_that_is_no_failure(outputs, capsys):
    assert _run("lassberg-letter-0041") == 0
    assert "lassberg-letter-0041: 0 passage(s)" in capsys.readouterr().out
    assert pi.query_passages(doc_id="lassberg-letter-0041") == []


def test_all_takes_every_entities_file_and_is_idempotent(outputs, capsys):
    assert _run(all=True) == 0
    assert _run(all=True) == 0
    assert len(pi.query_passages(doc_id="lassberg-letter-0036")) == 3
    assert "documents : 2, 0 failed" in capsys.readouterr().out


def test_a_missing_or_broken_file_fails_without_stopping_the_others(outputs, capsys):
    (outputs / "lassberg-letter-0099_entities.json").write_text("{nope", encoding="utf-8")
    assert _run("lassberg-letter-0098", "lassberg-letter-0099", "lassberg-letter-0036") == 1
    out = capsys.readouterr().out
    assert "lassberg-letter-0098: cannot read" in out and "lassberg-letter-0099: cannot read" in out
    assert "lassberg-letter-0036: 3 passage(s)" in out and "documents : 3, 2 failed" in out


def test_a_write_that_fails_is_said_and_exits_1(outputs, monkeypatch, capsys):
    def refuse(doc_id, records):
        raise sqlite3.IntegrityError("NOT NULL constraint failed: passages.content_hash")
    monkeypatch.setattr(pi, "upsert_passages", refuse)
    assert _run("lassberg-letter-0036") == 1
    assert "lassberg-letter-0036: FAILED — 3 passage(s) to write, 0 in the index" in capsys.readouterr().out


def test_nothing_named_is_an_error(outputs, capsys):
    assert _run() == 1
    assert "name DOC_IDs or pass --all" in capsys.readouterr().out


def test_the_command_is_registered():
    args = cli.build_parser().parse_args(["index-passages", "lassberg-letter-0036", "--all"])
    assert args.func is cli.index_passages_cmd and args.all and args.doc_ids == ["lassberg-letter-0036"]
