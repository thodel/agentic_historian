"""
path_compare.py — Gate 2 path-comparison card (HITL-2b, #149).

After Phase 3, when ≥2 transcription paths produced output (VLM / kraken /
reconciled), this shows them side by side with their **measured** pairwise CER
(from eval/metrics.py — real disagreement, never a pseudo-confidence). The
historian picks the winning path with one click; that text becomes the working
transcription and B/C re-run on it (via the RunState invalidation matrix).

Gating rule: only interrupt when the paths actually disagree (max CER >
threshold). When they agree, the pipeline auto-proceeds with the reconciled text.

Pure logic here (comparison, gating, render, choice); the py-cord View is a thin
wrapper.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from feedback_logger import log_routing_feedback

from eval.metrics import cer
from runstate import RunState

PATHS = ("vlm", "kraken", "reconciled")
LABELS = {"vlm": "VLM", "kraken": "Kraken", "reconciled": "Reconciled"}

DEFAULT_GATE_THRESHOLD = 0.15


def compare_paths(paths: dict[str, str]) -> dict:
    """Pairwise CER between the available (non-empty) transcription paths."""
    names = [n for n in PATHS if (paths.get(n) or "").strip()]
    pairs: dict[tuple[str, str], float] = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pairs[(a, b)] = cer(paths[a], paths[b])
    return {"names": names, "pairs": pairs,
            "max_cer": max(pairs.values()) if pairs else 0.0}


def should_gate(paths: dict[str, str], threshold: float = DEFAULT_GATE_THRESHOLD) -> bool:
    """Interrupt only when ≥2 paths exist AND they disagree above threshold."""
    comp = compare_paths(paths)
    return len(comp["names"]) >= 2 and comp["max_cer"] > threshold


def render_compare_card(state: RunState, paths: dict[str, str], snippet: int = 300) -> str:
    comp = compare_paths(paths)
    if not comp["names"]:
        return f"📊 **{state.doc_id}** · keine Transkriptionspfade vorhanden"
    lines = [f"📊 **{state.doc_id}** · Transkriptionsvergleich", ""]
    for n in comp["names"]:
        text = paths[n]
        more = "…" if len(text) > snippet else ""
        lines.append(f"**{LABELS[n]}** ({len(text)} Z.):")
        lines.append(f"> {text[:snippet]}{more}")
    lines.append("")
    for (a, b), c in comp["pairs"].items():
        lines.append(f"`CER {LABELS[a]}↔{LABELS[b]}`: {c:.1%}")
    if not should_gate(paths):
        lines.append("\nℹ️ Pfade stimmen weitgehend überein — auto-gewählt: Reconciled")
    return "\n".join(lines)


def apply_path_choice(
    state: RunState,
    choice: str,
    paths: dict[str, str],
    *,
    decided_by: str = "human",
) -> str:
    """The historian picked ``choice``: make it the working transcription and
    dirty reconcile/B/C so they re-run on it. Returns the chosen text.

    Also logs the routing feedback event (HITL-4b, #154).
    """
    if choice not in PATHS:
        raise ValueError(f"unknown path {choice!r}; valid: {PATHS}")
    text = paths.get(choice, "") or ""
    # Infer path_preference from the artifacts before overriding
    inferred_value = state.gate_decisions.get("path")
    state.invalidate("path_preference", value=choice, user=state.gate_decisions.get("user"))
    # the chosen transcription becomes the reconcile artifact B/C read from
    state.artifacts["reconcile"] = text
    state.gate_decisions["path"] = choice
    logger.info(f"[gate2] {state.doc_id}: path={choice} ({len(text)} chars)")
    # Log routing feedback (#154)
    log_routing_feedback(
        state=state,
        field="path_preference",
        inferred_value=inferred_value,
        chosen_value=choice,
        path=choice,
        decided_by=decided_by,
    )
    return text

def build_view(state: RunState, paths: dict[str, str], runners: Optional[dict] = None):
    """Construct the RoutingComparisonView (3 buttons). py-cord imported lazily."""
    import discord

    comp = compare_paths(paths)

    class _PathButton(discord.ui.Button):
        def __init__(self, path: str):
            self.path = path
            super().__init__(
                label=f"Nutze {LABELS[path]}",
                style=discord.ButtonStyle.primary if path == "reconciled"
                else discord.ButtonStyle.secondary,
                custom_id=f"ah:{state.doc_id}:gate2:{path}",
            )

        async def callback(self, interaction):
            apply_path_choice(state, self.path, paths)
            if runners:
                state.resume(runners)
            state.save()
            await interaction.response.edit_message(
                content=render_compare_card(state, paths), view=self.view)

    class PathComparisonView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for name in comp["names"]:
                self.add_item(_PathButton(name))

    return PathComparisonView()
