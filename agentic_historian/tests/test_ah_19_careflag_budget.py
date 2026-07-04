"""Tests for #19: raise the care-flag token budget and parse defensively.

_care_flag used max_tokens=800 (too small for the gpt-oss reasoning model, which
spends tokens on reasoning_content before the JSON -> truncated null) and did a
bare json.loads(raw) that broke on any wrapping text.

Offline: gs.chat_text is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_19_careflag_budget.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

SRC = (PKG / "agents" / "source_description.py").read_text(encoding="utf-8")


def test_budget_raised_above_800():
    from agents import source_description as sd
    import inspect
    body = inspect.getsource(sd._care_flag)
    assert "max_tokens=800" not in body, "care-flag budget must be raised above 800"
    assert "max_tokens=2500" in body, "care-flag should use a generous budget (2500)"


def test_parses_json_wrapped_in_reasoning_text(monkeypatch):
    """A reasoning model wraps the JSON in prose/fences — must still parse."""
    from agents import source_description as sd

    wrapped = (
        "Ich denke ueber das Dokument nach...\n"
        '```json\n{"is_care_related": true, "care_context": "Spital", '
        '"care_types": ["almosen"], "beteiligte": ["arme luet"]}\n```\n'
        "Fertig."
    )
    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: wrapped)
    out = sd._care_flag("Ein langer Text.")
    assert out["is_care_related"] is True
    assert out["care_context"] == "Spital"
    assert out["care_types"] == ["almosen"]
    assert out["beteiligte"] == ["arme luet"]


def test_defensive_defaults_on_empty_or_bad(monkeypatch):
    """Empty/None/garbage response -> the full default dict, never a crash."""
    from agents import source_description as sd

    for bad in ("", None, "kein json hier", "{broken"):
        monkeypatch.setattr(sd.gs, "chat_text", lambda *a, _b=bad, **k: _b)
        out = sd._care_flag("text")
        assert out == {"is_care_related": False, "care_context": "",
                       "care_types": [], "beteiligte": []}


def test_normalises_missing_keys(monkeypatch):
    """Partial JSON still yields all four keys with correct types."""
    from agents import source_description as sd
    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: '{"is_care_related": true}')
    out = sd._care_flag("text")
    assert out["is_care_related"] is True
    assert out["care_context"] == "" and out["care_types"] == [] and out["beteiligte"] == []
