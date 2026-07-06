"""
nl_orchestrator.py — natural-language / Scholar-in-the-Loop planner (#32, prototype).

Given a chat instruction, ask the LLM (GPUStack) to produce a *plan* — a sequence
of agent-tool calls chosen from ``agent_tools.list_tools()`` — then validate and
execute it via ``agent_tools.call_tool()``. This is the v1 substrate for the
OpenClaw-style SitL orchestrator: the LLM decides WHICH agents to run instead of
the hardcoded A→B→C sequence.

Prototype / opt-in. The registry contains only read/process tools (no destructive
operations), and unknown tools or bad params are dropped/reported, never executed.
"""

from __future__ import annotations

import json

from loguru import logger

import config
from utils import gpustack_client as gs
import agent_tools

_PLAN_SYSTEM = (
    "Du bist das Planungsmodul einer historischen Dokumenten-Pipeline. "
    "Wähle aus den verfügbaren Tools die minimalen Schritte, um die Anweisung zu "
    "erfüllen. Antworte AUSSCHLIESSLICH mit gültigem JSON der Form "
    '{"plan": [{"tool": "<name>", "args": {...}}]}. '
    "Nutze nur die aufgelisteten Tool-Namen und deren Parameter; kein Freitext."
)


def build_prompt(instruction: str) -> str:
    tools = agent_tools.list_tools()
    return (f"{_PLAN_SYSTEM}\n\nVerfügbare Tools:\n"
            f"{json.dumps(tools, ensure_ascii=False, indent=2)}\n\n"
            f"Anweisung:\n{instruction}")


def _parse_plan(raw: str) -> list[dict]:
    """Tolerant parse of the LLM reply into a list of ``{tool, args}`` steps."""
    text = (raw or "").strip()
    text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        text = text[a:b + 1]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return []
    steps = data.get("plan") if isinstance(data, dict) else data
    return [s for s in (steps or []) if isinstance(s, dict)]


def plan(instruction: str) -> list[dict]:
    """Ask the LLM for a tool-call plan; return only steps for known tools."""
    raw = gs.chat_text(build_prompt(instruction), system=None,
                       max_tokens=getattr(config, "GPUSTACK_TEXT_MAX_TOKENS", 4096))
    known = {t["name"] for t in agent_tools.list_tools()}
    valid: list[dict] = []
    for s in _parse_plan(raw):
        name = s.get("tool")
        if name not in known:
            logger.warning(f"[NL] dropping unknown tool in plan: {name!r}")
            continue
        valid.append({"tool": name, "args": s.get("args") or {}})
    return valid


def run(instruction: str, execute: bool = True) -> dict:
    """Plan and (optionally) execute. Returns ``{plan, results, errors}``.

    Each result records the tool name and success; a failing step is reported and
    does not stop the plan.
    """
    steps = plan(instruction)
    out: dict = {"plan": steps, "results": [], "errors": []}
    if not execute:
        return out
    for s in steps:
        try:
            agent_tools.call_tool(s["tool"], **s["args"])
            out["results"].append({"tool": s["tool"], "ok": True})
        except Exception as e:
            out["errors"].append({"tool": s["tool"], "error": str(e)})
    return out
