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
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from feedback_logger import log_routing_feedback

import config
from eval.metrics import cer
from runstate import ClosestReadingText, RunState

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


def _selected(state: RunState) -> list[str]:
    """The labels the historian has toggled on this Gate-2 card so far."""
    sel = state.gate_decisions.get("gate2_selected")
    return list(sel) if isinstance(sel, list) else []


def render_vote_card(state: RunState, paths: dict[str, str], *,
                     max_chars: int = 1900) -> str:
    """The selection card: each candidate reading, marked ☑/☐ for what's picked,
    with an instruction to select one or many and confirm (#313 multi-select).

    Capped to Discord's 2000-char limit: with the ensemble's engine set (up to 7
    candidates) the old card ran to 8392 chars. Each snippet scales to a
    per-candidate budget; the verbose N×N pairwise-CER matrix collapses to one line;
    header + CER + selection footer are kept whole and only the candidate blocks are
    trimmed. The buttons carry the actual choice; the text is only for judging.
    """
    comp = compare_paths(paths)
    names = comp["names"]
    if not names:
        return f"📊 **{state.doc_id}** · keine Lesarten vorhanden"

    sel = _selected(state)
    header = (f"📊 **{state.doc_id}** · {len(names)} Lesart(en) — wähle **eine oder "
              f"mehrere** und dann **Bestätigen** (mehrere werden kombiniert).")
    cer_line = (f"`max. paarweise CER {comp['max_cer']:.0%}` — die Engines sind uneinig."
                if len(names) >= 2 else "")
    footer = (f"✅ Ausgewählt: {', '.join(_label_for(n) for n in sel)}"
              if sel else "▫️ Noch nichts ausgewählt.")

    reserved = len(header) + len(cer_line) + len(footer) + 14
    per = max(50, (max_chars - reserved) // max(1, len(names)))
    blocks = []
    for n in names:
        mark = "☑" if n in sel else "☐"
        text = paths[n]
        more = "…" if len(text) > per else ""
        blocks.append(f"{mark} **{_label_for(n)}** ({len(text)} Z.):\n> {text[:per]}{more}")

    blocks_text = "\n".join(blocks)
    avail = max_chars - reserved
    if len(blocks_text) > avail:
        blocks_text = blocks_text[:max(0, avail - 1)].rstrip() + "…"

    return "\n".join([header, "", blocks_text, "", cer_line, "", footer])


def render_decided_card(state: RunState, paths: dict[str, str],
                        chosen: list[str], text: str) -> str:
    """The collapsed card shown after Bestätigen — no buttons, just the outcome."""
    if not chosen:
        return f"📊 **{state.doc_id}** · abgebrochen — nichts ausgewählt."
    labels = ", ".join(_label_for(c) for c in chosen)
    how = "kombiniert aus" if len(chosen) > 1 else "gewählt:"
    preview = (text or "")[:400]
    more = "…" if len(text or "") > 400 else ""
    return (f"✅ **{state.doc_id}** · {how} {labels}\n"
            f"> {preview}{more}\n"
            f"_B/C laufen auf diesem Text neu._")


def apply_combined_choice(
    state: RunState,
    selected: list[str],
    paths: dict[str, str],
    *,
    decided_by: str = "human",
    editor: str = "",
) -> str:
    """Apply the historian's selection as the working transcription (#313).

    One selected reading → that text verbatim. Several → **fused into one**, with
    the automatic no-merge band (#300) forced OFF: the historian explicitly chose
    to combine these, overriding the pipeline's "at high disagreement, don't blend"
    default. Only the selected readings are fused; rejected engines are ignored.

    Dirties B/C via RunState invalidation and logs a positive routing signal for
    every selected engine. Returns the resulting text.
    """
    available = [n for n in paths if (paths.get(n) or "").strip()]
    chosen = [s for s in selected if s in available]
    if not chosen:
        raise ValueError(f"no valid selection among {available}")

    if len(chosen) == 1:
        text = paths[chosen[0]] or ""
    else:
        from fusion import fuse
        recs = [{"engine": c, "text": paths[c], "error": "", "confidence": 0.5}
                for c in chosen]
        # no_merge_cer > 1 never triggers → force the merge the human asked for.
        text = fuse(recs, no_merge_cer=1.01).text

    # Pseudonymise BEFORE anything is stored: the raw platform id must never reach
    # the RunState on disk or the published RDF export. preferences.pseudonym is
    # salted per deployment — a bare hash of a Discord id is reversible by brute
    # force over an enumerable id space, so an unsalted digest is not a pseudonym.
    from preferences import pseudonym as _pseudonym
    editor_pseudonym = "editor-" + _pseudonym(str(editor or decided_by))

    state.invalidate("path_preference", value=",".join(chosen), user=editor_pseudonym)
    state.artifacts["reconcile"] = text
    state.gate_decisions["path"] = chosen[0] if len(chosen) == 1 else list(chosen)
    state.gate_decisions["gate2_combined"] = list(chosen)
    # The editorial act, under its one canonical contract. The text is tagged
    # ClosestReadingText so eval.harness refuses it as a reference (#336).
    state.closest_reading = {
        "text": ClosestReadingText(text),
        "candidates_offered": {name: paths[name] for name in available},
        "chosen": list(chosen),
        "combined": len(chosen) > 1,
        "editor_pseudonym": editor_pseudonym,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "status": "revisable_editorial_choice",
    }
    for c in chosen:
        log_routing_feedback(state=state, field="path_preference",
                             inferred_value=None, chosen_value=c, path=c,
                             decided_by=decided_by)
    logger.info(f"[gate2] {state.doc_id}: combined {len(chosen)} reading(s) "
                f"({', '.join(chosen)}) → {len(text)} chars")
    return text


_CONFIRM_FIELD = "__confirm__"      # the Bestätigen button's custom_id field


def build_view(state: RunState, paths: dict[str, str],
               runners: Optional[dict] = None):
    """Construct the Gate-2 selection view (#313 multi-select).

    Each candidate is a **toggle**: click to add/remove it from the selection
    (green = selected). A **Bestätigen** button finalises — one selected reading is
    used verbatim, several are **combined** (fused, overriding the auto no-merge) —
    then the card collapses (buttons removed, outcome shown) and B/C re-run.

    Selection lives on the RunState (``gate_decisions['gate2_selected']``) so the
    toggles survive a bot restart when #150 rebuilds the view.
    """
    import discord

    comp = compare_paths(paths)

    async def _ack(interaction, content, view):
        try:
            await interaction.response.edit_message(content=content, view=view)
        except Exception as e:
            logger.warning(f"[gate2] {state.doc_id}: card update failed: {e}")

    class _ToggleButton(discord.ui.Button):
        def __init__(self, path: str):
            self.path = path
            selected = path in _selected(state)
            super().__init__(
                label=_label_for(path),
                style=discord.ButtonStyle.success if selected
                      else discord.ButtonStyle.secondary,
                custom_id=f"ah:{state.doc_id}:gate2:{path}",
            )

        async def callback(self, interaction):
            sel = _selected(state)
            if self.path in sel:
                sel.remove(self.path)
            else:
                sel.append(self.path)
            state.gate_decisions["gate2_selected"] = sel
            try:
                state.save()
            except Exception as e:
                logger.warning(f"[gate2] {state.doc_id}: selection save failed: {e}")
            # rebuild the view so the toggled button reflects its new state
            await _ack(interaction, render_vote_card(state, paths),
                       build_view(state, paths, runners=runners))

    class _ConfirmButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="✅ Bestätigen",
                style=discord.ButtonStyle.primary,
                custom_id=f"ah:{state.doc_id}:gate2:{_CONFIRM_FIELD}",
                disabled=not _selected(state),
            )

        async def callback(self, interaction):
            import asyncio
            chosen = _selected(state)
            applied = None
            try:
                if chosen:
                    _user = getattr(interaction, "user", None)
                    applied = apply_combined_choice(
                        state, chosen, paths,
                        editor=str(getattr(_user, "id", "") or ""))
                    state.gate_decisions["gate2_selected"] = []      # clear
                    state.save()
            except Exception as e:
                logger.warning(f"[gate2] {state.doc_id}: confirm failed: {e}")

            # Record the decision as a PREFERENCE over the alternatives that were
            # offered (#332) — not as a reference text. The historian picked the
            # closest of the options WE produced; treating that as ground truth
            # would certify our own errors (#326). Best-effort: never break the
            # click. The raw user id is pseudonymised inside preferences.
            if chosen:
                try:
                    import preferences
                    user = getattr(interaction, "user", None)
                    preferences.record_selection(
                        state, paths, chosen,
                        voter=str(getattr(user, "id", "") or ""))
                except Exception as e:
                    logger.warning(f"[gate2] {state.doc_id}: preference log failed: {e}")
            # collapse: no buttons, just the outcome
            await _ack(interaction, render_decided_card(state, paths, chosen, applied or ""),
                       None)
            if applied is not None and runners and config.AUTO_RESUME_AFTER_GATE:
                try:
                    asyncio.get_running_loop().create_task(
                        asyncio.to_thread(state.resume, runners))
                except Exception as e:
                    logger.warning(f"[gate2] {state.doc_id}: resume scheduling failed: {e}")

    class PathComparisonView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for name in comp["names"]:
                self.add_item(_ToggleButton(name))
            self.add_item(_ConfirmButton())

    return PathComparisonView()
