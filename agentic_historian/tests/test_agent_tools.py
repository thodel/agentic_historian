"""#41: uniform tool registry over the individually-callable agents.

Offline — orchestrator entry points are monkeypatched. Run from the repo root:
    pytest agentic_historian/tests/test_agent_tools.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import orchestrator  # noqa: E402
import agent_tools as at  # noqa: E402


def test_list_tools_covers_all_agents():
    names = {t["name"] for t in at.list_tools()}
    assert {"agent_a", "agent_b", "agent_c", "agent_d", "agent_e"} <= names
    # each descriptor carries a schema
    for t in at.list_tools():
        assert "description" in t and isinstance(t["parameters"], dict)


def test_every_tool_resolves_to_an_orchestrator_callable():
    for t in at.AGENT_TOOLS:
        assert callable(getattr(orchestrator, t.fn_name))


def test_call_tool_dispatches(monkeypatch):
    seen = {}
    monkeypatch.setattr(orchestrator, "run_agent_a", lambda file_path: (seen.update(a=file_path), {"ok": 1})[1])
    monkeypatch.setattr(orchestrator, "run_agent_e", lambda: {"ok": "e"})
    assert at.call_tool("agent_a", file_path="hot/x.jpg") == {"ok": 1}
    assert seen["a"] == "hot/x.jpg"
    assert at.call_tool("agent_e") == {"ok": "e"}


def test_call_tool_missing_required():
    try:
        at.call_tool("agent_a")            # file_path required
        assert False, "expected ValueError"
    except ValueError as e:
        assert "file_path" in str(e)


def test_call_tool_unknown_param(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_agent_e", lambda: {})
    try:
        at.call_tool("agent_e", nope=1)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "nope" in str(e)


def test_get_tool_unknown_raises():
    try:
        at.get_tool("agent_z")
        assert False, "expected KeyError"
    except KeyError:
        pass
