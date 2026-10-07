"""Agent C tolerates malformed NER JSON (repair + salvage + one retry).

Previously a single ``json.loads`` failure dropped a whole chunk's entities —
a page full of names could yield 0 entities on one bad delimiter. Offline.
Run from the repo root:
    pytest agentic_historian/tests/test_ah_196_agentc_json_robustness.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
config.ensure_dirs()

from agents import entity_agent as ec  # noqa: E402


# ── _loads_entities: repair / salvage ────────────────────────────────────────

def test_clean_json():
    assert ec._loads_entities('{"entities": [{"text": "Bern", "type": "PLACE"}]}') \
        == [{"text": "Bern", "type": "PLACE"}]


def test_code_fenced():
    raw = '```json\n{"entities": [{"text": "Bern", "type": "PLACE"}]}\n```'
    assert len(ec._loads_entities(raw)) == 1


def test_prose_around_object():
    raw = 'Hier:\n{"entities": [{"text":"Bern","type":"PLACE"}]}\nFertig.'
    assert ec._loads_entities(raw)[0]["text"] == "Bern"


def test_trailing_comma_repaired():
    raw = '{"entities": [{"text":"Bern","type":"PLACE"},]}'
    assert len(ec._loads_entities(raw)) == 1


def test_truncated_salvages_complete_objects():
    """A reply cut off mid-object still yields the complete ones."""
    raw = ('{"entities": [{"text":"Bern","type":"PLACE"},'
           '{"text":"Thun","type":"PLACE"},{"text":"Hans","typ')
    names = {e["text"] for e in ec._loads_entities(raw)}
    assert {"Bern", "Thun"} <= names


def test_bare_list_accepted():
    assert len(ec._loads_entities('[{"text":"Bern","type":"PLACE"}]')) == 1


def test_unparseable_returns_none_not_an_empty_list():
    """Changed by #546, and the change is the point.

    This asserted `== []`, which is what made "the model answered, there is
    nothing here" and "the model did not answer" the same value — and so made
    `_extract_llm` retry on both. `None` is "did not parse"; `[]` is now
    reserved for a document that parsed and declared no entities.
    """
    assert ec._loads_entities("total garbage, no json here") is None
    assert ec._loads_entities("") is None
    assert ec._loads_entities("```json\n```") is None


def test_a_valid_empty_answer_is_an_empty_list():
    """The other side of the distinction: this must NOT be None, or a chunk
    with nothing in it would be retried for ever."""
    assert ec._loads_entities('{"entities": []}') == []
    assert ec._loads_entities("[]") == []


def test_a_reply_of_the_wrong_shape_did_not_parse():
    """A document that reads but is not the requested format is exactly what a
    format nudge fixes, so it counts as "did not parse"."""
    assert ec._loads_entities('{"foo": 1}') is None
    assert ec._loads_entities('{"entities": "nicht eine Liste"}') is None


# ── retry path in _extract_llm ───────────────────────────────────────────────

def test_retry_recovers_after_bad_first_reply(monkeypatch):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return "oops not json" if calls["n"] == 1 \
            else '{"entities":[{"text":"Bern","type":"PLACE"}]}'

    monkeypatch.setattr(ec.gs, "chat_text", fake)
    out = ec._extract_llm("Ze Bern gelegen ...")
    assert calls["n"] == 2  # retried exactly once
    assert any(e["text"] == "Bern" for e in out["entities"])


def test_no_retry_when_first_reply_parses(monkeypatch):
    calls = {"n": 0}

    def fake(prompt, **kw):
        calls["n"] += 1
        return '{"entities":[{"text":"Bern","type":"PLACE"}]}'

    monkeypatch.setattr(ec.gs, "chat_text", fake)
    ec._extract_llm("Ze Bern gelegen ...")
    assert calls["n"] == 1  # no wasted retry on a good reply


# ── #546: an entity-free chunk is not a parse failure ──────────────────────

def _counting(*replies):
    """A `chat_text` stand-in answering `replies` in order, counting calls."""
    calls = {"n": 0, "prompts": []}

    def fake(prompt, **kw):
        i = calls["n"]
        calls["n"] += 1
        calls["prompts"].append(prompt)
        return replies[i] if i < len(replies) else replies[-1]

    fake.calls = calls
    return fake


def test_an_empty_answer_is_not_asked_again(monkeypatch):
    """The whole of #546. A chunk with nothing in it answers
    `{"entities": []}` — correct, complete work — and used to cost a second
    full call because that was indistinguishable from rubbish."""
    fake = _counting('{"entities": []}')
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    out = ec._extract_llm("Ze Bern gelegen, ohne Namen darin.")

    assert fake.calls["n"] == 1
    assert out == {"entities": []}


def test_an_unparsable_answer_is_still_asked_again(monkeypatch):
    """The safety net stays a safety net — #196's reason for it is unchanged:
    one `json.loads` failure used to drop a page full of names."""
    fake = _counting("oops not json",
                     '{"entities":[{"text":"Bern","type":"PLACE"}]}')
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    out = ec._extract_llm("Ze Bern gelegen ...")

    assert fake.calls["n"] == 2
    assert any(e["text"] == "Bern" for e in out["entities"])
    assert "AUSSCHLIESSLICH" in fake.calls["prompts"][1], "the nudge was sent"


def test_a_wrong_shape_is_asked_again(monkeypatch):
    """A document that reads but is not the requested format is precisely what
    the nudge fixes."""
    fake = _counting('{"foo": 1}',
                     '{"entities":[{"text":"Bern","type":"PLACE"}]}')
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    ec._extract_llm("Ze Bern gelegen ...")

    assert fake.calls["n"] == 2


def test_an_empty_reply_is_not_asked_again(monkeypatch):
    """Unchanged behaviour, re-pinned: the condition requires `raw.strip()`,
    so a silent model is not nudged."""
    fake = _counting("")
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    ec._extract_llm("Ze Bern gelegen ...")

    assert fake.calls["n"] == 1


def test_a_salvaged_object_is_not_asked_again(monkeypatch):
    """A truncated tail the salvage pass rescued did carry entities, so there
    is nothing to ask about."""
    fake = _counting('{"entities": [{"text":"Bern","type":"PLACE"},{"text":"Th')
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    out = ec._extract_llm("Ze Bern gelegen ...")

    assert fake.calls["n"] == 1
    assert any(e["text"] == "Bern" for e in out["entities"])


def test_n_chunks_of_which_k_are_empty_cost_n_calls(monkeypatch):
    """#546's acceptance. A document of n chunks with k empty ones used to
    cost n + k calls; the k were the ones the model got right."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 100)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 20)
    text = "".join(f"<<{i:04d}>>" + "x" * 90 for i in range(4))
    chunks = ec._chunk_text(text)
    assert len(chunks) > 2, "the fixture must split"

    fake = _counting('{"entities": []}')
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    ec._extract_llm(text)

    assert fake.calls["n"] == len(chunks)


def test_an_unparsable_chunk_among_empty_ones_costs_exactly_one_extra(monkeypatch):
    """The two cases in one document, so the saving cannot come from having
    switched the retry off altogether."""
    monkeypatch.setattr(ec, "CHUNK_SIZE", 100)
    monkeypatch.setattr(ec, "CHUNK_OVERLAP", 20)
    text = "".join(f"<<{i:04d}>>" + "y" * 90 for i in range(4))
    chunks = ec._chunk_text(text)

    replies = ['{"entities": []}'] * (2 * len(chunks))
    replies[1] = "not json at all"          # the second chunk's first try
    fake = _counting(*replies)
    monkeypatch.setattr(ec.gs, "chat_text", fake)

    ec._extract_llm(text)

    assert fake.calls["n"] == len(chunks) + 1
