"""
uncertainty.py — HITL-4a (#153): when a gate actually blocks, and gate timeouts.

A gate interrupts the historian only when the router is genuinely unsure;
otherwise the pipeline auto-proceeds and the card is posted already-resolved
(ℹ️ auto-routed) with a non-blocking "Ändern…" affordance. Every blocking gate
has a timeout after which the default action fires and is recorded as
``decided_by = "auto"``.

Gate 1 (routing) blocks if ANY of:
  - top kraken model score < SCORE_BLOCK
  - top-2 model scores within TOP2_GAP (ambiguous)
  - Agent B flagged a routing field ``(unsicher)``
Gate 2 (path comparison) blocks if the max pairwise CER > GATE2_CER_THRESHOLD.

Standalone + UI-agnostic: consumes model_selector + path_compare; the Discord
layer just renders the decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from agent_a.model_selector import SourceCriteria, select_kraken_model
from path_compare import compare_paths
from runstate import RunState

SCORE_BLOCK = 0.4           # top model score below this → block (definite doubt)
SCORE_WARN = 0.6            # advisory threshold (plan: score < 0.6 warns)
TOP2_GAP = 0.15            # top-2 scores within this → ambiguous → block
GATE2_CER_THRESHOLD = 0.15  # max pairwise CER above this → paths disagree → block
GATE_TIMEOUT_MINUTES = 30   # blocking gate expiry → auto


@dataclass
class UncertaintyResult:
    block: bool
    decided_by: str            # "human" (a human must resolve) | "model" (auto)
    reason: str
    level: str                 # hoch ✅ | mittel ⚠️ | niedrig ⚠️ | unbekannt

    @property
    def auto(self) -> bool:
        return not self.block


def _level(score: Optional[float]) -> str:
    if score is None:
        return "unbekannt"
    if score < SCORE_BLOCK:
        return "niedrig ⚠️"
    if score < SCORE_WARN:
        return "mittel ⚠️"
    return "hoch ✅"


def _criteria(state: RunState) -> SourceCriteria:
    c = state.criteria
    return SourceCriteria(script=c.get("script"), lang=c.get("lang"),
                          century=c.get("century"), document_type=c.get("document_type"))


def agent_b_unsicher(source_json: Optional[dict]) -> bool:
    """True if Agent B flagged any routing-relevant element ``(unsicher)``."""
    if not isinstance(source_json, dict):
        return False
    for element in ("Datierung", "Sprache", "Schrift"):
        el = source_json.get(element)
        wert = el.get("wert") if isinstance(el, dict) else el
        if isinstance(wert, str) and "unsicher" in wert.lower():
            return True
    return False


def assess_gate1(state: RunState, source_json: Optional[dict] = None) -> UncertaintyResult:
    """Decide whether the Gate-1 routing card should block."""
    matches = select_kraken_model(_criteria(state), top_k=2)
    if not matches:
        return UncertaintyResult(True, "human", "kein Modell-Treffer", "unbekannt")

    top = matches[0].score
    gap = (top - matches[1].score) if len(matches) > 1 else 1.0

    if agent_b_unsicher(source_json):
        return UncertaintyResult(True, "human",
                                 "Agent B hat ein Routing-Feld als unsicher markiert",
                                 _level(top))
    if top < SCORE_BLOCK:
        return UncertaintyResult(True, "human",
                                 f"Modell-Score {top:.2f} < {SCORE_BLOCK}", "niedrig ⚠️")
    if gap < TOP2_GAP:
        return UncertaintyResult(True, "human",
                                 f"Top-2 Modelle nah beieinander (Δ{gap:.2f})", "mittel ⚠️")
    return UncertaintyResult(False, "model", "auto-geroutet", _level(top))


def assess_gate2(paths: dict[str, str]) -> UncertaintyResult:
    """Decide whether the Gate-2 path-comparison card should block."""
    comp = compare_paths(paths)
    if len(comp["names"]) < 2:
        return UncertaintyResult(False, "model", "nur ein Transkriptionspfad", "hoch ✅")
    if comp["max_cer"] > GATE2_CER_THRESHOLD:
        return UncertaintyResult(True, "human",
                                 f"Pfade divergieren (CER {comp['max_cer']:.0%})", "mittel ⚠️")
    return UncertaintyResult(False, "model", "Pfade stimmen überein", "hoch ✅")


# ── timeouts ─────────────────────────────────────────────────────────────────

def is_expired(posted_iso: str, timeout_minutes: int = GATE_TIMEOUT_MINUTES) -> bool:
    """True if a blocking gate posted at ``posted_iso`` has exceeded the timeout."""
    try:
        posted = datetime.fromisoformat(posted_iso)
    except (TypeError, ValueError):
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - posted).total_seconds() > timeout_minutes * 60


def resolve_on_timeout(state: RunState, gate: str, posted_iso: str,
                       timeout_minutes: int = GATE_TIMEOUT_MINUTES) -> Optional[str]:
    """If a blocking gate's window expired, record decided_by='auto' and return
    "auto"; otherwise None. The caller then fires the default action."""
    if not is_expired(posted_iso, timeout_minutes):
        return None
    state.gate_decisions.setdefault("timeouts", []).append(
        {"gate": gate, "decided_by": "auto",
         "ts": datetime.now(timezone.utc).isoformat()})
    return "auto"
