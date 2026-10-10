"""Tests for S4 (#569): the LLM-orchestration overlay is off by default and no
slash command reaches the planner.

`nl_orchestrator` + `orchestrator_llm` are an OpenClaw-style overlay where an LLM
picks which agents run. The standalone bot works only through its registered
slash commands and gate views; this pins that the overlay is behind one explicit
flag (`ORCHESTRATOR_LLM_ENABLED`, default off), refuses without calling the model
when off, and that `bot.py` opens no path to it.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config             # noqa: E402
import nl_orchestrator    # noqa: E402
import orchestrator_llm   # noqa: E402
from runstate import RunState  # noqa: E402


def _boom(*a, **k):
    raise AssertionError("the LLM must not be called while the overlay is off")


# ── off by default ───────────────────────────────────────────────────────────

def test_overlay_is_off_by_default():
    import os
    if not os.environ.get("ORCHESTRATOR_LLM_ENABLED"):
        assert config.ORCHESTRATOR_LLM_ENABLED is False


# ── the NL planner is gated ──────────────────────────────────────────────────

def test_plan_refuses_without_calling_the_model_when_off(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATOR_LLM_ENABLED", False)
    monkeypatch.setattr(nl_orchestrator.gs, "chat_text", _boom)
    assert nl_orchestrator.plan("beschreibe BAT_664") == []


def test_run_refuses_and_executes_nothing_when_off(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATOR_LLM_ENABLED", False)
    monkeypatch.setattr(nl_orchestrator.gs, "chat_text", _boom)
    monkeypatch.setattr(nl_orchestrator.agent_tools, "call_tool",
                        lambda *a, **k: _boom())
    out = nl_orchestrator.run("beschreibe BAT_664")
    assert out == {"plan": [], "results": [], "errors": [], "disabled": True}


def test_plan_reaches_the_model_only_when_enabled(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATOR_LLM_ENABLED", True)
    called = []
    monkeypatch.setattr(nl_orchestrator.gs, "chat_text",
                        lambda *a, **k: called.append(1) or '{"plan": []}')
    nl_orchestrator.plan("x")
    assert called, "with the flag on, the planner calls the model"


# ── the orchestrator_llm router is gated ─────────────────────────────────────

def test_llm_router_returns_none_when_off(monkeypatch):
    monkeypatch.setattr(config, "ORCHESTRATOR_LLM_ENABLED", False)
    assert orchestrator_llm.route_with_llm(RunState(doc_id="x")) is None


# ── no Discord path reaches the planner ──────────────────────────────────────

def test_bot_opens_no_path_to_the_planner():
    """The S4 guard: bot.py must not import or reference the overlay modules, so
    no slash command can reach the LLM planner."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    for name in ("nl_orchestrator", "orchestrator_llm", "agent_tools"):
        assert name not in src, f"bot.py references the overlay module {name!r}"
