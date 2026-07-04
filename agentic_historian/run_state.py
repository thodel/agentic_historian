"""
run_state.py — RunState + stage-invalidation state machine (issue #145, HITL-1a).

Design (AGENTIC_HITL_PLAN.md):
  - RunState is a persistent, JSON-serialisable per-document state object.
  - invalidate(field) marks the minimum set of stages dirty per the
    invalidation matrix:
      Datierung / Sprache / Schrift / Dokumenttyp → dirty all except VLM
      Pfad_präferenz → dirty B, C only
      entity_link → dirty nothing
  - resume() re-runs only dirty stages; clean stages are preserved.
  - pin_field() records a human override as authoritative and invalidates.
  - Exact acceptance: invalidate("century") dirties exactly
    {model_select, kraken, reconcile, B-pin, C}; VLM stage untouched.

File: data/runs/<doc_id>.json
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger


# ── Paths ────────────────────────────────────────────────────────────────────

def _runs_dir() -> Path:
    from agentic_historian import config
    return config.RUNS_DIR


# ── Stage status ─────────────────────────────────────────────────────────────

class StageStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    SKIPPED  = "skipped"
    DIRTY    = "dirty"       # invalidated by a human override — needs re-run


STAGE_ORDER = [
    "model_select",
    "kraken",
    "reconcile",
    "agent_b",
    "agent_c",
    "agent_d",
]

# Invalidation matrix: human field change → stages marked DIRTY.
# Acceptance: invalidate("century") dirties exactly
#   {model_select, kraken, reconcile, agent_b, agent_c}
# VLM stage is NEVER re-run by metadata changes (human edits text, not VLM).
INVALIDATION_MATRIX: dict[str, set[str]] = {
    # Core metadata fields (Gate 1) — re-run everything model-adjacent
    "datierung":    {"model_select", "kraken", "reconcile", "agent_b", "agent_c"},
    "sprache":      {"model_select", "kraken", "reconcile", "agent_b", "agent_c"},
    "schrift":      {"model_select", "kraken", "reconcile", "agent_b", "agent_c"},
    "dokumenttyp":  {"model_select", "kraken", "reconcile", "agent_b", "agent_c"},
    # Gate 2: path preference — only re-runs downstream B/C evaluation
    "pfad_präferenz": {"agent_b", "agent_c"},
    # Gate 3: entity link — no pipeline re-run (entity resolution is a overlay)
    "entity_link": set(),
}


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class HumanOverride:
    """A human-clicked value that is authoritative over any inferred value."""
    field: str          # "datierung" | "sprache" | "schrift" | "dokumenttyp"
    value: str          # the human-chosen value
    inferred: str       # the originally-inferred (model) value
    user: str           # Discord username or "system"
    ts: str             # ISO timestamp


@dataclass
class GateDecision:
    """Record of how a gate was decided (auto / human / timeout / blocked)."""
    gate: str           # "gate_1" | "gate_2" | "gate_3"
    decision: str       # "auto" | "human" | "timeout" | "blocked"
    details: str = ""   # free-text context


@dataclass
class PipelineArtifacts:
    """Stage outputs stored for resumption — filled as stages complete."""
    transcription: str = ""
    description: dict = field(default_factory=dict)
    entities: dict = field(default_factory=dict)
    kraken_model_id: Optional[str] = None
    kraken_transcription: str = ""
    party_transcription: str = ""
    reconciled_transcription: str = ""
    error: str = ""


@dataclass
class RunState:
    """
    Per-document pipeline state with stage-level dirty tracking.

    Human overrides are authoritative — once pinned, a field's value is
    NOT inferred again in re-runs.
    """
    doc_id: str
    # Stage statuses (one of StageStatus value strings)
    model_select: str = StageStatus.PENDING.value
    kraken: str = StageStatus.PENDING.value
    reconcile: str = StageStatus.PENDING.value
    agent_b: str = StageStatus.PENDING.value
    agent_c: str = StageStatus.PENDING.value
    agent_d: str = StageStatus.PENDING.value
    # Human-authored overrides (authoritative downstream)
    human_overrides: list[HumanOverride] = field(default_factory=list)
    # Gate decisions
    gate_decisions: list[GateDecision] = field(default_factory=list)
    # Source criteria used for model selection (populated at model_select)
    source_criteria: dict = field(default_factory=dict)
    # Pipeline stage artifacts (filled as stages complete)
    artifacts: PipelineArtifacts = field(default_factory=PipelineArtifacts)
    # Document-level meta
    doc_meta: dict = field(default_factory=dict)
    # Timestamps
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    # Human-readable summary for the routing card
    summary: dict = field(default_factory=dict)

    # ── Stage helpers ─────────────────────────────────────────────────────

    def stage_status(self, stage: str) -> StageStatus:
        return StageStatus(getattr(self, stage, StageStatus.PENDING.value))

    def is_dirty(self, stage: str) -> bool:
        return self.stage_status(stage) == StageStatus.DIRTY

    def dirty_stages(self) -> list[str]:
        """All stages that need re-running."""
        return [s for s in STAGE_ORDER if self.is_dirty(s)]

    def is_stage_done(self, stage: str) -> bool:
        return self.stage_status(stage) == StageStatus.DONE

    def invalidate(self, field: str) -> set[str]:
        """
        Mark stages dirty per INVALIDATION_MATRIX when a human changes `field`.

        Returns the set of stages newly marked DIRTY.
        A field's pinned value is authoritative over any inferred value.
        Only marks DIRTY if the stage is already DONE (clean stages are not
        touched — a PENDING stage doesn't need invalidation).
        """
        stages = INVALIDATION_MATRIX.get(field, set())
        dirtied = set()
        for stage in stages:
            status = self.stage_status(stage)
            if status not in (StageStatus.DIRTY, StageStatus.PENDING):
                setattr(self, stage, StageStatus.DIRTY.value)
                dirtied.add(stage)
        self.updated_at = _now()
        if dirtied:
            logger.info(f"[RunState] invalidate({field!r}) → dirty: {sorted(dirtied)}")
        return dirtied

    def pin_field(self, field: str, value: str, inferred: str, user: str) -> None:
        """
        Record a human override as authoritative.

        The pinned value takes precedence over any inferred value in re-runs.
        Also invalidates affected stages so downstream outputs are refreshed.
        """
        override = HumanOverride(
            field=field,
            value=value,
            inferred=inferred,
            user=user,
            ts=_now(),
        )
        # Replace any existing override for this field
        self.human_overrides = [o for o in self.human_overrides if o.field != field]
        self.human_overrides.append(override)
        self.invalidate(field)
        logger.info(
            f"[RunState] Pinned {field}={value!r} (was: {inferred!r}) by {user}"
        )

    def get_override(self, field: str) -> Optional[HumanOverride]:
        for o in self.human_overrides:
            if o.field == field:
                return o
        return None

    def is_field_pinned(self, field: str) -> bool:
        return any(o.field == field for o in self.human_overrides)

    def pinned_value(self, field: str) -> Optional[str]:
        """Return the human-pinned value for a field, or None."""
        o = self.get_override(field)
        return o.value if o else None

    def mark_running(self, stage: str) -> None:
        setattr(self, stage, StageStatus.RUNNING.value)
        self.updated_at = _now()

    def mark_done(self, stage: str) -> None:
        setattr(self, stage, StageStatus.DONE.value)
        self.updated_at = _now()

    def mark_failed(self, stage: str) -> None:
        setattr(self, stage, StageStatus.FAILED.value)
        self.updated_at = _now()

    def add_gate_decision(self, gate: str, decision: str, details: str = "") -> None:
        """
        Record or update a gate decision for this document.

        If a decision for the same gate already exists it is replaced (idempotent).
        Unresolved gates are those with decision == "blocked".
        """
        # Remove any existing entry for this gate (replace semantics)
        self.gate_decisions = [
            gd for gd in self.gate_decisions if gd.gate != gate
        ]
        self.gate_decisions.append(GateDecision(gate=gate, decision=decision, details=details))
        self.updated_at = _now()
        logger.debug(f"[RunState] gate_decision {gate}={decision}")

    def unresolved_gates(self) -> list[str]:
        """Gates that are blocked and awaiting human input."""
        return [gd.gate for gd in self.gate_decisions if gd.decision == "blocked"]

    # ── Convenience ───────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        d = asdict(self)
        d["_version"] = "1.0"
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RunState:
        d.pop("_version", None)
        if "human_overrides" in d:
            d["human_overrides"] = [
                HumanOverride(**o) if isinstance(o, dict) else o
                for o in d["human_overrides"]
            ]
        if "gate_decisions" in d:
            d["gate_decisions"] = [
                GateDecision(**g) if isinstance(g, dict) else g
                for g in d["gate_decisions"]
            ]
        if "artifacts" in d and isinstance(d["artifacts"], dict):
            d["artifacts"] = PipelineArtifacts(**d["artifacts"])
        return cls(**d)

    def save(self) -> Path:
        """Write RunState to data/runs/<doc_id>.json."""
        out_path = _runs_dir() / f"{self.doc_id}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug(f"[RunState] Saved {out_path}")
        return out_path

    @classmethod
    def load(cls, doc_id: str) -> Optional[RunState]:
        """Load RunState from data/runs/<doc_id>.json. Returns None if absent."""
        path = _runs_dir() / f"{doc_id}.json"
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning(f"[RunState] Failed to load {path}: {e}")
            return None

    @classmethod
    def create(cls, doc_id: str) -> RunState:
        return cls(doc_id=doc_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")