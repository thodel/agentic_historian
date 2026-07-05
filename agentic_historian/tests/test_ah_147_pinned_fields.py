"""Tests for #147 (HITL-1c): pinned human metadata is authoritative downstream.

Offline: gs.chat_text is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_147_pinned_fields.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents import source_description as sd


def test_apply_pins_overwrites_elements_with_quelle():
    sj = {"Datierung": {"wert": "13. Jh. (unsicher)"}, "Sprache": {}}
    out = sd._apply_pins(sj, {"century": 15, "lang": "de", "script": "Kurrent"})
    assert out["Datierung"]["wert"] == "15. Jahrhundert"
    assert out["Datierung"]["quelle"] == "historiker"
    assert out["Sprache"]["wert"] == "de" and out["Sprache"]["quelle"] == "historiker"
    assert out["Schrift"]["wert"] == "Kurrent" and out["Schrift"]["quelle"] == "historiker"


def test_apply_pins_noop_without_pins():
    sj = {"Datierung": {"wert": "x"}}
    assert sd._apply_pins(sj, {}) == {"Datierung": {"wert": "x"}}


def test_pin_constraint_text():
    c = sd._pin_constraint({"century": 15, "lang": "de"})
    assert "GESICHERT" in c and "Datierung = 15. Jahrhundert" in c and "Sprache = de" in c
    assert sd._pin_constraint({}) == ""


def test_pins_from_runstate_last_wins():
    from runstate import RunState
    rs = RunState(doc_id="d")
    rs.invalidate("century", value=14, user="t")
    rs.invalidate("century", value=15, user="t")   # corrected again
    rs.invalidate("lang", value="de")
    rs.invalidate("document_type", value="urkunde")  # not a pinned-element field
    assert sd.pins_from_runstate(rs) == {"century": 15, "lang": "de"}


def test_describe_injects_pin_and_persists_with_quelle(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "DESCRIPTIONS_DIR", tmp_path)

    prompts = []

    def fake_chat(prompt, system=None, **kw):
        prompts.append(prompt)
        # model returns a WRONG dating; the pin must override it
        return '{"Datierung": {"wert": "13. Jahrhundert"}}\n\n# Beschreibung\nText.'

    monkeypatch.setattr(sd.gs, "chat_text", fake_chat)

    result = sd.describe("saa-1", "Eine kurze Transkription.", pins={"century": 15})

    # 1. returned source_json carries the pinned value + provenance
    assert result["source_json"]["Datierung"]["wert"] == "15. Jahrhundert"
    assert result["source_json"]["Datierung"]["quelle"] == "historiker"

    # 2. the pin was injected into the prompt sent to the model (adopt, don't re-infer)
    assert any("GESICHERT" in p and "15. Jahrhundert" in p for p in prompts)

    # 3. survives into descriptions/<id>.json
    saved = json.loads((tmp_path / "saa-1.json").read_text(encoding="utf-8"))
    assert "historiker" in json.dumps(saved, ensure_ascii=False)
