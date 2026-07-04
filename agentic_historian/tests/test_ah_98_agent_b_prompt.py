"""Tests for #98: Agent B prompt produces invalid JSON + contains garbage tokens.

The prompt contained garbage tokens (المفتاحية, 空String) and told the model to
annotate uncertainty with a C-style comment (/* unsicher */) — which is invalid
JSON and corrupts the parse.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_98_agent_b_prompt.py
"""

import json
import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

SRC = (PKG / "agents" / "source_description.py").read_text(encoding="utf-8")


def test_no_non_latin_garbage_tokens():
    """No CJK / Arabic / Cyrillic tokens anywhere in Agent B."""
    garbage = re.findall(r"[一-鿿؀-ۿЀ-ӿ]", SRC)
    assert not garbage, f"garbage tokens still present: {set(garbage)}"


def test_no_json_comment_convention():
    """The prompt must not instruct C-style comments (invalid JSON)."""
    assert "/*" not in SRC and "*/" not in SRC, (
        "prompt still references JSON comments (/* ... */) — invalid JSON"
    )
    assert "JSON erlaubt KEINE Kommentare" in SRC, (
        "prompt should explicitly forbid comments and give a valid alternative"
    )
    assert "(unsicher)" in SRC, "expected the valid string-suffix uncertainty convention"


def test_schema_is_json_serialisable():
    """The embedded schema must serialise to valid JSON (it is dumped into the prompt)."""
    from agents import source_description as sd
    json.loads(json.dumps(sd.SIXTEEN_ELEMENT_SCHEMA))  # round-trips → valid JSON


def test_built_prompt_is_comment_free(monkeypatch, tmp_path):
    """Functional: capture the prompt actually sent to the model and assert it
    carries no JSON comments and no non-Latin garbage."""
    from agents import source_description as sd

    captured = {}

    def _fake_chat_text(prompt, system=None, **kwargs):
        captured.setdefault("prompts", []).append(prompt)
        # a minimal valid "JSON then markdown" reply so describe() completes
        return '{"titel": "Test"}\n\n# Beschreibung\nText.'

    monkeypatch.setattr(sd.gs, "chat_text", _fake_chat_text)
    sd.describe("doc1", "Eine kurze Transkription.")

    assert captured.get("prompts"), "describe did not call chat_text"
    main_prompt = captured["prompts"][0]
    assert "/*" not in main_prompt and "*/" not in main_prompt
    assert not re.search(r"[一-鿿؀-ۿЀ-ӿ]", main_prompt)
