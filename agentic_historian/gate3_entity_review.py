"""
gate3_entity_review.py — Gate 3 entity-link review + hub variant write-back
(HITL-3b, #152).

After Agent C has run (Phase 4), this module surfaces any PERSON/PLACE entity
that came back with link_method in {none, hls_dhs} (i.e. "unverified"/"low")
for a one-click link confirmation. A click:

1.  Sets the link ids (wikidata / gnd / hls) on the entity output record.
2.  Writes the observed spelling into the hub as a **variant** of the linked
    record, so the next document that mentions the same person/place gets an
    immediate ``hub_exact`` hit and needs zero interaction.

The compounding loop: every click permanently improves future linking.
``hub.add_person()`` / ``hub.add_place()`` handle both the "existing record →
append variant" and the "new record → create it" cases (dedup by id; append if
id already exists but variant is new).

Gating rule: Gate 3 is non-blocking by design (per HITL plan §gating rules).
It renders as an optional review card. Only entities with ≥1 MCP candidate
are surfaced (the MCP federation in mcp_registry.py is the source of truth for
candidates).

Pure logic (entity extraction, variant construction, gating, render, choice)
lives here and is unit-tested offline; ``DiscordView`` is a thin py-cord wrapper.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from loguru import logger

from knowledge_hub import hub
from runstate import RunState

# ── entity extraction ────────────────────────────────────────────────────────

# Entities with these link_methods need a human review click.
_REVIEW_LINK_METHODS = {"none", "hls_dhs"}
# Entities with these status values are considered low-confidence.
_LOW_CONFIDENCE = {"unverified", "low"}


def gate3_entities(state: RunState) -> list[dict]:
    """Extract PERSON/PLACE entities from Agent C's output that need review.

    Returns a list of entity dicts (copies, not the originals) with at least:
      text         — the raw mention spelling
      type         — PERSON | PLACE
      link_method  — current linking method
      candidates   — top-N candidate records from the MCP federation (list)

    An empty list means no entity needs review (pipeline can proceed).
    """
    artifact = state.artifacts.get("agent_c")
    if not artifact:
        return []

    # Agent C stores either a JSON string or a dict.
    if isinstance(artifact, str):
        try:
            artifact = json.loads(artifact)
        except Exception:
            return []

    entities = artifact.get("entities", []) if isinstance(artifact, dict) else []

    review: list[dict] = []
    for ent in entities:
        if ent.get("type") not in ("PERSON", "PLACE"):
            continue
        lm = ent.get("link_method", "")
        conf = ent.get("hub_confidence", "")
        needs_review = lm in _REVIEW_LINK_METHODS or conf in _LOW_CONFIDENCE
        if not needs_review:
            continue

        candidates = _fetch_candidates(ent.get("text", ""), ent.get("type"))
        review.append({
            "text": ent.get("text", ""),
            "normalized": ent.get("normalized", ent.get("text", "")),
            "type": ent.get("type"),
            "link_method": lm,
            "hub_confidence": conf,
            "candidates": candidates,
            # current link ids (may be empty)
            "hls": ent.get("hls", ""),
            "gnd": ent.get("gnd", ""),
            "wikidata": ent.get("wikidata", ""),
        })

    return review


def _fetch_candidates(text: str, ent_type: str) -> list[dict]:
    """Top-5 candidates for ``text`` from the MCP federation.

    Candidates are the raw records returned by each live MCPSource that
    declares the given entity kind.  Each candidate dict has at least:
      source   — the MCPSource.name
      name     — canonical name in that authority
      hls_id   — HLS-DHS id (if available)
      gnd_id   — GND id (if available)
      wikidata_id  — Wikidata id (if available)
    """
    # Lazy import to avoid circular dependency.
    from knowledge_hub.mcp_registry import sources_for_kind

    candidates: list[dict] = []
    for src in sources_for_kind(ent_type.lower()):
        try:
            # Each source has its own MCP client; call via agents/entity_agent
            # _mcp_search which already handles the protocol (HTTP POST + JSON).
            # Import lazily so this module stays importable without a live MCP.
            from agents import entity_agent as ea
            recs = ea._mcp_search(text, ent_type, sources=[src.name])
            for r in recs[:5]:
                r["source"] = src.name
                candidates.append(r)
        except Exception as e:
            logger.debug(f"[gate3] _fetch_candidates({src.name!r}) failed: {e}")
        if len(candidates) >= 5:
            break

    return candidates[:5]


# ── variant write-back ───────────────────────────────────────────────────────

def _variant_entry(entity: dict, choice: dict) -> dict:
    """Build the variant dict to append to the hub record identified by ``choice``.

    ``entity`` is the reviewed entity (from ``gate3_entities``).
    ``choice`` is the selected candidate dict (with at least ``id`` or
    ``hls_id`` / ``gnd_id`` / ``wikidata_id``).
    Returns a minimal hub-format dict with name + variants.

    The hub record ``name`` is the authority's canonical name (from ``choice``);
    the observed spelling from the document goes into ``variants``.
    """
    canonical_name = choice.get("name", entity.get("normalized", entity.get("text", "")))
    observed_spelling = entity.get("normalized", entity.get("text", ""))
    return {
        "name": canonical_name,
        "variants": [observed_spelling],
    }


def write_variants(state: RunState, choices: dict[int, dict]) -> None:
    """Apply the historian's link choices and persist variant spellings to hub.

    Args:
        state:   the RunState whose agent_c artifact to update.
        choices: ``{entity_index: selected_candidate}`` — the result of a
                 human clicking through the Gate 3 card.
                 selected_candidate None  → "kein Link" / skip
                 selected_candidate dict  → write hub variant + update entity ids

    For each entity that received a non-None candidate:
      1. Update the entity's link ids (hls / gnd / wikidata) in the agent_c
         artifact (so the document record reflects the confirmed link).
      2. Call ``hub.add_person()`` / ``hub.add_place()`` with the variant entry
         derived from the selected candidate.  If the record does not yet exist
         in hub it is created; if it exists, the observed spelling is appended
         as a new variant (deduped by the hub's add_person/add_place).

    The RunState is NOT saved here — the caller (bot.py callback) does that.
    """
    entities = _get_artifact_entities(state)
    dirty = False

    for idx, chosen in choices.items():
        if idx < 0 or idx >= len(entities):
            logger.warning(f"[gate3] invalid entity index {idx}, skipping")
            continue
        if chosen is None:
            continue          # "kein Link" — nothing to write

        ent = entities[idx]
        ent_type = ent.get("type", "").lower()
        # Build the minimal hub record with the observed spelling as a variant.
        hub_entry: dict[str, Any] = {
            "name": chosen.get("name", ent.get("text", "")),
            "variants": [ent.get("normalized", ent.get("text", ""))],
            "notes": f"added via Gate 3 click ({state.doc_id})",
        }

        try:
            if ent_type == "person":
                existing = hub.find_person(hub_entry["name"])
                if existing:
                    hub_entry.setdefault("id", existing.get("id"))
                    hub_entry.setdefault("wikidata", existing.get("wikidata", ""))
                    hub_entry.setdefault("gnd", existing.get("gnd", ""))
                    hub_entry.setdefault("hls", existing.get("hls", ""))
                # Candidate ids override only when they are non-empty.
                for k in ("wikidata", "gnd", "hls"):
                    v = chosen.get(k) or chosen.get(f"{k}_id", "")
                    if v:
                        hub_entry[k] = v
                hub.add_person(hub_entry)
                logger.info(f"[gate3] wrote variant for person {hub_entry['name']!r}")
            elif ent_type == "place":
                existing = hub.find_place(hub_entry["name"])
                if existing:
                    hub_entry.setdefault("id", existing.get("id"))
                    hub_entry.setdefault("wikidata", existing.get("wikidata", ""))
                    hub_entry.setdefault("gnd", existing.get("gnd", ""))
                    hub_entry.setdefault("hls", existing.get("hls", ""))
                for k in ("wikidata", "gnd", "hls"):
                    v = chosen.get(k) or chosen.get(f"{k}_id", "")
                    if v:
                        hub_entry[k] = v
                hub.add_place(hub_entry)
                logger.info(f"[gate3] wrote variant for place {hub_entry['name']!r}")
        except Exception as e:
            logger.warning(f"[gate3] hub write-back failed for {ent.get('text')!r}: {e}")
            continue

        # Propagate confirmed ids back into the entity record.
        ent["hls"] = chosen.get("hls", chosen.get("hls_id", ""))
        ent["gnd"] = chosen.get("gnd", chosen.get("gnd_id", ""))
        ent["wikidata"] = chosen.get("wikidata", chosen.get("wikidata_id", ""))
        ent["hub_confidence"] = "high"
        ent["link_method"] = f"gate3_confirmed:{chosen.get('source', 'unknown')}"
        dirty = True

    if dirty:
        _set_artifact_entities(state, entities)
        state.gate_decisions["gate3"] = {
            "reviewed": len(choices),
            "linked": sum(1 for c in choices.values() if c is not None),
        }


# ── internal artifact helpers ────────────────────────────────────────────────

def _get_artifact_entities(state: RunState) -> list[dict]:
    artifact = state.artifacts.get("agent_c", {})
    if isinstance(artifact, str):
        artifact = json.loads(artifact)
    if isinstance(artifact, dict):
        return artifact.get("entities", [])
    return []


def _set_artifact_entities(state: RunState, entities: list[dict]) -> None:
    artifact = state.artifacts.get("agent_c", {})
    if isinstance(artifact, str):
        artifact = json.loads(artifact)
    if isinstance(artifact, dict):
        artifact["entities"] = entities
    state.artifacts["agent_c"] = artifact


# ── render ───────────────────────────────────────────────────────────────────

def render_gate3_card(state: RunState, entities: Optional[list[dict]] = None) -> str:
    """Render Gate 3 as a compact Discord embed (presentation only).

    Shows one line per entity needing review, the top candidate name, and the
    source.  If entities is None, extracts them from state.artifacts["agent_c"].
    """
    if entities is None:
        entities = gate3_entities(state)

    if not entities:
        return (f"✅ **{state.doc_id}** · Gate 3 — keine Entitäten zur Prüfung\n"
                "Alle Personen/Orte wurden automatisch verlinkt.")

    lines = [f"🔗 **{state.doc_id}** · Gate 3 — Entitäten-Verlinkung", "",
             "_Nur Entitäten mit geringer Konfidenz werden angezeigt._", ""]
    for i, ent in enumerate(entities):
        text = ent.get("text", "?")
        normalized = ent.get("normalized", text)
        cans = ent.get("candidates", [])
        top = cans[0] if cans else None
        cand_str = f"→ {top.get('name', '?')} ({top.get('source', '')})" if top else "— keine Kandidaten"
        lines.append(f"`{i+1}.` **{text}** ({ent.get('type', '').lower()})")
        lines.append(f"   {cand_str}")
        lines.append("")
    return "\n".join(lines)


# ── Discord View (thin wrapper) ──────────────────────────────────────────────

def build_gate3_view(
    state: RunState,
    entities: Optional[list[dict]] = None,
    runners: Optional[dict] = None,
):
    """Build the interactive Gate3View (one Select per entity, max 5).

    Each Select offers up to 3 top MCP candidates + "kein Link" as options.
    Clicking Apply commits all choices at once (single interaction token).
    """
    import discord

    if entities is None:
        entities = gate3_entities(state)

    if not entities:
        return None

    entity_list = entities[:5]          # cap per gate3 design

    class _EntitySelect(discord.ui.Select):
        def __init__(self, idx: int, text: str, candidates: list[dict]):
            self.idx = idx
            self.ent_text = text
            options = []
            for c in candidates[:3]:
                label = f"{c.get('name', '?')} ({c.get('source', '')})"
                options.append(
                    discord.SelectOption(label=label[:100], value=str(idx)))
            options.append(
                discord.SelectOption(label="— kein Link", value=f"{idx}:none"))
            super().__init__(
                placeholder=f"#{idx+1}: {text[:40]}",
                options=options,
                custom_id=f"ah:{state.doc_id}:gate3:{idx}",
                min_values=1,
                max_values=1,
            )

        async def callback(self, interaction):
            pass   # deferred — see Apply below

    class ApplyButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="✅ Verlinkung bestätigen",
                style=discord.ButtonStyle.success,
                custom_id=f"ah:{state.doc_id}:gate3:apply",
            )

        async def callback(self, interaction):
            # Collect all selected values from the Select children.
            choices: dict[int, dict] = {}
            for child in self.view.children:
                if isinstance(child, _EntitySelect) and child.values:
                    raw = child.values[0]
                    if raw.endswith(":none"):
                        choices[child.idx] = None
                    else:
                        idx = int(raw)
                        if 0 <= idx < len(entity_list):
                            choices[idx] = entity_list[idx].get("candidates", [{}])[0]

            write_variants(state, choices)
            state.save()
            if runners:
                state.resume(runners)

            await interaction.response.edit_message(
                content=render_gate3_card(state, entity_list),
                view=None,   # card is final after apply
            )

    class Gate3View(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for i, ent in enumerate(entity_list):
                cans = ent.get("candidates", [])
                if cans:
                    self.add_item(_EntitySelect(i, ent.get("text", ""), cans))
            self.add_item(ApplyButton())

    return Gate3View()