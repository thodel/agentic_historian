"""
routing_card.py — Gate 1 routing card (HITL-1b, #146).

The card shows the pipeline's inferred routing metadata for a document and lets
a historian correct it with clicks (never free text). The four selects map 1:1
onto ``SourceCriteria`` (script / lang / century / document_type); confirming a
change re-selects the kraken model (and marks the kraken path dirty via the
RunState), so the model + score on the card update in place.

Pure logic (options, render, model re-selection) lives here and is unit-tested
offline; ``RoutingCardView`` is a thin py-cord wrapper whose callbacks delegate
to it (bot.py stays a renderer, #33).
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

import config

from feedback_logger import log_routing_feedback

from agent_a.model_selector import (
    LANG_ALIASES, SCRIPT_ALIASES, ModelMatch, SourceCriteria, select_kraken_model,
)
from knowledge_hub import hub
from runstate import RunState

# ── Select options (from the existing registries) ────────────────────────────

_CENTURIES = list(range(13, 18))                      # 13.–17. Jh.
# Curated to the corpus languages (LANG_ALIASES also holds many irrelevant ones).
_LANG_OPTIONS = [("Deutsch", "de"), ("Latein", "la"),
                 ("Französisch", "fr"), ("Italienisch", "it")]


def century_options() -> list[tuple[str, int]]:
    return [(f"{c}. Jh.", c) for c in _CENTURIES]


def lang_options() -> list[tuple[str, str]]:
    return list(_LANG_OPTIONS)


def script_options() -> list[tuple[str, str]]:
    # SCRIPT_ALIASES keys are the canonical script names; cap at Discord's 25.
    return [(k.capitalize(), k) for k in list(SCRIPT_ALIASES)[:25]]


def type_options() -> list[tuple[str, str]]:
    types = hub.get_hub().get_document_types() or ["urkunde", "register", "brief", "rechnung"]
    return [(t.capitalize(), t) for t in types[:25]]


FIELD_OPTIONS = {
    "century": century_options,
    "lang": lang_options,
    "script": script_options,
    "document_type": type_options,
}
FIELD_LABELS = {"century": "Datierung", "lang": "Sprache",
                "script": "Schrift", "document_type": "Typ"}


# ── Model (re-)selection from the current criteria ───────────────────────────

def _criteria(state: RunState) -> SourceCriteria:
    c = state.criteria
    return SourceCriteria(
        script=c.get("script"),
        lang=c.get("lang"),
        century=c.get("century"),
        document_type=c.get("document_type"),
    )


def select_model(state: RunState) -> Optional[ModelMatch]:
    """Top kraken ModelMatch for the state's current criteria (or None)."""
    matches = select_kraken_model(_criteria(state), top_k=1)
    return matches[0] if matches else None


def apply_criteria_change(
    state: RunState,
    field: str,
    value,
    *,
    decided_by: str = "human",
) -> Optional[ModelMatch]:
    """A historian pinned ``field=value``: invalidate + re-select the model.

    Updates the RunState (pins the criterion, marks the kraken path dirty via
    the invalidation matrix) and records the new model choice as a gate
    decision. Returns the new top ModelMatch. Caller resumes the dirty stages.

    Also logs the routing feedback event (HITL-4b, #154).
    """
    inferred_value = state.criteria.get(field)
    state.invalidate(field, value=value, user=state.gate_decisions.get("user"))
    match = select_model(state)
    state.gate_decisions["model"] = (
        {"id": match.model.model_id, "name": match.model.name,
         "score": round(match.score, 3), "matched_on": match.matched_on}
        if match else None
    )
    logger.info(f"[card] {state.doc_id}: {field}={value} → "
                f"{match.model.name if match else 'no model'}")
    # Log routing feedback (#154)
    log_routing_feedback(
        state=state,
        field=field,
        inferred_value=inferred_value,
        chosen_value=value,
        decided_by=decided_by,
    )
    return match

def _pinned_fields(state: RunState) -> set[str]:
    return {o["field"] for o in state.human_overrides}


def render_card(state: RunState, match: Optional[ModelMatch] = None) -> str:
    """Render the routing card as Discord markdown (presentation only)."""
    if match is None:
        match = select_model(state)
    pinned = _pinned_fields(state)
    c = state.criteria

    def row(field: str, disp) -> str:
        marker = "📌 Historiker:in" if field in pinned else "inferred"
        return f"`{FIELD_LABELS[field]:<10}`: {disp if disp not in (None, '') else '—'}  _({marker})_"

    lines = [f"📜 **{state.doc_id}** · Routing", ""]
    lines.append(row("century", f"{c.get('century')}. Jh." if c.get("century") else None))
    lines.append(row("lang", c.get("lang")))
    lines.append(row("script", c.get("script")))
    lines.append(row("document_type", c.get("document_type")))
    if match:
        warn = " ⚠️" if match.score < 0.6 else ""
        lines.append(f"`{'HTR-Modell':<10}`: {match.model.name} "
                     f"(score {match.score:.2f}{warn})")
    else:
        lines.append(f"`{'HTR-Modell':<10}`: — _(kein Treffer)_")
    return "\n".join(lines)


# ── Discord View (thin wrapper) ──────────────────────────────────────────────

def build_view(state: RunState, runners: Optional[dict] = None):
    """Construct the interactive RoutingCardView (imported lazily so this module
    stays importable without discord installed in some contexts)."""
    import discord  # noqa: F401  (py-cord)

    class _FieldSelect(discord.ui.Select):
        def __init__(self, field: str):
            self.field = field
            opts = [discord.SelectOption(label=lbl, value=str(val))
                    for lbl, val in FIELD_OPTIONS[field]()]
            super().__init__(placeholder=FIELD_LABELS[field], options=opts,
                             custom_id=f"ah:{state.doc_id}:gate1:{field}", min_values=1,
                             max_values=1)

        async def callback(self, interaction):
            raw = self.values[0]
            value = int(raw) if self.field == "century" else raw
            match = apply_criteria_change(state, self.field, value)
            state.save()
            if runners and config.AUTO_RESUME_AFTER_GATE:
                state.resume(runners)
            await interaction.response.edit_message(
                content=render_card(state, match), view=self.view)

    class RoutingCardView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for field in ("century", "lang", "script", "document_type"):
                self.add_item(_FieldSelect(field))

    return RoutingCardView()
