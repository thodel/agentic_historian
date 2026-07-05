"""
feedback_logger.py — HITL-4b (#154): log routing human-feedback events.

After each Gate 1 (routing_card) or Gate 2 (path_compare) human interaction,
one JSON line is appended to ``data/feedback/routing.jsonl``::

    {
        "doc_id": <str>,
        "ts": <ISO8601 UTC>,
        "field": <str>,          # century | lang | script | document_type |
                                 # model_select | path_preference
        "inferred_value": <any>, # what the pipeline originally inferred
        "chosen_value": <any>,   # what the historian picked
        "model_id": <str|null>,  # only set when field == "model_select"
        "model_name": <str|null>,
        "model_score": <float|null>,
        "path": <str|null>,      # only set when field == "path_preference"
        "decided_by": <str>,     # "human"
        "score": <float|null>    # model confidence score if available
    }

The log is append-only; one line per human routing decision.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

import config
from runstate import RunState


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Fields tracked in routing feedback
ROUTING_FEEDBACK_FIELDS = frozenset(
    {"century", "lang", "script", "document_type",
     "model_select", "path_preference"}
)


def log_routing_feedback(
    state: RunState,
    field: str,
    inferred_value: Any,
    chosen_value: Any,
    *,
    model_id: Optional[str] = None,
    model_name: Optional[str] = None,
    model_score: Optional[float] = None,
    path: Optional[str] = None,
    decided_by: str = "human",
    score: Optional[float] = None,
) -> None:
    """Append one feedback entry to ``data/feedback/routing.jsonl``."""
    entry = {
        "doc_id": state.doc_id,
        "ts": _now_iso(),
        "field": field,
        "inferred_value": inferred_value,
        "chosen_value": chosen_value,
        "model_id": model_id,
        "model_name": model_name,
        "model_score": model_score,
        "path": path,
        "decided_by": decided_by,
        "score": score,
    }
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.ROUTING_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.debug(f"[feedback] logged routing: {state.doc_id}/{field} "
                 f"'{inferred_value}' → '{chosen_value}'")


def log_criteria_feedback(
    state: RunState,
    field: str,
    old_value: Any,
    new_value: Any,
    *,
    score: Optional[float] = None,
) -> None:
    """Log a Gate 1 criteria override (century / lang / script / document_type)."""
    log_routing_feedback(
        state,
        field=field,
        inferred_value=old_value,
        chosen_value=new_value,
        decided_by="human",
        score=score,
    )


def log_model_select_feedback(
    state: RunState,
    inferred_id: Optional[str],
    inferred_name: Optional[str],
    inferred_score: Optional[float],
    chosen_id: Optional[str],
    chosen_name: Optional[str],
    chosen_score: Optional[float],
    *,
    decided_by: str = "human",
) -> None:
    """Log a Gate 1 model-select override (model change after criteria change)."""
    log_routing_feedback(
        state,
        field="model_select",
        inferred_value=inferred_name or inferred_id,
        chosen_value=chosen_name or chosen_id,
        model_id=chosen_id,
        model_name=chosen_name,
        model_score=chosen_score,
        decided_by=decided_by,
        score=chosen_score,
    )


def log_path_preference_feedback(
    state: RunState,
    inferred_path: Optional[str],
    chosen_path: str,
    *,
    decided_by: str = "human",
) -> None:
    """Log a Gate 2 path preference override (VLM / kraken / reconciled)."""
    log_routing_feedback(
        state,
        field="path_preference",
        inferred_value=inferred_path,
        chosen_value=chosen_path,
        path=chosen_path,
        decided_by=decided_by,
    )