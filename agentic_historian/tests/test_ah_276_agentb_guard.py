"""#276: Agent B must not fabricate a source description for a degenerate/illegible
transcription — it returns an honest low-confidence result and spends NO LLM call.

Offline — the GPUStack client and _save are mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_276_agentb_guard.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import agents.source_description as sd
from agents.source_description import HANDSCHRIFTEN_ELEMENTS


def _degenerate() -> str:
    # a u-17__-style VLM repetition collapse
    return "--- e-codices_saa-0428_015v.jpg ---\n" + "\n".join(["u", "uuu", "uu", "u", "uuuu"] * 8)


NORMAL = "Wir Hans von Wiler tuend kund allen die disen brief ansehent oder hoerent lesen"


def test_degenerate_skips_llm_and_returns_honest_result(monkeypatch):
    calls = []
    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: calls.append(1) or "{}")
    monkeypatch.setattr(sd, "_save", lambda *a, **k: None)

    r = sd.describe("d-degenerate", _degenerate())

    assert calls == []                                   # no LLM spent on garbage
    assert r["low_confidence"] is True
    desc = r["source_description"].lower()
    assert "degeneriert" in desc or "unlesbar" in desc
    # the 16 Ad-Fontes elements are present but empty (no fabricated content)
    assert all(e in r["source_json"] for e in HANDSCHRIFTEN_ELEMENTS)
    assert r["source_json"]["Schrift"] is None
    # care-flag is the safe default (also not an LLM call)
    assert r["care_flag"]["is_care_related"] is False


def test_degenerate_still_applies_human_pins(monkeypatch):
    monkeypatch.setattr(sd.gs, "chat_text", lambda *a, **k: "{}")
    monkeypatch.setattr(sd, "_save", lambda *a, **k: None)

    r = sd.describe("d-pin", _degenerate(), pins={"script": "Kurrent"})

    assert r["low_confidence"] is True
    # a historian pin is authoritative even for illegible pages
    assert "historiker" in str(r["source_json"])


def test_normal_transcription_calls_llm_and_describes(monkeypatch):
    calls = []

    def fake_chat(prompt, **k):
        calls.append(1)
        return '{"Schrift": {"wert": "Kurrent"}}\n\n# Beschreibung\nEin Gerichtsbrief, 15. Jh.'

    monkeypatch.setattr(sd.gs, "chat_text", fake_chat)
    monkeypatch.setattr(sd, "_save", lambda *a, **k: None)
    monkeypatch.setattr(sd, "_validate_and_log", lambda *a, **k: None)

    r = sd.describe("d-normal", NORMAL)

    assert calls                                         # LLM invoked → guard did NOT fire
    assert r.get("low_confidence") is not True           # normal text isn't flagged
