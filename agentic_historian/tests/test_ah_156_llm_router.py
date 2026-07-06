"""
Tests for #156 (HITL-4d): optional LLM routing for Phase 4+ decisions.

Offline. Run:
    cd agentic_historian
    .venv/bin/python -m pytest tests/test_ah_156_llm_router.py -v
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from orchestrator_llm import (
    LLM_ROUTER_SYSTEM,
    VALID_ACTIONS,
    VALID_FIELDS,
    build_routing_prompt,
    parse_routing_response,
    route_with_llm,
)


# ── helpers ───────────────────────────────────────────────────────────────────

class _MockRunState:
    """Minimal RunState stand-in for testing."""
    STAGES = ["transcribe", "describe", "reconcile", "gate1", "gate2", "classify", "link"]

    def __init__(self, doc_id="d1"):
        self.doc_id = doc_id
        self.criteria = {"century": 14, "lang": "de", "script": "caroline", "document_type": None}
        self.stage_status = {s: "pending" for s in self.STAGES}
        self.human_overrides = []
        self.gate_decisions = {}
        self.artifacts = {}


# ── build_routing_prompt ─────────────────────────────────────────────────────

def test_build_routing_prompt_produces_non_empty_prompt():
    """Prompt is non-empty and contains document criteria."""
    state = _MockRunState("test1")
    prompt = build_routing_prompt(state)
    assert len(prompt) > 100
    assert "test1" in prompt
    assert "century" in prompt
    assert "14" in prompt
    assert "caroline" in prompt


def test_build_routing_prompt_includes_gate_decisions():
    """Gate decisions appear in the prompt."""
    state = _MockRunState("test2")
    state.gate_decisions = {"model": {"id": "m1", "name": "Fraktur-Modell"}}
    prompt = build_routing_prompt(state)
    assert "m1" in prompt or "Fraktur" in prompt


def test_build_routing_prompt_shows_pinned_fields():
    """Human-overridden fields are marked as [GESPERRT — Mensch]."""
    state = _MockRunState("test3")
    state.human_overrides = [{"field": "lang", "value": "fr", "user": "Tester"}]
    prompt = build_routing_prompt(state)
    assert "GESPERRT" in prompt


# ── parse_routing_response ───────────────────────────────────────────────────

def test_parses_valid_criteria_change():
    """Valid criteria_change JSON is parsed correctly."""
    response = '{"action": "criteria_change", "field": "lang", "value": "fr", "confidence": 0.75}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "criteria_change"
    assert result["field"] == "lang"
    assert result["value"] == "fr"
    assert result["confidence"] == 0.75


def test_parses_valid_path_preference():
    """Valid path_preference JSON is parsed correctly."""
    response = '{"action": "path_preference", "value": "reconciled", "confidence": 0.82}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "path_preference"
    assert result["value"] == "reconciled"
    assert result["confidence"] == 0.82


def test_parses_valid_entity_link():
    """Valid entity_link JSON is parsed correctly."""
    response = '{"action": "entity_link", "confidence": 0.70}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "entity_link"
    assert result["confidence"] == 0.70


def test_parses_valid_proceed():
    """Valid proceed JSON is parsed correctly."""
    response = '{"action": "proceed", "confidence": 0.90}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "proceed"
    assert result["confidence"] == 0.90


def test_returns_none_for_invalid_json():
    """Non-JSON text returns None."""
    assert parse_routing_response("Das ist keine JSON.") is None
    assert parse_routing_response("{invalid}") is None
    assert parse_routing_response("") is None


def test_returns_none_for_unknown_action():
    """Unknown action types return None."""
    response = '{"action": "unknown_action", "confidence": 0.80}'
    assert parse_routing_response(response) is None


def test_returns_none_for_confidence_below_0_6():
    """confidence < 0.6 is rejected."""
    response = '{"action": "proceed", "confidence": 0.59}'
    assert parse_routing_response(response) is None


def test_accepts_confidence_exactly_0_6():
    """confidence == 0.6 is accepted (boundary)."""
    response = '{"action": "proceed", "confidence": 0.6}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["confidence"] == 0.6


def test_returns_none_for_unknown_field():
    """Unknown criteria fields return None."""
    response = '{"action": "criteria_change", "field": "unknown_field", "value": "x", "confidence": 0.80}'
    assert parse_routing_response(response) is None


def test_returns_none_for_missing_confidence():
    """Missing confidence field returns None."""
    response = '{"action": "proceed"}'
    assert parse_routing_response(response) is None


def test_returns_none_for_confidence_not_a_number():
    """Non-numeric confidence returns None."""
    response = '{"action": "proceed", "confidence": "high"}'
    assert parse_routing_response(response) is None


def test_strips_markdown_json_fence():
    """Markdown ```json ... ``` fences are stripped before parsing."""
    response = '```json\n{"action": "proceed", "confidence": 0.85}\n```'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "proceed"


def test_parses_with_extra_fields():
    """Extra/unknown fields are ignored; only action/field/value/confidence matter."""
    response = '{"action": "criteria_change", "field": "century", "value": 15, "confidence": 0.78, "extra": "ignored"}'
    result = parse_routing_response(response)
    assert result is not None
    assert result["action"] == "criteria_change"
    assert "extra" not in result


# ── route_with_llm ───────────────────────────────────────────────────────────

def test_route_with_llm_returns_none_when_disabled():
    """ORCHESTRATOR_LLM_ENABLED=false → route_with_llm returns None immediately."""
    with patch("orchestrator_llm.config.ORCHESTRATOR_LLM_ENABLED", False):
        state = _MockRunState()
        result = route_with_llm(state)
    assert result is None


def test_route_with_llm_returns_none_on_api_error():
    """gs.chat_text raises exception → route_with_llm returns None."""
    with patch("orchestrator_llm.config.ORCHESTRATOR_LLM_ENABLED", True):
        with patch("orchestrator_llm.config.GPUSTACK_MODEL_ORCHESTRATOR", "test-model"):
            # Mock gs module inside the function's import
            import sys
            mock_gs_mod = MagicMock()
            mock_gs_mod.chat_text.side_effect = RuntimeError("network error")
            with patch.dict(sys.modules, {"utils.gpustack_client": mock_gs_mod}):
                state = _MockRunState()
                result = route_with_llm(state)
    assert result is None


def test_route_with_llm_passes_valid_decision_through():
    """Valid LLM response is parsed and returned."""
    with patch("orchestrator_llm.config.ORCHESTRATOR_LLM_ENABLED", True):
        with patch("orchestrator_llm.config.GPUSTACK_MODEL_ORCHESTRATOR", "test-model"):
            # Patch at the utils.gpustack_client source so the inside-route_with_llm
            # import picks up the mock without sys.modules pollution
            mock_chat = MagicMock(return_value='{"action": "proceed", "confidence": 0.85}')
            with patch("utils.gpustack_client.chat_text", mock_chat):
                state = _MockRunState()
                result = route_with_llm(state)
    assert result is not None
    assert result["action"] == "proceed"
    assert result["confidence"] == 0.85
def test_route_with_llm_invalid_response_returns_none():
    """Invalid LLM JSON → None (rule-based fallback)."""
    with patch("orchestrator_llm.config.ORCHESTRATOR_LLM_ENABLED", True):
        with patch("orchestrator_llm.config.GPUSTACK_MODEL_ORCHESTRATOR", "test-model"):
            mock_gs_mod = MagicMock()
            mock_gs_mod.chat_text.return_value = "not json at all"
            with patch.dict(sys.modules, {"utils.gpustack_client": mock_gs_mod}):
                state = _MockRunState()
                result = route_with_llm(state)
    assert result is None


# ── constants ─────────────────────────────────────────────────────────────────

def test_valid_actions_contains_expected_values():
    """VALID_ACTIONS contains all expected action types."""
    assert VALID_ACTIONS == {"criteria_change", "path_preference", "entity_link", "proceed"}


def test_valid_fields_contains_expected_values():
    """VALID_FIELDS contains all expected criterion fields."""
    assert VALID_FIELDS == {"century", "lang", "script", "document_type"}


def test_llm_router_system_prompt_exists():
    """LLM_ROUTER_SYSTEM is a non-empty German system prompt."""
    assert len(LLM_ROUTER_SYSTEM) > 100
    assert "Du bist" in LLM_ROUTER_SYSTEM