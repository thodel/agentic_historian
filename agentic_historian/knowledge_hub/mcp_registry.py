"""
knowledge_hub/mcp_registry.py — the Knowledge Hub source registry.

The Knowledge Hub is a *federation of MCP servers*, one per authority source,
NOT a local store. This module is the single, declarative source of truth for
which sources exist and what each one can answer.

Adding a new knowledge hub = adding ONE `MCPSource(...)` entry to `SOURCES`
below (plus the verification steps in docs/knowledge_hub.md). No schema
migration, no changes to agents — every consumer iterates this registry.

Each source promises the common contract (see `PersonResult` in
docs/knowledge_hub.md): a source-tagged person/place record with authority ids
(HLS / GND / Wikidata) where available. When a source's native MCP response
does not already match that shape, point its `adapter` at a mapping function;
`adapter=None` means "already conforms / default pass-through".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import config

# Entity kinds a source can resolve. Consumers filter the federation by kind
# (e.g. Agent C queries only PERSON/PLACE sources for a PERSON mention).
KINDS = ("person", "place", "org", "fulltext")


@dataclass(frozen=True)
class MCPSource:
    """One authority source exposed over MCP.

    name     stable key used in code, logs and PersonResult.source
    path     URL suffix under config.MCP_BASE_URL (ignored if `full_url` set)
    title    human-readable name
    kinds    which entity kinds this source can resolve (subset of KINDS)
    tools    MCP tool names the source is expected to expose
    authority whether the source yields durable authority ids (gnd/wikidata/hls)
    external if True, the MCP is registered outside the tei base (e.g. the
             shared `wikidata` MCP) and `full_url` (or the MCP gateway) applies
    full_url overrides the derived tei URL (for external sources)
    transport MCP wire transport this source speaks: "sse" (legacy SSE: GET
             <url>/sse event stream + POST <url>/messages) or "http" (streamable
             -HTTP: JSON-RPC POSTed to <url> with an Mcp-Session-Id header).
    adapter  optional callable mapping the source's native record -> PersonResult;
             None = already conforms
    notes    free-text provenance / caveats
    """

    name: str
    title: str
    kinds: tuple[str, ...]
    path: str = ""
    tools: tuple[str, ...] = ("search_persons", "get_person", "search_fulltext")
    authority: bool = False
    external: bool = False
    full_url: Optional[str] = None
    transport: str = "sse"
    adapter: Optional[Callable] = None
    notes: str = ""

    @property
    def url(self) -> str:
        """Resolved MCP base URL for this source."""
        if self.full_url:
            return self.full_url.rstrip("/")
        if self.external:
            # Registered via the MCP gateway, not the tei host; resolved by the
            # MCP client at call time rather than a fixed URL.
            return ""
        return f"{config.MCP_BASE_URL}/{self.path.strip('/')}"


# ── The registry — edit HERE to add a knowledge hub ──────────────────────────
# Every source below is LIVE (confirmed 2026-07-03). To add one, append an
# MCPSource and follow docs/knowledge_hub.md §"Adding a source".
SOURCES: tuple[MCPSource, ...] = (
    MCPSource(
        name="eos",
        title="EOS — HGB Basel (documents & spans)",
        kinds=("person", "place", "org", "fulltext"),
        path="eos",
        authority=False,
        notes="75,447 documents, 893,303 spans. Full-text + mention search.",
    ),
    MCPSource(
        name="hbls",
        title="HBLS — Historisch-Biographisches Lexikon der Schweiz",
        kinds=("person",),
        path="hbls",
        authority=True,
        notes="~137k merged person records; GND/Wikidata where available.",
    ),
    MCPSource(
        name="hls",
        title="HLS-DHS — Historisches Lexikon der Schweiz",
        kinds=("person", "place", "org"),
        path="hls",
        authority=True,
        notes="Person/place authority. Replaces the retired local hls.json dump.",
    ),
    MCPSource(
        name="kf",
        title="KF — Königsfelden register",
        kinds=("person", "place"),
        path="kf",
        authority=False,
        transport="http",   # streamable-HTTP server (nginx rewrites /mcp/kf → /mcp)
        notes="Königsfelden persons, places, register entries.",
    ),
    MCPSource(
        name="wikidata",
        title="Wikidata / GND authority",
        kinds=("person", "place", "org"),
        tools=("search_entity", "get_metadata", "execute_sparql"),
        authority=True,
        external=True,
        notes="Shared `wikidata` MCP (gateway-registered) for reconciliation.",
    ),
)


# ── Accessors (what consumers use) ───────────────────────────────────────────

def list_sources(include_external: bool = True) -> list[MCPSource]:
    """All registered sources (optionally excluding external/gateway ones)."""
    return [s for s in SOURCES if include_external or not s.external]


def get_source(name: str) -> MCPSource:
    """Look up one source by its stable name. Raises KeyError if unknown."""
    for s in SOURCES:
        if s.name == name:
            return s
    raise KeyError(f"unknown knowledge-hub source: {name!r} "
                   f"(known: {[s.name for s in SOURCES]})")


def sources_for_kind(kind: str) -> list[MCPSource]:
    """Sources that can resolve a given entity kind (e.g. 'person')."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; valid: {KINDS}")
    return [s for s in SOURCES if kind in s.kinds]


def validate() -> None:
    """Fail fast on a malformed registry (called by tests and at startup).

    Guarantees the invariants every consumer relies on:
      - names are unique and non-empty
      - every kind is valid
      - every non-external source resolves to an https URL
      - every source declares at least one tool and one kind
    """
    seen: set[str] = set()
    for s in SOURCES:
        assert s.name and s.name not in seen, f"duplicate/empty source name: {s.name!r}"
        seen.add(s.name)
        assert s.kinds, f"{s.name}: must declare at least one kind"
        for k in s.kinds:
            assert k in KINDS, f"{s.name}: invalid kind {k!r}"
        assert s.tools, f"{s.name}: must declare at least one MCP tool"
        if not s.external:
            assert s.url.startswith("https://"), f"{s.name}: non-https url {s.url!r}"
