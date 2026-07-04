"""
agents/search_agent.py — federated search agent (KH-4, #90).

Composes the MCP federation client (#87) and the entity resolver (#88) into one
call: query all Knowledge-Hub sources in parallel, merge cross-source matches,
and return unified, source-attributed, confidence-ranked results.

Plain ``asyncio.gather`` in the client is sufficient — no OpenClaw subagents
(per IMPLEMENTATION_PLAN.md → epic #143 sequencing note).
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pydantic import BaseModel, Field

from utils import entity_resolver, mcp_client
from utils.entity_resolver import ResolvedEntity

_CONF_RANK = {"high": 3, "medium": 2, "low": 1}


class SearchResponse(BaseModel):
    query: str
    entities: list[ResolvedEntity] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)
    raw_count: int = 0                 # PersonResults before merge

    @property
    def resolved_count(self) -> int:
        return len(self.entities)


def _rank_key(e: ResolvedEntity):
    """Rank: confidence, then corroboration (sources), then mentions."""
    total_mentions = sum(m.mention_count for m in e.members)
    return (_CONF_RANK.get(e.confidence, 0), len(e.sources), total_mentions)


async def search(query: str, limit: int = 20) -> SearchResponse:
    """Federated person search across all live Knowledge-Hub sources."""
    logger.info(f"[search] federated query: {query!r}")
    fr = await mcp_client.search_persons(query, limit=limit)
    entities = entity_resolver.resolve(fr.persons)
    entities.sort(key=_rank_key, reverse=True)
    if fr.failed_sources:
        logger.warning(f"[search] sources unavailable: {fr.failed_sources}")
    logger.info(f"[search] {len(fr.persons)} hits → {len(entities)} entities")
    return SearchResponse(
        query=query,
        entities=entities,
        failed_sources=fr.failed_sources,
        raw_count=len(fr.persons),
    )


def search_sync(query: str, limit: int = 20) -> SearchResponse:
    """Blocking wrapper for sync callers (CLI, tests)."""
    return asyncio.run(search(query, limit=limit))


def _authority_links(e: ResolvedEntity) -> str:
    links = []
    if e.gnd_id:
        links.append(f"[GND](https://d-nb.info/gnd/{e.gnd_id})")
    if e.hls_id:
        links.append(f"[HLS](https://www.hls-dhs-dss.ch/de/{e.hls_id})")
    if e.wikidata_id:
        links.append(f"[WD](https://www.wikidata.org/entity/{e.wikidata_id})")
    return " · ".join(links)


def format_response(resp: SearchResponse, top: int = 10, max_chars: int = 1900) -> str:
    """Render a SearchResponse as a Discord message (pure; presentation only)."""
    if not resp.entities:
        msg = f"🔎 **«{resp.query}»** — keine Treffer"
        if resp.failed_sources:
            msg += f"\n⚠️ Quellen nicht erreichbar: {', '.join(resp.failed_sources)}"
        return msg

    header = (f"🔎 **«{resp.query}»** — {resp.resolved_count} Treffer aus "
              f"{len({s for e in resp.entities for s in e.sources})} Quelle(n) "
              f"({resp.raw_count} Roh-Datensätze)")
    lines = [header, ""]
    for e in resp.entities[:top]:
        dates = f" ({e.life_dates})" if e.life_dates else ""
        line = f"**{e.name}**{dates} · _{e.confidence}_ · {', '.join(e.sources)}"
        if e.needs_review:
            line += " ⚠️"
        links = _authority_links(e)
        if links:
            line += f"\n   {links}"
        lines.append(line)

    if resp.resolved_count > top:
        lines.append(f"\n… und {resp.resolved_count - top} weitere")
    if resp.failed_sources:
        lines.append(f"\n⚠️ Quellen nicht erreichbar: {', '.join(resp.failed_sources)}")

    out = "\n".join(lines)
    return out if len(out) <= max_chars else out[:max_chars - 1] + "…"
