"""
utils/mcp_client.py — shared async client for the Knowledge-Hub MCP federation.

Reads the declarative source registry (``knowledge_hub/mcp_registry.py``) and
queries each source's tools, normalising every response to the common
``PersonResult`` contract (docs/knowledge_hub.md). Adding a source is a registry
edit — this client automatically includes it.

Partial failure is normal: a slow/failing source is skipped and reported in
``FederatedResult.failed_sources``, never fatal.

KH-1 (#87). This module is the transport + normalisation layer only; the
cross-source resolver/merger is KH-2 (#88) and lives elsewhere.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import BaseModel, Field

import config
from knowledge_hub import mcp_registry as reg


# ── Common contract (docs/knowledge_hub.md) ──────────────────────────────────

class PersonResult(BaseModel):
    source: str                       # registry name: "hls" | "hbls" | "kf" | ...
    pid: str                          # source-local id
    name: str
    forename: Optional[str] = None
    surname: Optional[str] = None
    life_dates: Optional[str] = None  # "1300–1370" | "fl. 1348"
    occupation: Optional[str] = None
    hls_id: Optional[int] = None
    gnd_id: Optional[str] = None
    wikidata_id: Optional[str] = None
    variants: list[str] = Field(default_factory=list)
    mention_count: int = 0
    entries: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class PersonRecord(PersonResult):
    """Full authority record for a single person."""
    relationships: list[dict] = Field(default_factory=list)
    geo: Optional[tuple[float, float]] = None
    all_entries: list[dict] = Field(default_factory=list)


class TextHit(BaseModel):
    source: str
    doc_id: str
    snippet: str
    score: float = 0.0


class FederatedResult(BaseModel):
    """Results plus which sources failed — partial-failure transparency."""
    persons: list[PersonResult] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)


class MCPError(Exception):
    """Raised when an MCP source returns a JSON-RPC error."""


# Type of the injectable transport seam.
CallTool = Callable[[reg.MCPSource, str, dict], Awaitable[Any]]


# ── Transport seam (mock this in tests) ──────────────────────────────────────

async def _call_tool(source: reg.MCPSource, tool: str, arguments: dict) -> Any:
    """Call one MCP tool on one source and return the raw result payload.

    Uses MCP streamable-HTTP JSON-RPC (``tools/call``). Kept as the single wire
    seam so the higher-level logic is transport-agnostic and unit-testable.

    NOTE: the exact endpoint/response envelope must be verified against a live
    server inside the VPN; adjust only this function if it differs.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    headers = {"Accept": "application/json, text/event-stream"}
    if source.external:
        raise MCPError(f"{source.name}: external MCP not reachable via this client")
    async with httpx.AsyncClient(timeout=config.MCP_TIMEOUT) as client:
        resp = await client.post(source.url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise MCPError(f"{source.name}: {data['error']}")
    # MCP tools/call wraps the tool output in "result"; tolerate a bare payload.
    return data.get("result", data) if isinstance(data, dict) else data


def _items(raw: Any) -> list[dict]:
    """Coerce a tool result into a list of record dicts."""
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        for key in ("results", "persons", "hits", "items"):
            if isinstance(raw.get(key), list):
                return [r for r in raw[key] if isinstance(r, dict)]
    return []


def _to_person_result(source: reg.MCPSource, raw: dict) -> PersonResult:
    """Normalise a source's native person record to ``PersonResult``.

    Applies the source's registry adapter if present; otherwise a best-effort
    mapping over the common field aliases documented in docs/knowledge_hub.md.
    """
    if source.adapter:
        raw = source.adapter(raw)

    def pick(*keys):
        for k in keys:
            if raw.get(k) not in (None, ""):
                return raw[k]
        return None

    return PersonResult(
        source=source.name,
        pid=str(pick("pid", "id", "n") or ""),
        name=str(pick("name", "n", "label") or ""),
        forename=pick("forename", "given"),
        surname=pick("surname", "family"),
        life_dates=pick("life_dates", "y", "dates"),
        occupation=pick("occupation", "occ"),
        hls_id=pick("hls_id", "hls"),
        gnd_id=pick("gnd_id", "gnd"),
        wikidata_id=pick("wikidata_id", "wd"),
        variants=list(pick("variants", "v") or []),
        mention_count=int(pick("mention_count", "c") or 0),
        entries=list(pick("entries") or []),
        notes=pick("notes"),
    )


# ── Public API ───────────────────────────────────────────────────────────────

async def search_persons(
    query: str,
    limit: int = 20,
    call_tool: Optional[CallTool] = None,
) -> FederatedResult:
    """Search every PERSON-capable (non-external) source in parallel.

    Returns a ``FederatedResult`` with normalised ``PersonResult``s and the
    names of any sources that failed/timed out (they are skipped, not fatal).
    """
    ct = call_tool or _call_tool
    sources = [s for s in reg.sources_for_kind("person") if not s.external]

    async def _one(s: reg.MCPSource):
        try:
            raw = await ct(s, "search_persons", {"query": query, "limit": limit})
            return s.name, [_to_person_result(s, r) for r in _items(raw)], None
        except Exception as e:  # timeout, HTTP, JSON-RPC, adapter — all non-fatal
            return s.name, [], str(e)

    fr = FederatedResult()
    for name, persons, err in await asyncio.gather(*[_one(s) for s in sources]):
        if err is not None:
            fr.failed_sources.append(name)
        else:
            fr.persons.extend(persons)
    return fr


async def get_person(
    source: str,
    pid: str,
    call_tool: Optional[CallTool] = None,
) -> Optional[PersonRecord]:
    """Fetch the full record for one person from one source (or None)."""
    ct = call_tool or _call_tool
    s = reg.get_source(source)
    try:
        raw = await ct(s, "get_person", {"pid": pid})
    except Exception:
        return None
    if not isinstance(raw, dict) or not raw:
        return None
    base = _to_person_result(s, raw)
    return PersonRecord(**base.model_dump(),
                        relationships=list(raw.get("relationships") or []),
                        geo=raw.get("geo"),
                        all_entries=list(raw.get("all_entries") or []))


async def search_fulltext(
    query: str,
    limit: int = 20,
    call_tool: Optional[CallTool] = None,
) -> list[TextHit]:
    """Full-text search across sources that expose ``search_fulltext``."""
    ct = call_tool or _call_tool
    sources = [s for s in reg.sources_for_kind("fulltext") if not s.external]

    async def _one(s: reg.MCPSource):
        try:
            raw = await ct(s, "search_fulltext", {"query": query, "limit": limit})
            return [
                TextHit(source=s.name,
                        doc_id=str(r.get("doc_id") or r.get("id") or ""),
                        snippet=str(r.get("snippet") or r.get("text") or ""),
                        score=float(r.get("score") or 0.0))
                for r in _items(raw)
            ]
        except Exception:
            return []

    hits: list[TextHit] = []
    for group in await asyncio.gather(*[_one(s) for s in sources]):
        hits.extend(group)
    return hits


# ── Sync convenience (for non-async callers, e.g. Agent C) ───────────────────

def search_persons_sync(query: str, limit: int = 20) -> FederatedResult:
    """Blocking wrapper around :func:`search_persons` for sync call sites."""
    return asyncio.run(search_persons(query, limit=limit))
