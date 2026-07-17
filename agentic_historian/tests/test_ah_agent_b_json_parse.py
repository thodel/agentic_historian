"""Agent B must not drop its JSON when the markdown has no "## " heading.

`_parse_response` guarded with `not md_start`, where `md_start = raw.find("## ")`.
find() returns **-1** when there is no heading, and -1 is truthy — so `not -1` is
False, and the `json_start.start() < md_start` fallback compared against -1 and was
False too. Both branches missed: the whole 16-element JSON was silently discarded.

Consequence: source_json={} → SourceCriteria empty → the model selector falls back
to a generic model (measured on tei: score 0.00, "no match"). Whether a run got its
criteria depended on whether the LLM happened to emit a "## " heading — which is
why it was invisible: the run that was traced did emit one.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_agent_b_json_parse.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents.source_description import _parse_response  # noqa: E402

JSON = '{"Schrift": {"wert": "Kurrent"}, "Datierung": {"wert": "16. Jh."}}'


def test_json_survives_a_response_with_no_heading():
    """The regression: plain prose after the JSON, no "## " anywhere."""
    source_json, md = _parse_response(f"{JSON}\n\nEine Urkunde in Kurrentschrift.")

    assert source_json.get("Schrift", {}).get("wert") == "Kurrent"
    assert "Eine Urkunde" in md


def test_json_still_parses_with_a_heading():
    source_json, md = _parse_response(f"{JSON}\n\n## Beschreibung\nEine Urkunde.")
    assert source_json.get("Schrift", {}).get("wert") == "Kurrent"
    assert "## Beschreibung" in md


def test_criteria_are_no_longer_starved_by_a_missing_heading():
    """What the bug actually cost: the criteria that drive model selection."""
    from agent_a.model_selector import SourceCriteria

    source_json, md = _parse_response(f"{JSON}\n\nEine Urkunde in Kurrentschrift.")
    criteria = SourceCriteria.from_agent_b_and_json(md, source_json)
    assert criteria.script or criteria.century


def test_markdown_only_response_is_still_tolerated():
    source_json, md = _parse_response("## Beschreibung\nKein JSON hier.")
    assert source_json == {}
    assert "Kein JSON" in md


def test_malformed_json_falls_back_without_raising():
    source_json, md = _parse_response('{"Schrift": {"wert": ' + "\n\nkaputt")
    assert source_json == {}
    assert "kaputt" in md


def test_empty_response():
    assert _parse_response("") == ({}, "")
