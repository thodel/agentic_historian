"""
runstate.py — per-document run state + stage-invalidation state machine (HITL-1a, #145).

Turns the linear A→B→(kraken)→C(→D) pipeline into a small resumable state machine.
Each document has a ``RunState`` (persisted to ``data/runs/<doc_id>.json``) that
tracks per-stage status + artifacts, the resolved ``criteria``, human overrides,
and gate decisions. A human metadata correction calls :meth:`RunState.invalidate`,
which marks exactly the affected stages dirty (per the invalidation matrix); a
later :meth:`RunState.resume` re-runs only the dirty stages.

Each completed stage emits a :class:`PhaseEvent` (the #139/VF-1 verbatim event
stream) via a pluggable ``on_phase`` callback — this module is the shared
substrate for both the verbatim feedback and the HITL routing card. It is
deliberately **UI-agnostic**: no Discord here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from pydantic import BaseModel, Field

import config

# Ordered pipeline stages.
STAGES: tuple[str, ...] = (
    "vlm",            # Phase 1 — VLM transcription
    "agent_b",        # Phase 2 — source description
    "model_select",   # Phase 3a — kraken model selection
    "kraken",         # Phase 3b — kraken OCR
    "reconcile",      # Phase 3c — reconciliation
    "agent_c",        # Phase 4 — entity extraction
    "agent_d",        # Phase 5 — corpus analysis (optional)
)

PENDING, DONE, DIRTY, ERROR = "pending", "done", "dirty", "error"

# Invalidation matrix (AGENTIC_HITL_PLAN.md). For each human input: which stages
# become dirty (re-run eagerly on resume) and which are only stale-flagged
# (re-run lazily, e.g. Agent D on the next /agent_d).
#                    field           -> (dirty stages, stale stages)
_INVALIDATION: dict[str, tuple[list[str], list[str]]] = {
    "century":       (["model_select", "kraken", "reconcile", "agent_b", "agent_c"], ["agent_d"]),
    "lang":          (["model_select", "kraken", "reconcile", "agent_b", "agent_c"], ["agent_d"]),
    "script":        (["model_select", "kraken", "reconcile", "agent_b"], []),
    "document_type": (["model_select", "kraken", "reconcile", "agent_b"], []),
    # Gate 2 — path preference: pick the winning transcription, re-run B/C on it.
    "path_preference": (["reconcile", "agent_b", "agent_c"], ["agent_d"]),
    # Gate 3 — entity link: writes the hub only; no stage re-run.
    "entity_link":   ([], []),
}

# Which criteria fields are "pinned" onto SourceCriteria when set by a human.
CRITERIA_FIELDS = {"century", "lang", "script", "document_type"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PhaseEvent(BaseModel):
    """A verbatim per-stage event (the #139/VF-1 stream)."""
    doc_id: str
    phase: str                 # stage name
    agent: str                 # producing agent/component
    status: str                # done | error
    excerpt: str = ""          # verbatim first-N chars of the stage output
    decision: str = ""         # the routing decision (model+score, qa, care flag…)
    error: str = ""            # verbatim error message on failure


class StageResult(BaseModel):
    """What a stage runner returns; resume() wraps it into a PhaseEvent."""
    artifact: Any = None
    agent: str = ""
    excerpt: str = ""
    decision: str = ""
    error: str = ""


def _log_emit(ev: PhaseEvent) -> None:
    if ev.status == ERROR:
        logger.warning(f"[{ev.doc_id}] {ev.phase} ({ev.agent}) ERROR: {ev.error}")
    else:
        logger.info(f"[{ev.doc_id}] {ev.phase} ({ev.agent}) — {ev.decision or ev.excerpt[:60]!r}")


class RunState(BaseModel):
    doc_id: str
    stage_status: dict[str, str] = Field(
        default_factory=lambda: {s: PENDING for s in STAGES})
    artifacts: dict[str, Any] = Field(default_factory=dict)
    criteria: dict[str, Any] = Field(default_factory=dict)
    human_overrides: list[dict] = Field(default_factory=list)
    gate_decisions: dict[str, Any] = Field(default_factory=dict)
    message_ids: dict[str, Any] = Field(default_factory=dict)   # Discord placeholder
    stale: list[str] = Field(default_factory=list)

    # ── queries ──────────────────────────────────────────────────────────────

    def dirty_stages(self) -> list[str]:
        return [s for s in STAGES if self.stage_status.get(s) == DIRTY]

    def _to_run(self) -> list[str]:
        """Stages that resume() should (re-)run: pending or dirty, in order."""
        return [s for s in STAGES if self.stage_status.get(s) in (PENDING, DIRTY)]

    # ── mutation ───────────────────────────────────────────────────────────────

    def invalidate(self, field: str, value: Any = None, user: Optional[str] = None) -> list[str]:
        """Apply a human input: pin it, mark affected stages dirty/stale.

        Returns the list of stages marked dirty. Raises ValueError on unknown
        field. Stages already dirty stay dirty; done stages that are affected
        become dirty; unaffected stages are untouched (their artifacts reused).
        """
        if field not in _INVALIDATION:
            raise ValueError(f"unknown invalidation field {field!r}; "
                             f"valid: {sorted(_INVALIDATION)}")
        dirty, stale = _INVALIDATION[field]
        if value is not None:
            self.human_overrides.append(
                {"field": field, "value": value, "user": user, "ts": _now()})
            if field in CRITERIA_FIELDS:
                self.criteria[field] = value        # pin — authoritative downstream
        for s in dirty:
            self.stage_status[s] = DIRTY
        for s in stale:
            if s not in self.stale:
                self.stale.append(s)
        return list(dirty)

    def resume(
        self,
        runners: dict[str, Callable[["RunState"], StageResult]],
        on_phase: Optional[Callable[[PhaseEvent], None]] = None,
    ) -> list[str]:
        """Run every pending/dirty stage (in order) for which a runner exists.

        Done stages are skipped (their artifacts reused). Each run stage stores
        its artifact, flips to done/error, and emits a PhaseEvent. Returns the
        list of stages actually run.
        """
        emit = on_phase or _log_emit
        ran: list[str] = []
        for stage in self._to_run():
            runner = runners.get(stage)
            if runner is None:
                continue                              # optional/inapplicable stage
            res = runner(self)
            self.artifacts[stage] = res.artifact
            status = ERROR if res.error else DONE
            self.stage_status[stage] = status
            emit(PhaseEvent(doc_id=self.doc_id, phase=stage, agent=res.agent or stage,
                            status=status, excerpt=res.excerpt or "",
                            decision=res.decision or "", error=res.error or ""))
            ran.append(stage)
        return ran

    # ── persistence ────────────────────────────────────────────────────────────

    @staticmethod
    def _path(doc_id: str) -> Path:
        return config.DATA_DIR / "runs" / f"{doc_id}.json"

    def save(self, path: Optional[Path] = None) -> Path:
        p = path or self._path(self.doc_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return p

    @classmethod
    def load(cls, doc_id: str, path: Optional[Path] = None) -> "RunState":
        p = path or cls._path(doc_id)
        return cls.model_validate_json(p.read_text(encoding="utf-8"))

    @classmethod
    def load_or_new(cls, doc_id: str) -> "RunState":
        p = cls._path(doc_id)
        return cls.load(doc_id) if p.exists() else cls(doc_id=doc_id)
