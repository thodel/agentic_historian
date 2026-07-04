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
