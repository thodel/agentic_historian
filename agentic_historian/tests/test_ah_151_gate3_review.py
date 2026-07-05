"""Tests for #151 (HITL-3a): Gate 3 entity-link review card.

Offline: the federation search is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_151_gate3_review.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import entity_review as er
from agents import search_agent
from agents.search_agent import SearchResponse
from utils.entity_resolver import ResolvedEntity
from runstate import RunState, STAGES, DONE


def _ent(text, type="PERSON", link_method="none", conf="unverified"):
    return {"text": text, "type": type, "normalised": text,
            "link_method": link_method, "hub_confidence": conf}


def _state():
    rs = RunState(doc_id="saa-0428")
    for s in STAGES:
        rs.stage_status[s] = DONE
    return rs


# ── filtering: only unverified/low PERSON/PLACE ──────────────────────────────

def test_needs_review():
    assert er.needs_review(_ent("x", link_method="none")) is True
    assert er.needs_review(_ent("x", link_method="hls_dhs", conf="low")) is True
    assert er.needs_review(_ent("x", link_method="hub_exact", conf="high")) is False
    assert er.needs_review(_ent("x", link_method="mcp_federation", conf="medium")) is False


def test_select_review_entities_filters_and_caps():
    ents = [
        _ent("Hans", link_method="none"),                 # review
        _ent("Bern", type="PLACE", link_method="hls_dhs", conf="low"),  # review
        _ent("Peter", link_method="hub_exact", conf="high"),            # linked → skip
        _ent("arme lüt", type="SOCIAL_GROUP", link_method="none"),      # wrong type → skip
    ] + [_ent(f"P{i}", link_method="none") for i in range(10)]          # many
    review = er.select_review_entities(ents, limit=5)
    assert len(review) == 5
    assert all(e["type"] in ("PERSON", "PLACE") and er.needs_review(e) for e in review)
    assert "Peter" not in [e["text"] for e in review]


# ── candidate fetch (federation mocked) ──────────────────────────────────────

def test_fetch_candidates_top_k(monkeypatch):
    ents = [ResolvedEntity(name=f"Cand {i}", sources=["hls"], gnd_id=f"G{i}")
            for i in range(5)]
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: SearchResponse(query=q, entities=ents))
    cands = er.fetch_candidates("Hans", k=3)
    assert len(cands) == 3 and cands[0].name == "Cand 0"


def test_fetch_candidates_empty_on_failure(monkeypatch):
    def boom(q, limit=10):
        raise RuntimeError("no VPN")
    monkeypatch.setattr(search_agent, "search_sync", boom)
    assert er.fetch_candidates("Hans") == []


# ── apply link ───────────────────────────────────────────────────────────────

def test_apply_entity_link_sets_authority_ids():
    ent = _ent("Hans Wiler")
    cand = ResolvedEntity(name="Hans von Wiler", sources=["hls", "hbls"],
                          gnd_id="118000", hls_id=12345)
    er.apply_entity_link(ent, cand)
    assert ent["link_method"] == "human_confirmed" and ent["hub_confidence"] == "high"
    assert ent["gnd"] == "118000" and ent["hls"] == 12345
    assert ent["mcp_sources"] == ["hls", "hbls"]


def test_apply_entity_link_none_is_kein_link():
    ent = _ent("Unklar")
    er.apply_entity_link(ent, None)
    assert ent["link_method"] == "human_none" and ent["hub_confidence"] == "unverified"


# ── rendering ────────────────────────────────────────────────────────────────

def test_render_review_card(monkeypatch):
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: SearchResponse(query=q, entities=[
                            ResolvedEntity(name="Hans von Wiler", sources=["hls"], gnd_id="118000")]))
    items = er.build_review_items([_ent("Hans Wiler")])
    card = er.render_review_card("saa-0428", items)
    assert "Entity-Link-Prüfung" in card and "Hans Wiler" in card and "Hans von Wiler" in card


def test_render_nothing_to_review():
    assert "nichts zu prüfen" in er.render_review_card("d", [])


# ── Discord View ─────────────────────────────────────────────────────────────

def test_build_view_has_select_per_entity(monkeypatch):
    import discord
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: SearchResponse(query=q, entities=[
                            ResolvedEntity(name="C", sources=["kf"])]))
    items = er.build_review_items([_ent("Hans"), _ent("Bern", type="PLACE")])

    async def build():
        return er.build_view(_state(), items)
    view = asyncio.run(build())
    selects = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(selects) == 2
    assert {s.custom_id for s in selects} == {"ah:saa-0428:gate3:0", "ah:saa-0428:gate3:1"}
    assert view.timeout is None
