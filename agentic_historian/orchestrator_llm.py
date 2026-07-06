"""
orchestrator_llm.py — LLM-based routing decision router for Phase 4+ (HITL-4d, #156).

Provides an optional GPUSTACK_MODEL_ORCHESTRATOR (minimax-m2.7) overlay that can
propose routing decisions (criteria changes, path preferences, entity links) for
the HITL pipeline.  All decisions are validated against the invalidation matrix
before being accepted; invalid proposals are silently dropped and the rule-based
fallback is used instead.

Enable with ORCHESTRATOR_LLM_ENABLED=true (default: False, opt-in).

Decision schema (JSON returned by LLM):
    {
        "action": "criteria_change" | "path_preference" | "entity_link" | "proceed",
        "field": "century" | "lang" | "script" | "document_type" | null,
        "value": <int | str | null>,
        "confidence": 0.6 – 1.0
    }

Confidence < 0.6 is always rejected (matches HITL uncertainty threshold #153).
"""

from __future__ import annotations

import json
import re
from typing import Optional

from loguru import logger

import config
from runstate import RunState

# ── System prompt ────────────────────────────────────────────────────────────

LLM_ROUTER_SYSTEM = (
    "Du bist ein Routing-Entscheidungsmodul für eine historische Dokumenten-Pipeline. "
    "Deine Aufgabe ist es, für ein gegebenes Dokument die bestmögliche Routing-Entscheidung "
    "vorzuschlagen: welches Kriterium (Jahrhundert, Sprache, Schrift, Dokumenttyp) soll "
    "für die Modellwahl verwendet werden, oder welcher Transkriptionspfad (VLM, Kraken, "
    "Reconciled) soll bevorzugt werden.\n\n"
    "Du erhältst: den aktuellen Dokumentstatus (Kriterien, Artefakte, Stufen), "
    "die verfügbaren Optionen pro Feld, und die Unsicherheits-Metrik.\n\n"
    "Antworte JEDES MAL mit einem einzelnen gültigen JSON-Objekt, "
    "niemals mit Freitext. Kein Markdown, kein Kommentar, keine Einleitung.\n\n"
    "Erlaubte Aktionen:\n"
    '  "criteria_change" — schlage eine Kriteriumsänderung vor (century / lang / script / document_type)\n'
    '  "path_preference" — schlage einen Transkriptionspfad vor (vlm / kraken / reconciled)\n'
    '  "entity_link" — schlage eine Entity-Verlinkung vor (kein Feld, kein value)\n'
    '  "proceed" — keine Änderung, Pipeline fortsetzen\n\n'
    "Regeln:\n"
    "  - Du darfst NUR Werte vorschlagen, die in den angegebenen gültigen Optionen enthalten sind.\n"
    "  - confidence muss zwischen 0.6 und 1.0 liegen. Bei Unsicherheit: 0.6–0.75.\n"
    "  - Bei genügend hoher Sicherheit (score ≥ 0.75): confidence = 0.85–1.0.\n"
    "  - Felder die bereits vom Menschen gesetzt wurden, NICHT nochmals ändern.\n"
    "  - Immer das gesamte JSON-Objekt in einer einzigen Zeile ausgeben."
)

# ── Valid option sets (imported lazily to avoid circular imports) ────────────

_FIELD_OPTIONS: Optional[dict] = None
_PATH_OPTIONS: Optional[list] = None


def _get_field_options() -> dict:
    global _FIELD_OPTIONS
    if _FIELD_OPTIONS is None:
        from routing_card import FIELD_OPTIONS, century_options, lang_options, script_options, type_options
        _FIELD_OPTIONS = {
            "century": {v for _, v in century_options()},
            "lang": {v for _, v in lang_options()},
            "script": {v for _, v in script_options()},
            "document_type": {v for _, v in type_options()},
        }
    return _FIELD_OPTIONS


def _get_path_options() -> list:
    global _PATH_OPTIONS
    if _PATH_OPTIONS is None:
        from path_compare import PATHS
        _PATH_OPTIONS = list(PATHS)
    return _PATH_OPTIONS


# ── Prompt builder ────────────────────────────────────────────────────────────

def build_routing_prompt(state: RunState) -> str:
    """Serialise RunState + available choices into an LLM routing prompt."""
    field_opts = _get_field_options()
    path_opts = _get_path_options()
    pinned = {o["field"] for o in state.human_overrides}

    # Summarise criteria
    criteria_parts = []
    for f in ("century", "lang", "script", "document_type"):
        val = state.criteria.get(f)
        marker = " [GESPERRT — Mensch]" if f in pinned else ""
        if val is not None:
            criteria_parts.append(f"  {f}: {val}{marker}")
        else:
            criteria_parts.append(f"  {f}: (noch nicht gesetzt){marker}")

    # Summarise stage status
    stage_parts = [f"  {s}: {state.stage_status.get(s, 'pending')}" for s in state.STAGES]

    # Summarise artifacts
    artifact_parts = []
    for name, art in state.artifacts.items():
        if art:
            snippet = str(art)[:120].replace("\n", " ")
            artifact_parts.append(f"  {name}: {snippet}…")
    if not artifact_parts:
        artifact_parts = ["  (keine)"]

    # Gate decisions
    gate_parts = []
    for k, v in state.gate_decisions.items():
        if k != "user":
            gate_parts.append(f"  {k}: {v}")
    if not gate_parts:
        gate_parts = ["  (keine)"]

    prompt = (
        "## Dokument: " + state.doc_id + "\n"
        "### Aktuelle Kriterien:\n" + "\n".join(criteria_parts) + "\n"
        "### Stufenstatus:\n" + "\n".join(stage_parts) + "\n"
        "### Verfügbare Artefakte:\n" + "\n".join(artifact_parts) + "\n"
        "### Gate-Entscheidungen:\n" + "\n".join(gate_parts) + "\n"
        "### Gültige Optionen:\n"
    )
    for f, opts in field_opts.items():
        opts_str = ", ".join(sorted(str(o) for o in opts))
        prompt += f"  {f}: {{{opts_str}}}\n"
    prompt += f"  path_preference: {{{', '.join(path_opts)}}}\n"

    prompt += (
        "\n### Deine Aufgabe:\n"
        "Gib EIN JSON-Objekt zurück mit dem Schlüssel 'action' (criteria_change / "
        "path_preference / entity_link / proceed).\n"
        "Bei criteria_change: auch 'field' und 'value' angeben.\n"
        "Bei path_preference: 'value' = gewählter Pfad.\n"
        "Bei entity_link: nur action und confidence.\n"
        "Bei proceed: nur action und confidence.\n"
        "confidence: 0.6–1.0, bei Unsicherheit niedrig halten.\n"
        'Antwortformat: {"action": "...", "field": "...", "value": "...", "confidence": 0.xx}\n'
    )
    return prompt


# ── Response parser ───────────────────────────────────────────────────────────

VALID_ACTIONS = {"criteria_change", "path_preference", "entity_link", "proceed"}
VALID_FIELDS = {"century", "lang", "script", "document_type"}


def parse_routing_response(text: str) -> dict | None:
    """
    Parse and validate an LLM routing response.

    Returns a decision dict if the response is valid JSON, the action and field
    (if applicable) are in the valid sets, the value (if applicable) is in the
    valid option set, and confidence >= 0.6.  Returns None on any validation
    failure (silently dropped — rule-based fallback will be used).
    """
    # Strip markdown code fences
    cleaned = re.sub(r"```json\s*", "", text.strip())
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()

    try:
        decision = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.debug(f"[llm_router] JSON parse failed: {text[:100]!r}")
        return None

    if not isinstance(decision, dict):
        logger.debug(f"[llm_router] not a dict: {type(decision)}")
        return None

    action = decision.get("action")
    if action not in VALID_ACTIONS:
        logger.debug(f"[llm_router] unknown action: {action!r}")
        return None

    confidence = decision.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        logger.debug(f"[llm_router] confidence not a number: {confidence!r}")
        return None
    if confidence < 0.6:
        logger.debug(f"[llm_router] confidence {confidence} below 0.6 threshold")
        return None

    # Validate field + value for criteria_change
    if action == "criteria_change":
        field = decision.get("field")
        if field not in VALID_FIELDS:
            logger.debug(f"[llm_router] unknown criteria field: {field!r}")
            return None
        value = decision.get("value")
        field_opts = _get_field_options()
        valid_values = field_opts.get(field, set())
        # Normalize century to int for comparison
        if field == "century":
            try:
                value = int(value)
            except (TypeError, ValueError):
                logger.debug(f"[llm_router] century not int: {value!r}")
                return None
        if value not in valid_values:
            logger.debug(f"[llm_router] invalid value {value!r} for field {field}")
            return None

    # Validate value for path_preference
    if action == "path_preference":
        value = decision.get("value")
        if value not in _get_path_options():
            logger.debug(f"[llm_router] invalid path_preference: {value!r}")
            return None

    # entity_link and proceed have no further constraints
    return {
        "action": action,
        "field": decision.get("field"),
        "value": decision.get("value"),
        "confidence": confidence,
    }


# ── LLM caller ───────────────────────────────────────────────────────────────

def route_with_llm(state: RunState) -> dict | None:
    """
    Call the ORCH model with the routing prompt and return a validated decision.

    Returns None if ORCHESTRATOR_LLM_ENABLED is False, if the model call fails,
    or if the response fails validation.  Callers must fall back to the
    rule-based router in all failure cases.
    """
    if not config.ORCHESTRATOR_LLM_ENABLED:
        return None

    try:
        from utils import gpustack_client as gs
    except Exception as e:
        logger.warning(f"[llm_router] could not import gpustack_client: {e}")
        return None

    prompt = build_routing_prompt(state)
    try:
        raw = gs.chat_text(
            prompt,
            system=LLM_ROUTER_SYSTEM,
            model=config.GPUSTACK_MODEL_ORCHESTRATOR,
            max_tokens=1024,
        )
    except Exception as e:
        logger.warning(f"[llm_router] gs.chat_text failed: {e}")
        return None

    decision = parse_routing_response(raw)
    if decision is None:
        logger.debug(f"[llm_router] response validation failed, falling back to rules")
    else:
        logger.info(f"[llm_router] {state.doc_id}: action={decision['action']} "
                    f"field={decision.get('field')} value={decision.get('value')} "
                    f"confidence={decision['confidence']}")
    return decision