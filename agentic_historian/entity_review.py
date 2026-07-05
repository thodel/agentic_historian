"""
entity_review.py — Gate 3 entity-link review card (HITL-3a, #151).

Only entities that Agent C left **unverified** or **low**-confidence
(``link_method in {none, hls_dhs}``) surface for review — at most 5 per
document. Each gets a select listing the top-3 candidates from the MCP
federation (#87–#92) plus "kein Link". A click sets the chosen authority link on
the entity output.

(The hub variant write-back on confirmation is HITL-3b, #152.)

Pure logic here (filter, fetch candidates, apply, render); the py-cord View is a
thin wrapper.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from knowledge_hub import hub
from runstate import RunState
from utils.entity_resolver import ResolvedEntity

MAX_REVIEW = 5
REVIEW_LINK_METHODS = {"none", "hls_dhs"}
REVIEW_CONFIDENCE = {"unverified", "low"}
REVIEW_TYPES = {"PERSON", "PLACE"}


def needs_review(ent: dict) -> bool:
    return (ent.get("link_method") in REVIEW_LINK_METHODS
            or ent.get("hub_confidence") in REVIEW_CONFIDENCE)


def select_review_entities(entities: list[dict], limit: int = MAX_REVIEW) -> list[dict]:
    """PERSON/PLACE entities that are unverified/low, capped at ``limit``."""
    return [e for e in entities
            if e.get("type") in REVIEW_TYPES and needs_review(e)][:limit]


def fetch_candidates(text: str, k: int = 3) -> list[ResolvedEntity]:
    """Top-k federation candidates for an entity mention (empty on failure)."""
    try:
        from agents import search_agent
        resp = search_agent.search_sync(text, limit=max(k * 2, 5))
    except Exception as e:  # noqa: BLE001 — offline / no VPN
        logger.warning(f"[gate3] candidate fetch failed for '{text}': {e}")
        return []
    return resp.entities[:k]


def build_review_items(entities: list[dict]) -> list[dict]:
    """[{entity, candidates}] for the entities that need review."""
    items = []
    for ent in select_review_entities(entities):
        text = (ent.get("normalised") or ent.get("text") or "").strip()
        items.append({"entity": ent, "candidates": fetch_candidates(text)})
    return items


def _write_variant(find, add, candidate: ResolvedEntity, observed: str) -> None:
    """Load the existing hub record (or start a new one), append the observed
    spelling as a variant, refresh authority ids, and persist."""
    existing = find(candidate.name) or find(observed)
    record = dict(existing) if existing else {}
    record["id"] = record.get("id") or candidate.gnd_id or (
        f"hls:{candidate.hls_id}" if candidate.hls_id else candidate.name)
    record["name"] = record.get("name") or candidate.name
    variants = list(record.get("variants", []))
    if observed and observed != record["name"] and observed not in variants:
        variants.append(observed)
    record["variants"] = variants
    if candidate.gnd_id:
        record["gnd"] = candidate.gnd_id
    if candidate.hls_id:
        record["hls"] = candidate.hls_id
    if candidate.wikidata_id:
        record["wikidata"] = candidate.wikidata_id
    add(record)


def write_variant(ent: dict, candidate: Optional[ResolvedEntity]) -> None:
    """HITL-3b (#152): after a confirmed link, record the document's observed
    spelling as a hub variant of the linked record — so the NEXT document with
    the same spelling links ``hub_exact`` with zero interaction (compounding loop).
    """
    if candidate is None:
        return
    observed = (ent.get("text") or "").strip()
    if not observed:
        return
    if ent.get("type") == "PERSON":
        _write_variant(hub.find_person, hub.add_person, candidate, observed)
    elif ent.get("type") == "PLACE":
        _write_variant(hub.find_place, hub.add_place, candidate, observed)


def apply_entity_link(ent: dict, candidate: Optional[ResolvedEntity]) -> None:
    """Set (or clear) the authority link on an entity from a human choice, and —
    on a confirmed link — write the observed spelling back to the hub (#152)."""
    if candidate is None:                       # "kein Link"
        ent["link_method"] = "human_none"
        ent["hub_confidence"] = "unverified"
        return
    ent["gnd"] = candidate.gnd_id or ""
    ent["hls"] = candidate.hls_id or ""
    ent["wikidata"] = candidate.wikidata_id or ""
    ent["hub_id"] = candidate.gnd_id or (
        str(candidate.hls_id) if candidate.hls_id else candidate.name)
    ent["hub_confidence"] = "high"
    ent["link_method"] = "human_confirmed"
    ent["mcp_sources"] = candidate.sources
    write_variant(ent, candidate)               # compounding loop (#152)


def _cand_label(c: ResolvedEntity) -> str:
    ids = c.gnd_id or (f"HLS {c.hls_id}" if c.hls_id else c.wikidata_id or "")
    src = ",".join(c.sources)
    return f"{c.name}" + (f" ({ids})" if ids else "") + (f" · {src}" if src else "")


def render_review_card(doc_id: str, review_items: list[dict]) -> str:
    if not review_items:
        return f"🔗 **{doc_id}** · alle Entitäten verlinkt — nichts zu prüfen"
    lines = [f"🔗 **{doc_id}** · Entity-Link-Prüfung ({len(review_items)})", ""]
    for it in review_items:
        e = it["entity"]
        lines.append(f"**{e.get('text','')}** ({e.get('type','')}) — "
                     f"_aktuell: {e.get('link_method','?')}_")
        for c in it["candidates"]:
            lines.append(f"   • {_cand_label(c)}")
        if not it["candidates"]:
            lines.append("   • (keine Kandidaten)")
    return "\n".join(lines)


def build_view(state: RunState, review_items: list[dict], runners: Optional[dict] = None):
    """One select per entity (top candidates + 'kein Link'). py-cord lazy."""
    import discord

    class _EntitySelect(discord.ui.Select):
        def __init__(self, index: int, item: dict):
            self.index = index
            self.item = item
            ent = item["entity"]
            options = [
                discord.SelectOption(label=(_cand_label(c))[:100], value=str(i))
                for i, c in enumerate(item["candidates"])
            ] or [discord.SelectOption(label="(keine Kandidaten)", value="none")]
            options.append(discord.SelectOption(label="kein Link", value="none"))
            super().__init__(
                placeholder=f"{ent.get('text','')} → Link wählen",
                options=options, min_values=1, max_values=1,
                custom_id=f"ah:{state.doc_id}:gate3:{index}",
            )

        async def callback(self, interaction):
            val = self.values[0]
            candidate = (None if val == "none"
                         else self.item["candidates"][int(val)])
            apply_entity_link(self.item["entity"], candidate)
            state.gate_decisions.setdefault("entity_links", []).append(
                {"text": self.item["entity"].get("text"), "value": val})
            state.invalidate("entity_link", value=val,
                             user=state.gate_decisions.get("user"))
            state.save()
            await interaction.response.edit_message(
                content=render_review_card(state.doc_id, review_items), view=self.view)

    class EntityReviewView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
            for i, item in enumerate(review_items[:MAX_REVIEW]):
                self.add_item(_EntitySelect(i, item))

    return EntityReviewView()
