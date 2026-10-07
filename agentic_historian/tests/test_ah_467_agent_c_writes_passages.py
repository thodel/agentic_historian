"""Q1c: Agent C writes into the passage index — the vocabulary types only (#467).

#395 built the store, #466 made the offsets real. This wires the two together,
and the issue names three things a first attempt got wrong, so each has a test
that fails if it comes back:

* It wrote **every** type, and then pinned that in a test. Only `VOCAB_TYPES`
  belong in a passage index — `PERSON`, `PLACE`, `ORG` and `DATE` link to a
  person or place register instead.
* It added a `reset_doc` call inside `_save`. `upsert_passages` already deletes
  the document's rows, and a deletion hidden in a function that writes two files
  is where a re-run quietly loses passages.
* It set `ent["_doc_id"]` and `ent["_page"]` on the entity dicts — which are
  serialised to `{doc_id}_entities.json`, so the internal keys reached the
  published file format.

Offline — the LLM is a stub and the index is a temp SQLite file.
    pytest agentic_historian/tests/test_ah_467_agent_c_writes_passages.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest  # noqa: E402

import passage_index as pi  # noqa: E402
from agents import entity_agent as ec  # noqa: E402


# ── fixtures ─────────────────────────────────────────────────────────────────

#: Care and taxonomy terms, each standing verbatim in the text. The two `almosen`
#: make the occurrence question concrete: one term, two rows.
TEXT = ("Der vogt gab almosen den armen lüten ze Kungsfelt, "
        "und der schultheiss gab almosen aber nicht den vaganten.")


def vocab(text, kind, **kw):
    return {"text": text, "type": kind, "normalised": text, "context": "", **kw}


@pytest.fixture
def index(tmp_path, monkeypatch):
    """A passage index in a temp file, torn down after the test."""
    pi._reset_test_db(tmp_path / "passages.db")
    monkeypatch.setattr(ec.config, "OUTPUTS_DIR", tmp_path / "out")
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    try:
        yield pi
    finally:
        pi._teardown_test_db()


def run(monkeypatch, doc_id, transcription, entities, *, page=1):
    """One Agent C pass with a stubbed LLM and no hub lookups."""
    monkeypatch.setattr(ec.gs, "chat_text",
                        lambda prompt, **kw: json.dumps({"entities": entities}))
    monkeypatch.setattr(ec.hub, "match_vocabulary", lambda term: None)
    return ec.extract_entities(doc_id, transcription, page=page)


# ── acceptance 1: n hits of the four types in, n rows out ──────────────────

def test_the_vocabulary_hits_become_rows_that_slice_back(index, monkeypatch):
    """#467's first acceptance point, both halves: the count matches, and every
    row's offsets cut the text it claims out of the transcription."""
    run(monkeypatch, "doc-a", TEXT, [
        vocab("vogt", "ROLE"),
        vocab("almosen", "CARE_ACTION"),
        vocab("armen lüten", "SOCIAL_GROUP"),
        vocab("schultheiss", "ROLE"),
    ])

    rows = index.query_passages(doc_id="doc-a")
    # `almosen` stands twice, so five hits from four terms (#466).
    assert len(rows) == 5
    assert index.passage_count("doc-a") == 5
    for row in rows:
        assert TEXT[row["char_start"]:row["char_end"]] == row["text"]


def test_one_term_standing_twice_is_two_rows(index, monkeypatch):
    """The reason #466 had to come first: an index of terms is not an index of
    passages."""
    run(monkeypatch, "doc-a", TEXT, [vocab("almosen", "CARE_ACTION")])

    rows = index.query_passages(doc_id="doc-a", entity_type="CARE_ACTION")
    assert len(rows) == 2
    assert sorted(r["char_start"] for r in rows) == [
        TEXT.find("almosen"), TEXT.rfind("almosen")]


def test_the_row_carries_the_type_and_the_normalised_form(index, monkeypatch):
    run(monkeypatch, "doc-a", TEXT,
        [{"text": "lüten", "type": "SOCIAL_GROUP",
          "normalised": "Leute", "context": "die armen"}])

    row = index.query_passages(doc_id="doc-a")[0]
    assert row["entity_type"] == "SOCIAL_GROUP"
    assert row["text"] == "lüten" and row["normalised"] == "Leute"
    assert row["context"] == "die armen"
    assert row["page"] == 1


def test_the_page_is_what_the_caller_said(index, monkeypatch):
    """`page` is `NOT NULL` and defaults to 1 because a doc_id carries one
    transcription today — but a caller that knows better must be able to say
    so, rather than every row claiming page 1."""
    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")], page=7)

    assert {r["page"] for r in index.query_passages(doc_id="doc-a")} == {7}


# ── acceptance 2: no other type produces a row ─────────────────────────────

def test_other_types_in_the_same_run_write_nothing(index, monkeypatch):
    """The first attempt wrote every type and pinned it. PERSON, PLACE, ORG and
    DATE link to a register, not to a passage index."""
    run(monkeypatch, "doc-a", TEXT, [
        vocab("vogt", "ROLE"),                       # in
        vocab("Kungsfelt", "PLACE"),                 # out
        vocab("schultheiss", "PERSON"),              # out
        vocab("vaganten", "ORG"),                    # out
        vocab("armen", "DATE"),                      # out
    ])

    rows = index.query_passages(doc_id="doc-a")
    assert [r["entity_type"] for r in rows] == ["ROLE"]
    assert {r["entity_type"] for r in rows} <= ec.VOCAB_TYPES


def test_a_run_with_no_vocabulary_hits_writes_no_rows(index, monkeypatch):
    run(monkeypatch, "doc-a", TEXT, [vocab("Kungsfelt", "PLACE")])

    assert index.passage_count("doc-a") == 0


# ── acceptance 3 + 4: idempotent, and no corpses ───────────────────────────

def test_the_same_run_twice_leaves_the_same_count(index, monkeypatch):
    """#467's third point. `upsert_passages` deletes the doc's rows first, so a
    second pass replaces rather than doubles."""
    ents = [vocab("vogt", "ROLE"), vocab("almosen", "CARE_ACTION")]
    run(monkeypatch, "doc-a", TEXT, ents)
    first = index.passage_count("doc-a")

    run(monkeypatch, "doc-a", TEXT, ents)

    assert index.passage_count("doc-a") == first == 3


def test_a_second_run_with_fewer_hits_leaves_only_the_new_rows(index, monkeypatch):
    """#467's fourth point, and the one a `reset_doc` inside `_save` was meant
    to solve — it is already solved by the upsert, which is why the extra call
    was both redundant and a deletion in the wrong place."""
    run(monkeypatch, "doc-a", TEXT,
        [vocab("vogt", "ROLE"), vocab("almosen", "CARE_ACTION"),
         vocab("armen lüten", "SOCIAL_GROUP")])
    assert index.passage_count("doc-a") == 4

    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")])

    rows = index.query_passages(doc_id="doc-a")
    assert len(rows) == 1
    assert [r["entity_type"] for r in rows] == ["ROLE"]
    assert not any(r["text"] == "almosen" for r in rows), "corpse from run one"


def test_one_document_rewritten_does_not_touch_another(index, monkeypatch):
    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")])
    run(monkeypatch, "doc-b", TEXT, [vocab("almosen", "CARE_ACTION")])

    run(monkeypatch, "doc-a", TEXT, [])

    assert index.passage_count("doc-a") == 0
    assert index.passage_count("doc-b") == 2


# ── acceptance 5: no internal keys in the published file ───────────────────

def test_the_entities_json_has_no_underscore_keys(index, monkeypatch, tmp_path):
    """The first attempt set `_doc_id` and `_page` on the entity dicts, which
    are the same dicts this file serialises."""
    out = run(monkeypatch, "doc-a", TEXT,
              [vocab("vogt", "ROLE"), vocab("Kungsfelt", "PLACE")])

    written = json.loads(
        (tmp_path / "out" / "doc-a_entities.json").read_text(encoding="utf-8"))
    for ent in written["entities"]:
        offenders = [k for k in ent if k.startswith("_")]
        assert not offenders, f"internal keys reached the file: {offenders}"

    # And in what the caller gets back. `_save` runs *before* the indexing
    # step, so the file alone cannot catch a key set during indexing — but
    # `ingest.py` keeps the returned dicts in the run state, where they are
    # serialised again later.
    for ent in out["entities"]:
        offenders = [k for k in ent if k.startswith("_")]
        assert not offenders, f"internal keys in the returned entities: {offenders}"


def test_building_the_rows_does_not_mutate_the_entities():
    """Where the guarantee actually lives: the records are new dicts.

    The entity must be one that gets a row — a vocabulary type *with* offsets.
    My first version of this test passed `vocab("vogt", "ROLE")` without
    `char_start`, so it was filtered out before the function touched it and the
    test would have passed with `ent["_doc_id"] = doc_id` right there in the
    loop. Checked by mutating the real thing.
    """
    ents = [dict(vocab("vogt", "ROLE"), char_start=4, char_end=8)]
    before = [dict(e) for e in ents]

    rows = ec.passage_records("doc-a", ents, page=3)

    assert rows, "the fixture must produce a row, or nothing is being tested"
    assert ents == before
    assert not any(k.startswith("_") for e in ents for k in e)


# ── an unlocated entity has no row, and keeps its place in the file ───────

def test_an_entity_without_offsets_is_not_a_passage(index, monkeypatch, tmp_path):
    """`char_start` is `NOT NULL`, so an entity whose text does not stand in the
    transcription (#466) has no row to write. It stays in the JSON — it simply
    is not a passage, because nobody can point at where it is."""
    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE"),
                                     vocab("Almosengeberei", "CARE_ACTION")])

    rows = index.query_passages(doc_id="doc-a")
    assert [r["entity_type"] for r in rows] == ["ROLE"]

    written = json.loads(
        (tmp_path / "out" / "doc-a_entities.json").read_text(encoding="utf-8"))
    ghost = next(e for e in written["entities"]
                 if e["text"] == "Almosengeberei")
    assert ghost["char_start"] is None and ghost["located"] is False


# ── the controlled-vocabulary link ────────────────────────────────────────

def test_the_vocabulary_term_lands_in_hub_id(index, monkeypatch):
    """`hub.match_vocabulary` returns the canonical vocabulary entry, which
    within that vocabulary is what identifies it — and it is what makes
    `ix_passages_hub_id` mean anything."""
    monkeypatch.setattr(ec.gs, "chat_text",
                        lambda prompt, **kw: json.dumps(
                            {"entities": [vocab("almosen", "CARE_ACTION")]}))
    monkeypatch.setattr(ec.hub, "match_vocabulary",
                        lambda term: "almosen" if term else None)

    ec.extract_entities("doc-a", TEXT)

    assert {r["hub_id"] for r in index.query_passages(doc_id="doc-a")} == {"almosen"}


def test_a_mention_matching_no_vocabulary_entry_is_not_an_error(index, monkeypatch):
    """The ordinary case for a term nobody has added yet. None, and a row."""
    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")])

    rows = index.query_passages(doc_id="doc-a")
    assert len(rows) == 1 and rows[0]["hub_id"] is None


# ── the write is a step of its own, and never costs the extraction ────────

def test_an_index_that_cannot_be_written_does_not_lose_the_entities(
        index, monkeypatch, tmp_path):
    """On the same terms as the run manifest: a document whose entities were
    extracted must not be lost to a database that refused. Logged, not raised."""
    def refuse(doc_id, records):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ec.passage_index, "upsert_passages", refuse)

    out = run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")])

    assert any(e["text"] == "vogt" for e in out["entities"])
    assert (tmp_path / "out" / "doc-a_entities.json").is_file()


def test_the_index_write_is_not_hidden_inside_save(index, monkeypatch, tmp_path):
    """`_save` writes two files and is named for it. The passage write is its
    own step, because `upsert_passages` **deletes** the document's rows first
    and a deletion nobody reads for deletions is how a re-run loses data.

    Pinned by behaviour: `_save` alone must write no rows.
    """
    ec._save("doc-a", {"entities": [dict(vocab("vogt", "ROLE"),
                                         char_start=9, char_end=13,
                                         located=True)]}, TEXT)

    assert index.passage_count("doc-a") == 0
    assert (tmp_path / "out" / "doc-a_entities.json").is_file()


def test_no_separate_reset_is_needed_because_the_upsert_deletes(index, monkeypatch):
    """The redundancy stated as a test: writing an empty set clears the doc, so
    nothing else has to."""
    run(monkeypatch, "doc-a", TEXT, [vocab("vogt", "ROLE")])
    assert index.passage_count("doc-a") == 1

    assert ec._index_passages("doc-a", {"entities": []}) == 0
    assert index.passage_count("doc-a") == 0


# ── the record builder on its own ─────────────────────────────────────────

def test_passage_records_filters_and_maps_without_a_database():
    """Pure, so the mapping can be checked without SQLite in the way."""
    rows = ec.passage_records("doc-x", [
        {"text": "vogt", "type": "ROLE", "normalised": "Vogt",
         "char_start": 4, "char_end": 8, "context": "Der"},
        {"text": "Bern", "type": "PLACE", "char_start": 0, "char_end": 4},
        {"text": "weg", "type": "ROLE", "char_start": None, "char_end": None},
    ], page=2)

    assert len(rows) == 1
    assert rows[0] == {
        "doc_id": "doc-x", "page": 2, "bestand": None, "entity_type": "ROLE",
        "text": "vogt", "normalised": "Vogt",
        "char_start": 4, "char_end": 8, "context": "Der",
        "hub_id": None, "gnd_id": None, "hls_id": None,
    }


def test_a_missing_normalised_falls_back_to_the_text():
    """The store requires one; the model does not always give one."""
    rows = ec.passage_records("doc-x", [
        {"text": "vogt", "type": "ROLE", "char_start": 0, "char_end": 4},
    ])

    assert rows[0]["normalised"] == "vogt"


def test_the_records_carry_every_field_the_store_requires():
    """Held against `upsert_passages`' own validation set, so the two cannot
    drift apart without this failing."""
    required = {"doc_id", "page", "entity_type", "text", "normalised",
                "char_start", "char_end"}
    rows = ec.passage_records("doc-x", [
        {"text": "vogt", "type": "ROLE", "char_start": 0, "char_end": 4},
    ])

    assert required <= set(rows[0])


# ── the holding (#396's migration) ─────────────────────────────────────────

def test_the_holding_is_a_parameter_not_derived_in_the_store():
    """The caller knows. A rule applied here would turn the first doc_id that
    does not follow the convention into wrong data instead of a missing value."""
    rows = ec.passage_records("Marbach__letter-0001__001", [
        dict(vocab("vogt", "ROLE"), char_start=0, char_end=4),
    ], bestand="Marbach")

    assert rows[0]["bestand"] == "Marbach"


def test_no_holding_given_is_none_not_a_guess():
    """Even where the doc_id plainly carries one. None means nobody said."""
    rows = ec.passage_records("Marbach__letter-0001__001", [
        dict(vocab("vogt", "ROLE"), char_start=0, char_end=4),
    ])

    assert rows[0]["bestand"] is None


def test_the_convention_is_available_to_a_caller_that_wants_it():
    """`PageRef.key` folds a page's path on `__`, so a batch-minted id begins
    with its holding. Offered as a helper, not applied by the store."""
    assert ec.bestand_from_doc_id("Marbach__letter-0001__001") == "Marbach"
    assert ec.bestand_from_doc_id("Inzigkofen__Ms-321__014") == "Inzigkofen"


def test_a_single_segment_id_has_no_holding():
    """One segment is a document with no holding above it, not a holding."""
    assert ec.bestand_from_doc_id("doc-a") is None
    assert ec.bestand_from_doc_id("") is None
    assert ec.bestand_from_doc_id("__") is None


def test_the_holding_reaches_the_index(index, monkeypatch):
    run(monkeypatch, "Marbach__01", TEXT, [vocab("vogt", "ROLE")])
    ec.extract_entities("Marbach__01", TEXT, bestand="Marbach")

    rows = index.query_passages(bestand="Marbach")
    assert rows and all(r["doc_id"] == "Marbach__01" for r in rows)


def test_one_holding_does_not_answer_for_another(index, monkeypatch):
    monkeypatch.setattr(ec.gs, "chat_text",
                        lambda prompt, **kw: json.dumps(
                            {"entities": [vocab("vogt", "ROLE")]}))
    monkeypatch.setattr(ec.hub, "match_vocabulary", lambda term: None)
    ec.extract_entities("Marbach__01", TEXT, bestand="Marbach")
    ec.extract_entities("Inzigkofen__01", TEXT, bestand="Inzigkofen")

    assert len(index.query_passages(bestand="Marbach")) == 1
    assert len(index.query_passages(bestand="Inzigkofen")) == 1
    assert len(index.query_passages()) == 2
