"""
agent_tools.py — uniform tool registry over the individually-callable agents (#41).

Each agent (A–E) plus the full pipeline is exposed as a named tool with a
JSON-schema-style parameter spec and a dispatcher, so a future natural-language /
Scholar-in-the-Loop orchestrator (#32) can enumerate and invoke agents by name
instead of hardcoding the A→B→C sequence. The standalone ``run_agent_*``
functions already live in ``orchestrator``; this is the thin, UI-agnostic seam
over them.

Functions are resolved from ``orchestrator`` at call time (late binding), so the
registry stays a pure declaration and is trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass

import orchestrator


@dataclass(frozen=True)
class AgentTool:
    name: str
    fn_name: str                 # attribute on the orchestrator module
    description: str
    parameters: dict             # {param: {"type", "required", "description"}}


AGENT_TOOLS: tuple[AgentTool, ...] = (
    AgentTool("agent_a", "run_agent_a",
              "HTR: transcribe a document image (kraken-first, VLM fallback).",
              {"file_path": {"type": "string", "required": True,
                             "description": "Path to the image/PDF to transcribe."}}),
    AgentTool("agent_b", "run_agent_b",
              "Ad-Fontes source description for an already-transcribed document.",
              {"doc_id": {"type": "string", "required": True,
                          "description": "Document id (its transcription must exist)."}}),
    AgentTool("agent_c", "run_agent_c",
              "Named-entity extraction + hub/authority linking for a document.",
              {"doc_id": {"type": "string", "required": True,
                          "description": "Document id (its transcription must exist)."}}),
    AgentTool("agent_d", "run_agent_d",
              "Corpus analysis across a set of documents.",
              {"corpus_name": {"type": "string", "required": False,
                               "description": "Corpus name (default 'default')."}}),
    AgentTool("agent_e", "run_agent_e",
              "Meta report over the pipeline run log.", {}),
    AgentTool("run_full_pipeline", "run_full_pipeline",
              "Full A→B→C(→D) pipeline on one document.",
              {"file_path": {"type": "string", "required": True,
                             "description": "Path to the image/PDF."}}),
)

_BY_NAME = {t.name: t for t in AGENT_TOOLS}


def list_tools() -> list[dict]:
    """Tool descriptors (name/description/parameters) — e.g. for an LLM tool spec."""
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in AGENT_TOOLS]


def get_tool(name: str) -> AgentTool:
    if name not in _BY_NAME:
        raise KeyError(f"unknown agent tool {name!r}; known: {sorted(_BY_NAME)}")
    return _BY_NAME[name]


def call_tool(name: str, **kwargs) -> dict:
    """Validate parameters against the tool's spec and invoke it (late-bound)."""
    tool = get_tool(name)
    missing = [p for p, spec in tool.parameters.items()
               if spec.get("required") and p not in kwargs]
    if missing:
        raise ValueError(f"{name}: missing required parameter(s): {missing}")
    unknown = [k for k in kwargs if k not in tool.parameters]
    if unknown:
        raise ValueError(f"{name}: unknown parameter(s): {unknown}")
    return getattr(orchestrator, tool.fn_name)(**kwargs)
