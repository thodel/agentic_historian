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


def test_unparseable_returns_empty():
    assert ec._loads_entities("total garbage, no json here") == []
    assert ec._loads_entities("") == []


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
