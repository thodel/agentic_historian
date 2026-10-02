"""Q1b: offsets out of the chunking — a passage index needs occurrences (#466).

#395 asks for "character offsets into the selected transcription". The place
that was to supply them had already thrown the information away:

    seen[key] = ent        # key = (text, type)

Two losses in eight lines. The position was never there — `_chunk_text` returned
bare strings, so which chunk produced a hit was unrecorded. And occurrences
became terms: a word standing twelve times in a document yielded one entry, and
an index over such entries is not a passage index.

The acceptance is one line, and it is the only thing that makes an offset
trustworthy:

    transcription[ent["char_start"]:ent["char_end"]] == ent["text"]

Offline — the LLM is a stub. Run from the repo root:
    pytest agentic_historian/tests/test_ah_466_entity_offsets.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import pytest  # noqa: E402

from agents import entity_agent as ec  # noqa: E402


# ── the stub LLM ─────────────────────────────────────────────────────────────

def replies(*entity_lists):
    """A `chat_text` stand-in answering one entity list per chunk, in order.

    Past the given lists it answers with no entities, so a text that chunks
    further than the test cares about does not fall over.
    """
    calls = {"n": 0}

    def fake(prompt, **kw):
        i = calls["n"]
        calls["n"] += 1
        ents = entity_lists[i] if i < len(entity_lists) else []
        return json.dumps({"entities": ents})

    fake.calls = calls
    return fake


def person(text, **kw):
    return {"text": text, "type": "PERSON", "normalised": text,
            "context": "", **kw}


def extract(monkeypatch, transcription, *entity_lists):
    monkeypatch.setattr(ec.gs, "chat_text", replies(*entity_lists))
    return ec._extract_llm(transcription)["entities"]


# ── the acceptance, on every record ─────────────────────────────────────────

TEXT = ("Hans von Wiler hat dem Closter ze Kungsfelt geben. "
        "Hans von Wiler was der jüngere, und Hans von Wiler sigelt.")


def test_every_offset_slices_back_to_its_own_text(monkeypatch):
    """#466's acceptance. The only check that makes an offset worth storing:
    the characters at the position are the characters the record claims."""
    ents = extract(monkeypatch, TEXT, [person("Hans von Wiler"),
                                       person("Kungsfelt", type="PLACE")])

    assert ents, "the fixture must produce entities"
    for ent in ents:
        if ent.get("char_start") is None:
            continue
        assert TEXT[ent["char_start"]:ent["char_end"]] == ent["text"]


def test_a_term_standing_three_times_yields_three_offsets(monkeypatch):
    """The second loss #466 names. One entry for twelve occurrences is not an
    index of occurrences."""
    ents = extract(monkeypatch, TEXT, [person("Hans von Wiler")])

    hits = [e for e in ents if e["text"] == "Hans von Wiler"]
    assert len(hits) == 3
    starts = sorted(e["char_start"] for e in hits)
    assert len(set(starts)) == 3
    assert starts == [TEXT.find("Hans von Wiler"),
                      TEXT.find("Hans von Wiler", 1),
                      TEXT.rfind("Hans von Wiler")]


def test_the_records_carry_the_type_the_model_gave(monkeypatch):
    """Expanding to occurrences must not lose what the entity was."""
    ents = extract(monkeypatch, TEXT, [person("Hans von Wiler"),
                                       person("Kungsfelt", type="PLACE")])

    by_type = {}
    for e in ents:
        by_type.setdefault(e["type"], []).append(e)
    assert len(by_type["PERSON"]) == 3
    assert len(by_type["PLACE"]) == 1


# ── the overlap: seen twice, reported once ──────────────────────────────────

def test_a_hit_in_the_overlap_comes_out_once(monkeypatch):
    """The case #466 says a test must hold. A term inside the 2000 characters
    two chunks share is read by both; it must still be one record.

    Settled by identity rather than arithmetic: both sightings map to the same
    absolute span, so they collapse without anybody working out which chunk
    owns the boundary — which would have been a third copy of the chunking
    constants."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 100)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 40)

    # "Laßberg" placed inside the overlap of chunk 0 ([0,100)) and chunk 1
    # ([60,160)): it starts at 70, so both chunks contain it whole.
    text = "A" * 70 + "Laßberg" + "B" * 123
    chunks = ec._chunk_text(text)
    assert len(chunks) > 1
    containing = [c for c in chunks if "Laßberg" in c.text]
    assert len(containing) == 2, "the fixture must put it in the overlap"

    # Both chunks report it, which is what the real model does.
    ents = extract(monkeypatch, text, [person("Laßberg")], [person("Laßberg")])

    hits = [e for e in ents if e["text"] == "Laßberg"]
    assert len(hits) == 1
    assert hits[0]["char_start"] == 70
    assert text[hits[0]["char_start"]:hits[0]["char_end"]] == "Laßberg"


def test_the_same_term_in_two_different_places_stays_two(monkeypatch):
    """The guard on the test above: collapsing by span must not collapse
    genuinely distinct occurrences into one."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 100)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 40)
    text = "Laßberg" + "A" * 120 + "Laßberg" + "B" * 60

    ents = extract(monkeypatch, text, [person("Laßberg")], [person("Laßberg")],
                   [person("Laßberg")])

    hits = sorted(e["char_start"] for e in ents if e["text"] == "Laßberg")
    assert hits == [0, 127]
    for start in hits:
        assert text[start:start + len("Laßberg")] == "Laßberg"


# ── the chunker reports its own position ────────────────────────────────────

def test_each_chunk_says_where_it_begins():
    """The first bullet of #466. Nothing recomputes a chunk's start from
    `len(chunk) - 2000` any more, which is the coupling where changing the
    chunker makes every offset in the database silently wrong."""
    text = "".join(f"{i:04d}" for i in range(20_000))     # 80 000 chars

    for piece in ec._chunk_text(text):
        assert text[piece.start:piece.end] == piece.text


def test_the_chunker_still_covers_the_whole_text():
    text = "X" * 50_000
    chunks = ec._chunk_text(text)

    assert chunks[0].start == 0
    assert chunks[-1].end == len(text)
    # No gap between consecutive chunks — a gap is a stretch of text no model
    # ever saw, which is the defect #103 opened for.
    for earlier, later in zip(chunks, chunks[1:]):
        assert later.start <= earlier.end


def test_a_short_text_is_one_chunk_at_zero():
    assert ec._chunk_text("Ze Bern gelegen") == [ec.Chunk(0, "Ze Bern gelegen")]


# ── finding the occurrences ─────────────────────────────────────────────────

def test_occurrences_are_exact_and_non_overlapping():
    assert ec.occurrences_in("Hans und Hans und Hansli", "Hans") == [0, 9, 18]
    assert ec.occurrences_in("aaaa", "aa") == [0, 2]      # not [0, 1, 2]
    assert ec.occurrences_in("Bern", "bern") == []        # exact, not folded
    assert ec.occurrences_in("", "Hans") == []
    assert ec.occurrences_in("Hans", "") == []


def test_the_normalised_form_is_not_what_is_searched(monkeypatch):
    """Why the first attempt failed. `chunk.lower().find(text.lower())` asked
    about a form that mostly does not stand in a 15th-century transcription —
    and when it did, it answered with the first occurrence rather than the one
    the model read. The search is for `text`, exactly, because that is what the
    acceptance compares."""
    text = "Hanns von Wyler sigelt"
    ents = extract(monkeypatch, text,
                   [{"text": "Hanns von Wyler", "type": "PERSON",
                     "normalised": "Hans von Wiler", "context": ""}])

    hit = next(e for e in ents if e["text"] == "Hanns von Wyler")
    assert text[hit["char_start"]:hit["char_end"]] == "Hanns von Wyler"
    assert hit["normalised"] == "Hans von Wiler"          # kept, not searched


# ── an entity with no verbatim position is kept, not dropped ───────────────

def test_an_entity_that_does_not_stand_in_the_text_survives_without_offsets(
        monkeypatch):
    """#466's third acceptance point: today's output must still come out.

    The model sometimes answers with a corrected or expanded form that is not
    in the text. The first attempt let `find` return -1 and the record vanished
    without a word. It is kept, with no position — because a position that was
    not found is the one thing that must not be invented."""
    text = "Ze Bern gelegen"
    ents = extract(monkeypatch, text, [person("Berne, Stadt")])

    ghost = next(e for e in ents if e["text"] == "Berne, Stadt")
    assert ghost["char_start"] is None and ghost["char_end"] is None
    assert ghost["located"] is False
    assert ghost["type"] == "PERSON"


def test_an_unlocatable_entity_is_reported_not_silent(monkeypatch, caplog):
    """It cannot enter the passage store (`char_start INTEGER NOT NULL`), so
    without a line in the log the corpus would simply be one entity short."""
    from loguru import logger as loguru_logger

    records: list[str] = []
    sink = loguru_logger.add(lambda m: records.append(m), level="INFO")
    try:
        extract(monkeypatch, "Ze Bern gelegen", [person("Berne, Stadt")])
    finally:
        loguru_logger.remove(sink)

    assert any("ohne wörtliche Fundstelle" in r for r in records)


def test_a_located_and_an_unlocated_entity_coexist(monkeypatch):
    text = "Hans von Wiler ze Bern"
    ents = extract(monkeypatch, text,
                   [person("Hans von Wiler"), person("Berne, Stadt")])

    located = [e for e in ents if e.get("located")]
    unlocated = [e for e in ents if e.get("located") is False]
    assert len(located) == 1 and len(unlocated) == 1
    assert text[located[0]["char_start"]:located[0]["char_end"]] == "Hans von Wiler"


# ── what the records claim, and what they do not ───────────────────────────

def test_positions_are_only_claimed_in_the_chunk_the_model_read(monkeypatch):
    """A record is two claims of different kinds: the *type* is the model's
    judgement on a term it read in that chunk, the *positions* are found by
    matching characters. So the search stays inside the chunk the entity came
    from — the same characters in a chunk the model never saw carry no
    judgement, and claiming a position there would assert one nobody made."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 60)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 10)
    # "Wiler" twice, far enough apart to sit in chunks 0 and 2 with nothing in
    # the overlap.
    text = "Wiler" + "A" * 80 + "Wiler" + "B" * 40
    chunks = ec._chunk_text(text)
    assert sum("Wiler" in c.text for c in chunks) >= 2

    # Only the FIRST chunk reports it; later chunks answer with nothing.
    ents = extract(monkeypatch, text, [person("Wiler")], [], [], [], [])

    hits = [e for e in ents if e["text"] == "Wiler"]
    assert [e["char_start"] for e in hits] == [0], \
        "a position was claimed in a chunk the model reported nothing for"


def test_the_preference_for_a_record_with_context_is_kept(monkeypatch):
    """The old rule was not simply "last wins" — an entry carrying `context`
    beat one without, and that preference is worth keeping per occurrence."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 100)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 40)
    text = "A" * 70 + "Laßberg" + "B" * 123

    ents = extract(monkeypatch, text,
                   [person("Laßberg")],                             # no context
                   [person("Laßberg", context="auf Eppishausen")])   # with

    hit = next(e for e in ents if e["text"] == "Laßberg")
    assert hit["context"] == "auf Eppishausen"


# ── nothing that worked before stops working ───────────────────────────────

def test_an_empty_transcription_yields_nothing(monkeypatch):
    assert extract(monkeypatch, "", []) == []


def test_a_chunk_the_model_fails_on_does_not_cost_the_others(monkeypatch):
    """Pre-existing behaviour, re-pinned because `_extract_llm` was rewritten
    around it: one chunk's failure must not take the run with it."""
    text = "Hans von Wiler ze Bern gelegen"
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gateway 500")
        return json.dumps({"entities": [person("Hans von Wiler")]})

    monkeypatch.setattr(ec.gs, "chat_text", fake)
    # One chunk, so the failure is the whole extraction — it must return a
    # shape rather than raise.
    out = ec._extract_llm(text)

    assert out == {"entities": []}


@pytest.mark.parametrize("bad", [
    {"type": "PERSON"},                      # no text at all
    {"text": "", "type": "PERSON"},          # empty text
])
def test_an_entity_without_text_does_not_crash_the_extraction(monkeypatch, bad):
    """`occurrences_in` with an empty needle would otherwise loop for ever or
    match at every position."""
    ents = extract(monkeypatch, "Ze Bern gelegen", [bad])

    assert all(e.get("char_start") is None for e in ents)
