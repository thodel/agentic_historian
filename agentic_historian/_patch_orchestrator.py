#!/usr/bin/env python3
"""Append route_phase4_llm to orchestrator.py."""
suffix = r"""

# ── HITL-4d: LLM routing for Phase 4+ (optional, #156) ───────────────────────

def route_phase4_llm(state: "RunState", runners: dict) -> str:
    r"Phase 4+ routing decision using the ORCH LLM router (HITL-4d, #156).

    Tries the LLM router first when ORCHESTRATOR_LLM_ENABLED=true. Falls
    back to the rule-based path if the LLM is disabled, unavailable, or
    returns an invalid response. Logs which mode was used.

    Returns the decided-by mode string: "llm", "rule", or "none".
    """
    if not config.ORCHESTRATOR_LLM_ENABLED:
        logger.info("[Phase4+LLM] ORCHESTRATOR_LLM_ENABLED=false -- using rule-based")
        return "none"

    from orchestrator_llm import route_with_llm
    decision = route_with_llm(state)

    if decision is None:
        logger.info("[Phase4+LLM] LLM returned invalid/none -- using rule-based")
        return "rule"

    action = decision.get("action")
    field = decision.get("field")
    value = decision.get("value")
    confidence = decision.get("confidence", 0)
    logger.info(
        "[Phase4+LLM] doc={} action={} field={} value={} confidence={:.2f}".format(
            state.doc_id, action, field, value, confidence)
    )

    if action == "criteria_change" and field and value is not None:
        from routing_card import apply_criteria_change
        apply_criteria_change(state, field, value, decided_by="llm")
        if runners:
            state.resume(runners)
        return "llm"

    if action == "path_preference" and value:
        from path_compare import apply_path_choice
        apply_path_choice(state, value)
        if runners:
            state.resume(runners)
        return "llm"

    if action in ("proceed", "entity_link"):
        # No state change; pipeline proceeds as-is
        return "llm"

    logger.warning("[Phase4+LLM] Unhandled action {} -- proceeding without change".format(action))
    return "rule"
"""

with open('/home/dh/.openclaw/workspace/agentic_historian/orchestrator.py', 'a') as f:
    f.write(suffix)
print("Done")