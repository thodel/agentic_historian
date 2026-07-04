"""Tests for #92 (KH-6): Agent C entity linking via the MCP federation.

Offline: search_agent.search_sync and the local hub chain are mocked. Run from
the repo root:
    pytest agentic_historian/tests/test_ah_92_agentc_mcp_linking.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
from agents import entity_agent, search_agent
from agents.search_agent import SearchResponse
from utils.entity_resolver import ResolvedEntity


@pytest.fixture(autouse=True)
def _neutralise_local_chain(monkeypatch):
    """Make the local fallback chain a no-op so tests isolate the MCP step."""
    monkeypatch.setattr(entity_agent.hub, "find_person", lambda t: None)
    monkeypatch.setattr(entity_agent.hub, "find_place", lambda t: None)
    monkeypatch.setattr(entity_agent, "_semantic_link", lambda t, k: None)
    monkeypatch.setattr(entity_agent, "_hls_lookup", lambda t, k: None)
    monkeypatch.setattr(config, "ENABLE_MCP_LINKING", True)


def _resp(query, *entities):
    return SearchResponse(query=query, entities=list(entities))


def test_person_linked_via_mcp(monkeypatch):
    resp = _resp("Hans von Wiler",
                 ResolvedEntity(name="Hans von Wiler", sources=["hbls", "hls"],
                                confidence="high", gnd_id="118000", hls_id=12345))
    monkeypatch.setattr(search_agent, "search_sync", lambda q, limit=10: resp)

    ent = {}
    entity_agent._link_entity(ent, "Hans von Wiler", "PERSON")

    assert ent["link_method"] == "mcp_federation"
    assert ent["gnd"] == "118000" and ent["hls"] == 12345
    assert ent["hub_confidence"] == "high"
    assert ent["mcp_sources"] == ["hbls", "hls"]


def test_spurious_top_hit_is_rejected(monkeypatch):
    """Top hit whose name doesn't match the query must NOT be linked."""
    resp = _resp("Hans Wiler",
                 ResolvedEntity(name="Peter Muster", sources=["kf"], confidence="high"))
    monkeypatch.setattr(search_agent, "search_sync", lambda q, limit=10: resp)

    ent = {}
    entity_agent._link_entity(ent, "Hans Wiler", "PERSON")
    assert ent["link_method"] == "none"          # fell through to the (mocked) local chain


def test_empty_federation_falls_back(monkeypatch):
    monkeypatch.setattr(search_agent, "search_sync", lambda q, limit=10: _resp("x"))
    ent = {}
    entity_agent._link_entity(ent, "Unbekannt", "PERSON")
    assert ent["link_method"] == "none"


def test_federation_failure_falls_back(monkeypatch):
    def boom(q, limit=10):
        raise RuntimeError("federation down / no VPN")
    monkeypatch.setattr(search_agent, "search_sync", boom)
    ent = {}
    entity_agent._link_entity(ent, "Hans Wiler", "PERSON")
    assert ent["link_method"] == "none"          # graceful degradation, no raise


def test_disabled_flag_skips_federation(monkeypatch):
    called = []
    monkeypatch.setattr(config, "ENABLE_MCP_LINKING", False)
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: called.append(q) or _resp(q))
    ent = {}
    entity_agent._link_entity(ent, "Hans Wiler", "PERSON")
    assert not called, "federation must not be queried when disabled"
    assert ent["link_method"] == "none"


def test_place_does_not_use_federation(monkeypatch):
    called = []
    monkeypatch.setattr(search_agent, "search_sync",
                        lambda q, limit=10: called.append(q) or _resp(q))
    ent = {}
    entity_agent._link_entity(ent, "Bern", "PLACE")
    assert not called, "PLACE must not go through the person federation"
    assert ent["link_method"] == "none"
