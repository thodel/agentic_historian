"""Tests for #87 (KH-1): MCP client + shared PersonResult schema.

Offline: the transport seam (_call_tool) is injected/mocked — no network, and
no pytest-asyncio dependency (coroutines are driven with asyncio.run).
Run from the repo root:
    pytest agentic_historian/tests/test_ah_87_mcp_client.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from utils import mcp_client as mc
from knowledge_hub import mcp_registry as reg

run = asyncio.run


# ── Contract ─────────────────────────────────────────────────────────────────

def test_person_result_contract():
    p = mc.PersonResult(source="hls", pid="1", name="Hans")
    assert p.variants == [] and p.mention_count == 0 and p.gnd_id is None


def test_normalisation_maps_common_aliases():
    s = reg.get_source("kf")  # adapter-free source → exercises default aliasing
    raw = {"id": "kf-42", "n": "Johann von Wiler", "y": "1300–1370",
           "gnd": "118?", "v": ["Hans"], "c": 7}
    pr = mc._to_person_result(s, raw)
    assert pr.source == "kf" and pr.pid == "kf-42"
    assert pr.name == "Johann von Wiler" and pr.life_dates == "1300–1370"
    assert pr.gnd_id == "118?" and pr.variants == ["Hans"] and pr.mention_count == 7


def test_hbls_adapter_maps_article_record():
    """hbls returns article records (headword/id/snippet); the registry adapter
    normalises them onto the PersonResult contract."""
    s = reg.get_source("hbls")
    raw = {"id": 10006, "headword": "LUTTRINGSHAUSEN", "volume": 4, "page": 751,
           "snippet": "…Johann Jakob, Miniaturmaler…", "article_text": "Urspr. …"}
    pr = mc._to_person_result(s, raw)
    assert pr.source == "hbls" and pr.pid == "10006"
    assert pr.name == "LUTTRINGSHAUSEN"
    assert "Johann Jakob" in (pr.notes or "")
    assert pr.entries == ["Bd. 4, S. 751"]


# ── Federated search: parallel fan-out + normalisation ───────────────────────

def test_search_persons_fans_out_and_tags_source():
    calls = []

    async def fake_call(source, tool, args):
        calls.append((source.name, tool, args))
        return {"results": [{"id": f"{source.name}-1", "name": f"Johann@{source.name}"}]}

    fr = run(mc.search_persons("Johann", limit=5, call_tool=fake_call))

    queried = {c[0] for c in calls}
    person_sources = {s.name for s in reg.sources_for_kind("person") if not s.external}
    assert queried == person_sources
    # each source is called with ITS resolved tool name (hls prefixes `hls_`).
    assert all(t == reg.get_source(name).tool("search_persons")
               and a == {"query": "Johann", "limit": 5}
               for name, t, a in calls)
    assert {p.source for p in fr.persons} == person_sources
    assert not fr.failed_sources


def test_partial_failure_is_flagged_not_fatal():
    async def flaky(source, tool, args):
        if source.name == "hls":
            raise TimeoutError("hls slow")
        return {"results": [{"id": "x", "name": "Y"}]}

    fr = run(mc.search_persons("Johann", call_tool=flaky))
    assert "hls" in fr.failed_sources
    assert fr.persons and "hls" not in {p.source for p in fr.persons}


def test_external_sources_excluded_from_fanout():
    seen = []

    async def spy(source, tool, args):
        seen.append(source.name)
        return {"results": []}

    run(mc.search_persons("x", call_tool=spy))
    assert "wikidata" not in seen  # external MCP is not fanned out here


def test_get_person_returns_record_or_none():
    async def fake_call(source, tool, args):
        assert tool == source.tool("get_person") and args == {"pid": "42"}
        return {"id": "42", "name": "Hans", "relationships": [{"rel": "father"}],
                "geo": [46.9, 7.4]}

    rec = run(mc.get_person("hls", "42", call_tool=fake_call))
    assert isinstance(rec, mc.PersonRecord)
    assert rec.name == "Hans" and rec.relationships == [{"rel": "father"}]
    assert rec.geo == (46.9, 7.4)


def test_get_person_none_on_empty():
    async def empty(source, tool, args):
        return {}
    assert run(mc.get_person("hls", "nope", call_tool=empty)) is None


def test_search_fulltext_only_fulltext_sources():
    seen = []

    async def spy(source, tool, args):
        seen.append(source.name)
        return {"hits": [{"doc_id": "d1", "snippet": "…arme lüt…", "score": 0.8}]}

    hits = run(mc.search_fulltext("arme lüt", call_tool=spy))
    ft_sources = {s.name for s in reg.sources_for_kind("fulltext") if not s.external}
    assert set(seen) == ft_sources
    assert hits and hits[0].snippet == "…arme lüt…"


def test_search_persons_sync_wrapper(monkeypatch):
    async def fake_call(source, tool, args):
        return {"results": [{"id": "1", "name": "Z"}]}
    monkeypatch.setattr(mc, "_call_tool", fake_call)
    fr = mc.search_persons_sync("Z")
    assert isinstance(fr, mc.FederatedResult) and fr.persons
