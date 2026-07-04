"""Tests for #90 (KH-4): federated search agent.

Offline: the MCP client's search_persons is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_90_search_agent.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents import search_agent
from utils import mcp_client
from utils.mcp_client import FederatedResult, PersonResult

run = asyncio.run


def _fed(persons, failed=None):
    return FederatedResult(persons=persons, failed_sources=failed or [])


def test_search_merges_and_ranks(monkeypatch):
    persons = [
        # one person across two sources (shared GND) — high confidence
        PersonResult(source="hls", pid="1", name="Hans Wiler", gnd_id="G1", mention_count=3),
        PersonResult(source="hbls", pid="2", name="Hans von Wiler", gnd_id="G1", mention_count=5),
        # a lone, unrelated person — single-source high but fewer sources/mentions
        PersonResult(source="kf", pid="9", name="Peter Muster", mention_count=1),
    ]

    async def fake_search(query, limit=20):
        return _fed(persons)

    monkeypatch.setattr(mcp_client, "search_persons", fake_search)
    resp = search_agent.search_sync("Wiler")

    assert resp.raw_count == 3
    assert resp.resolved_count == 2
    # the 2-source, high-mention entity ranks first
    assert resp.entities[0].sources == ["hbls", "hls"]
    assert resp.entities[0].confidence == "high"
    assert not resp.failed_sources


def test_search_propagates_failed_sources(monkeypatch):
    async def fake_search(query, limit=20):
        return _fed([PersonResult(source="kf", pid="1", name="X")], failed=["hls", "eos"])

    monkeypatch.setattr(mcp_client, "search_persons", fake_search)
    resp = search_agent.search_sync("X")
    assert set(resp.failed_sources) == {"hls", "eos"}
    assert resp.resolved_count == 1


def test_ranking_confidence_beats_mentions(monkeypatch):
    persons = [
        # medium-confidence merge, high mentions
        PersonResult(source="hls", pid="1", name="Anna Muster", mention_count=50),
        PersonResult(source="kf", pid="2", name="Anna Muster", mention_count=50),
        # high-confidence single source, low mentions
        PersonResult(source="hbls", pid="3", name="Zulu Person", gnd_id="G9", mention_count=1),
    ]

    async def fake_search(query, limit=20):
        return _fed(persons)

    monkeypatch.setattr(mcp_client, "search_persons", fake_search)
    resp = search_agent.search_sync("q")
    # high-confidence entity outranks the higher-mention medium one
    assert resp.entities[0].confidence == "high"


def test_empty_result(monkeypatch):
    async def fake_search(query, limit=20):
        return _fed([])
    monkeypatch.setattr(mcp_client, "search_persons", fake_search)
    resp = search_agent.search_sync("nobody")
    assert resp.resolved_count == 0 and resp.raw_count == 0


def test_async_search_returns_response(monkeypatch):
    async def fake_search(query, limit=20):
        return _fed([PersonResult(source="kf", pid="1", name="Y")])
    monkeypatch.setattr(mcp_client, "search_persons", fake_search)
    resp = run(search_agent.search("Y"))
    assert isinstance(resp, search_agent.SearchResponse) and resp.query == "Y"
