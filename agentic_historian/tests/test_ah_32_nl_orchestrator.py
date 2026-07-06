"""#32 (prototype): NL planner turns a chat instruction into agent-tool calls.

Offline — the LLM (gs.chat_text) and tool execution are mocked. Run from repo root:
    pytest agentic_historian/tests/test_ah_32_nl_orchestrator.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import nl_orchestrator as nl  # noqa: E402
import agent_tools  # noqa: E402


def _llm_returns(monkeypatch, text):
    monkeypatch.setattr(nl.gs, "chat_text", lambda *a, **k: text)


def test_prompt_lists_the_tools():
    p = nl.build_prompt("Transkribiere BAT_1.jpg")
    assert "agent_a" in p and "agent_c" in p and "Anweisung" in p


def test_plan_parses_and_keeps_known_tools(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"plan": [
        {"tool": "agent_a", "args": {"file_path": "hot/x.jpg"}},
        {"tool": "agent_c", "args": {"doc_id": "x"}},
    ]}))
    steps = nl.plan("Transkribiere und extrahiere Entitäten")
    assert [s["tool"] for s in steps] == ["agent_a", "agent_c"]


def test_plan_drops_unknown_tools(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"plan": [
        {"tool": "agent_a", "args": {"file_path": "hot/x.jpg"}},
        {"tool": "delete_everything", "args": {}},
    ]}))
    assert [s["tool"] for s in nl.plan("...")] == ["agent_a"]


def test_plan_tolerates_prose_wrapped_json(monkeypatch):
    _llm_returns(monkeypatch, 'Hier der Plan:\n```json\n{"plan":[{"tool":"agent_e","args":{}}]}\n```')
    assert [s["tool"] for s in nl.plan("Meta-Report")] == ["agent_e"]


def test_plan_empty_on_garbage(monkeypatch):
    _llm_returns(monkeypatch, "kein json hier")
    assert nl.plan("...") == []


def test_run_executes_each_step(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"plan": [
        {"tool": "agent_a", "args": {"file_path": "hot/x.jpg"}},
        {"tool": "agent_e", "args": {}},
    ]}))
    calls = []
    monkeypatch.setattr(agent_tools, "call_tool",
                        lambda name, **kw: calls.append((name, kw)) or {"ok": name})
    out = nl.run("Transkribiere und berichte")
    assert [c[0] for c in calls] == ["agent_a", "agent_e"]
    assert [r["tool"] for r in out["results"]] == ["agent_a", "agent_e"]
    assert out["errors"] == []


def test_run_reports_step_error_without_stopping(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"plan": [
        {"tool": "agent_a", "args": {"file_path": "x"}},
        {"tool": "agent_e", "args": {}},
    ]}))

    def flaky(name, **kw):
        if name == "agent_a":
            raise RuntimeError("boom")
        return {}
    monkeypatch.setattr(agent_tools, "call_tool", flaky)
    out = nl.run("...")
    assert any(e["tool"] == "agent_a" for e in out["errors"])
    assert [r["tool"] for r in out["results"]] == ["agent_e"]   # continued


def test_run_plan_only(monkeypatch):
    _llm_returns(monkeypatch, json.dumps({"plan": [{"tool": "agent_e", "args": {}}]}))
    called = {"n": 0}
    monkeypatch.setattr(agent_tools, "call_tool", lambda *a, **k: called.update(n=called["n"] + 1))
    out = nl.run("...", execute=False)
    assert out["plan"] and out["results"] == [] and called["n"] == 0
