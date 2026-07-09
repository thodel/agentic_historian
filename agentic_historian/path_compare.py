"""
path_compare.py — Gate 2 path-comparison card (HITL-2b, #149).

After Phase 3, when ≥2 transcription paths produced output (VLM / kraken /
reconciled / any engine), this shows them side by side with their **measured**
pairwise CER (from eval/metrics.py). The historian picks the winning path with
one click; that text becomes the working transcription and B/C re-run on it.

N-candidate support (#238): any number of engines is supported.
Per-span HITL: when candidate texts differ, the card highlights disagreement
spans so the historian can override individual spans with specific readings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger
from feedback_logger import log_routing_feedback

import config
from eval.metrics import cer
from runstate import RunState

LABELS: dict[str, str] = {
    "vlm": "VLM",
    "kraken": "Kraken",
    "party": "PARTY",
    "vlm-legacy": "VLM-legacy",
    "trocr": "TrOCR",
    "reconciled": "Reconciled",
    "fused": "Fused",
}

# Canonical path names, back-compat export for consumers that import them
# (e.g. orchestrator_llm._get_path_options validates LLM path_preference values
# against this). N-candidate (#238): now spans every supported engine, not just
# the original ("vlm", "kraken", "reconciled").
PATHS = tuple(LABELS.keys())

DEFAULT_GATE_THRESHOLD = 0.15


@dataclass
class DisagreementSpan:
    index: int
    tokens: dict[str, str]
    chars_start: int


def _label_for(path: str) -> str:
    return LABELS.get(path, path.replace("_", " ").title())


def compare_paths(paths: dict[str, str]) -> dict:
    """Pairwise CER between all available (non-empty) transcription paths.

    Any number of paths is supported (N-candidate Gate-2, #238).
    """
    names = [n for n in paths if (paths.get(n) or "").strip()]
    pairs: dict[tuple[str, str], float] = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            pairs[(a, b)] = cer(paths[a], paths[b], ignore_case=False,
                                ignore_whitespace=False, ignore_punctuation=False)
    return {"names": names, "pairs": pairs,
            "max_cer": max(pairs.values()) if pairs else 0.0}


def should_gate(paths: dict[str, str], threshold: float = DEFAULT_GATE_THRESHOLD) -> bool:
    """Interrupt only when ≥2 paths exist AND they disagree above threshold."""
    comp = compare_paths(paths)
    return len(comp["names"]) >= 2 and comp["max_cer"] > threshold


def compute_disagreements(paths: dict[str, str]) -> list[DisagreementSpan]:
    """Find token-level disagreements between available paths.

    Uses the longest candidate as pivot. For each other engine, aligns tokens
    via difflib.SequenceMatcher and builds per-position token columns. Any column
    with >1 distinct non-empty reading is a DisagreementSpan.
    """
    import difflib

    names = [n for n in paths if (paths.get(n) or "").strip()]
    if len(names) < 2:
        return []

    by_name = {n: paths[n].split() for n in names}
    pivot_name = max(names, key=lambda n: len(by_name[n]))
    pivot_tokens = by_name[pivot_name]

    # columns[i][engine] = token string at position i ("" = no reading)
    columns: list[dict[str, str]] = [{pivot_name: tok} for tok in pivot_tokens]

    for eng_name, eng_tokens in by_name.items():
        if eng_name == pivot_name:
            continue
        sm = difflib.SequenceMatcher(None, pivot_tokens, eng_tokens, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "equal":
                for k in range(i1, i2):
                    columns[k][eng_name] = eng_tokens[j1 + (k - i1)]
            elif tag == "replace":
                if i2 > i1:
                    columns[i1][eng_name] = " ".join(eng_tokens[j1:j2])
                    for k in range(i1 + 1, i2):
                        if k < len(columns):
                            columns[k][eng_name] = ""
            elif tag == "delete":
                for k in range(i1, i2):
                    if k < len(columns):
                        columns[k][eng_name] = ""
            elif tag == "insert":
                pass  # inserted tokens have no pivot position

    spans: list[DisagreementSpan] = []
    for idx, col in enumerate(columns):
        values = [v for v in col.values() if v]
        if len(set(values)) > 1:
            char_offset = sum(len(t) + 1 for t in pivot_tokens[:idx])
            spans.append(DisagreementSpan(index=idx, tokens=dict(col), chars_start=char_offset))
    return spans


def render_compare_card(
    state: RunState,
    paths: dict[str, str],
    snippet: int = 300,
    *,
    show_disagreements: bool = True,
) -> str:
    comp = compare_paths(paths)
    if not comp["names"]:
        return f"📊 **{state.doc_id}** · keine Transkriptionspfade vorhanden"

    lines = [f"📊 **{state.doc_id}** · Transkriptionsvergleich ({len(comp['names'])} Pfade)", ""]

    for n in comp["names"]:
        text = paths[n]
        more = "…" if len(text) > snippet else ""
        lines.append(f"**{_label_for(n)}** ({len(text)} Z.):")
        lines.append(f"> {text[:snippet]}{more}")
    lines.append("")

    if len(comp["names"]) >= 2:
        for (a, b), c in comp["pairs"].items():
            lines.append(f"`CER {_label_for(a)}↔{_label_for(b)}` {c:.1%}")
        lines.append("")

    if not should_gate(paths):
        longest = max(comp["names"], key=lambda n: len(paths[n]))
        lines.append(f"ℹ️ Pfade stimmen weitgehend überein — auto-gewählt: "
                     f"{_label_for(longest)}")

    if show_disagreements and len(comp["names"]) >= 2:
        disagree_spans = compute_disagreements(paths)
        if disagree_spans:
            lines.append("")
            lines.append(f"**⚠️ {len(disagree_spans)} umstrittene Stelle(n)** "
                         "(klicke einen Button um den Span zu überschreiben):")
            for sp in disagree_spans[:10]:
                tokens_display = "; ".join(
                    f"{_label_for(n)}={repr(t)}" for n, t in sp.tokens.items()
                )
                lines.append(f"  [{sp.index}] {tokens_display}")

    return "\n".join(lines)


def apply_path_choice(
    state: RunState,
    choice: str,
    paths: dict[str, str],
    *,
    decided_by: str = "human",
    span_index: Optional[int] = None,
) -> str:
    """Record the historian's path choice; dirty B/C via RunState invalidation."""
    available = [n for n in paths if (paths.get(n) or "").strip()]
    if choice not in available:
        raise ValueError(f"unknown path {choice!r}; available: {available}")

    if span_index is not None:
        _existing = state.gate_decisions.get("span_overrides", {})
        _existing[str(span_index)] = choice
        state.gate_decisions["span_overrides"] = _existing
        text = paths.get(choice, "") or ""
        logger.info(f"[gate2] {state.doc_id}: span[{span_index}] override → {choice}")
    else:
        text = paths.get(choice, "") or ""
        state.invalidate("path_preference", value=choice,
                         user=state.gate_decisions.get("user"))
        state.artifacts["reconcile"] = text
        state.gate_decisions["path"] = choice
        logger.info(f"[gate2] {state.doc_id}: path={choice} ({len(text)} chars)")

    log_routing_feedback(
        state=state,
        field="path_preference",
        inferred_value=state.gate_decisions.get("path") if span_index is None else None,
        chosen_value=choice,
        path=choice,
        decided_by=decided_by,
    )
    return text


def build_view(state: RunState, paths: dict[str, str],
               runners: Optional[dict] = None):
    """Construct RoutingComparisonView — one button per available path."""
    import discord

    comp = compare_paths(paths)

    class _PathButton(discord.ui.Button):
        def __init__(self, path: str):
            self.path = path
            super().__init__(
                label=f"Nutze {_label_for(path)}",
                style=discord.ButtonStyle.primary
                      if path in ("reconciled", "fused")
                      else discord.ButtonStyle.secondary,
                custom_id=f"ah:{state.doc_id}:gate2:{path}",
            )

        async def callback(self, interaction):
            apply_path_choice(state, self.path, paths, decided_by="human")
            state.save()
            if runners and config.AUTO_RESUME_AFTER_GATE:
                state.resume(runners)
            await interaction.response.edit_message(
                content=render_compare_card(state, paths), view=self.view)

    class PathComparisonView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for name in comp["names"]:
                self.add_item(_PathButton(name))

    return PathComparisonView()
