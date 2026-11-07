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
    tool_map maps the client's LOGICAL operation ("search_persons" / "get_person"
             / "search_fulltext") to this server's ACTUAL tool name when they
             differ (e.g. hls prefixes everything `hls_`). Unmapped operations
             fall through to the logical name.
    adapter  optional callable mapping the source's native record -> PersonResult;
             None = already conforms
    notes    free-text provenance / caveats
    basic_auth_config  name of a config attribute holding base64("user:pass")
             for an ``Authorization: Basic`` header; the source fails closed
             when it is empty (hls, #800)
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
    tool_map: dict = field(default_factory=dict)
    adapter: Optional[Callable] = None
    notes: str = ""
    basic_auth_config: Optional[str] = None
    #: The tool that returns one whole document, its id argument, and the
    #: field holding the text (#812): Agent F reads a passage in its document
    #: rather than in a 200-character search snippet.
    document_tool: Optional[str] = None
    document_arg: str = "doc_id"
    document_text_field: str = "text_raw"

    def tool(self, logical: str) -> str:
        """Resolve a logical operation to this source's actual tool name."""
        return self.tool_map.get(logical, logical)

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


# ── Source adapters (native record → common PersonResult aliases) ────────────

def _hbls_adapter(raw: dict) -> dict:
    """Map an HBLS ``search`` record to the common PersonResult aliases.

    HBLS returns biographical-lexicon article records keyed
    ``id/headword/volume/page/snippet/article_text`` (verified live 2026-07-06),
    not the person contract — so rename onto the aliases `_to_person_result`
    understands. ``headword`` is the family/person name.
    """
    entries = []
    if raw.get("volume") and raw.get("page"):
        entries.append(f"Bd. {raw['volume']}, S. {raw['page']}")
    return {
        "id": raw.get("id"),
        "name": raw.get("headword"),
        "notes": raw.get("snippet") or (raw.get("article_text") or "")[:200] or None,
        "entries": entries,
    }


# ── The registry — edit HERE to add a knowledge hub ──────────────────────────
# Every source below is LIVE (confirmed 2026-07-03). To add one, append an
# MCPSource and follow docs/knowledge_hub.md §"Adding a source".
#
# Since #800 (05.11.2026) every source on tei speaks Streamable HTTP at
# /mcp/<name>/mcp; /mcp/<name> alone is a landing page for humans and answers
# 302 (dh-bot's Hermes config uses the /mcp form). The registry kept the old
# paths and EOS's SSE transport, so from then on every federation call of this
# repository failed: Agent C's linking, /search, and Agent F's full text
# ("302 Moved Temporarily", "SSE stream closed"), found on Agent F's first run
# on tei (06.11.2026, #812).
SOURCES: tuple[MCPSource, ...] = (
    MCPSource(
        name="eos",
        title="EOS — HGB Basel (documents & spans)",
        kinds=("person", "place", "org", "fulltext"),
        path="eos/mcp",
        transport="http",
        document_tool="get_document",
        authority=False,
        # eos exposes search_persons but names full-text search `search_text`
        # (verified via tools/list 2026-07-06); no get_person.
        tool_map={"search_fulltext": "search_text"},
        notes="75,447 documents, 893,303 spans. Full-text + mention search.",
    ),
    MCPSource(
        name="hbls",
        title="HBLS — Historisch-Biographisches Lexikon der Schweiz",
        kinds=("person",),
        path="hbls/mcp",
        transport="http",
        authority=True,
        # hbls exposes a generic `search` (args query/limit) returning article
        # records keyed headword/id/snippet (verified live 2026-07-06); map the
        # tool name and normalise the records via _hbls_adapter.
        tool_map={"search_persons": "search"},
        adapter=_hbls_adapter,
        notes="~137k merged person records; GND/Wikidata where available.",
    ),
    MCPSource(
        name="hls",
        title="HLS-DHS — Historisches Lexikon der Schweiz",
        kinds=("person", "place", "org"),
        path="hls/mcp",
        transport="http",
        basic_auth_config="HLS_BASIC",
        authority=True,
        # hls prefixes every tool `hls_` (verified via tools/list 2026-07-06).
        tool_map={"search_persons": "hls_search_persons",
                  "get_person": "hls_get_person",
                  "search_fulltext": "hls_search_fulltext"},
        notes="Person/place authority. Replaces the retired local hls.json dump.",
    ),
    MCPSource(
        name="kf",
        title="KF — Königsfelden register",
        # fulltext since #812: the server's search_fulltext answers with the
        # edition's transcriptions (shelfmark, year, snippet), checked against
        # it on 2026-11-06. HLS has hls_search_fulltext too and is left out on
        # purpose: a lexicon is secondary literature, not a source to quote.
        kinds=("person", "place", "fulltext"),
        path="kf/mcp",
        authority=False,
        transport="http",
        document_tool="get_entry",
        document_arg="entry_id",
        notes="Königsfelden persons, places, register entries.",
    ),
    MCPSource(
        name="humanlineage",
        title="Human Lineage — open genealogical dataset (GEDCOM-X, Zenodo snapshot)",
        kinds=("person",),
        # The streamable-HTTP client POSTs to the bare `url`, and the server is
        # mounted at its public path /mcp/humanlineage/mcp (nginx proxies without
        # rewriting), so the path carries the trailing /mcp. Verified 06.11.2026:
        # POST /mcp/kf answers 302 text/html, POST /mcp/kf/mcp answers the stream.
        path="humanlineage/mcp",
        # The server speaks the contract natively: search_persons(query, limit)
        # answers rows with id/name/life_dates/occupation/hls_id/gnd_id/
        # wikidata_id, get_person(pid) the full record with parents, children
        # and spouses. Beyond the contract it offers resolve_name (ranked
        # candidates with a status and birth/death-year narrowing),
        # get_by_identifier (QID, GND, VIAF, HLS id), get_relatives and
        # get_family_tree — the genealogical walk no other hub has.
        tools=("search_persons", "get_person", "resolve_name", "get_by_identifier",
               "get_relatives", "get_family_tree", "snapshot_info"),
        authority=True,
        transport="http",
        notes="Offline snapshot of one Zenodo version of Human Lineage "
              "(humanlineage.org, concept DOI 10.5281/zenodo.23284943, CC BY-SA "
              "4.0): 2.7 M deceased persons with a persistent identifier, their "
              "Wikidata/VIAF/GND/ISNI/LCCN/BnF ids, HLS ids via Wikidata P902, "
              "and 615k parent-child and couple relationships. An authority on "
              "Wikidata's word, with provenance: every answer carries the snapshot "
              "date. Served from idhefix :8025 (thodel/humanlineage_mcp).",
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
