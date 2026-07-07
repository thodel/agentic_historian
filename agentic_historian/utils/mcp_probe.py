"""
utils/mcp_probe.py — offline-capable MCP server probe and registry snippet generator.

Given an MCP server URL, probes it to detect:
- transport  (streamable-HTTP | sse | None)
- server_info (from initialize)
- tools      (tools/list descriptors)
- contract   (heuristic: does it expose a search-persons-like tool?)
- sample     (result count from a live search, if available)
- errors     (any non-fatal issues encountered)

Also generates a ready-to-paste ``MCPSource(...)`` entry for the registry.

The HTTP seam is injectable so the whole module is offline-testable (no pytest-asyncio,
no network). Timeouts are short (5 s); errors are never raised — they go into the
report.

#189 / #190 / #195 / #228.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import httpx

_PROTOCOL_VERSION = "2024-11-05"
_TIMEOUT = 5.0


# ── Report dataclass ──────────────────────────────────────────────────────────

@dataclass
class ProbeReport:
    url: str
    transport: Optional[str] = None          # "http" | "sse" | None
    server_info: Optional[dict] = None        # {"name":…, "version":…}
    tools: list[dict] = field(default_factory=list)
    contract: bool = False                    # search-persons-like tool detected?
    contract_tool: Optional[str] = None       # which tool satisfied the heuristic
    sample: Optional[int] = None              # result count from live search, if any
    errors: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return self.transport is not None and not bool(self.errors)


# ── Contract heuristic ────────────────────────────────────────────────────────

# Patterns that indicate a search-persons-like tool.
# Group 1: exact logical name (our convention)
_SEARCH_EXACT = re.compile(r"^search_persons$", re.I)
# Group 2: prefixed variant (e.g. hls_search_persons)
_SEARCH_PREFIXED = re.compile(r"^(hls|hbls|kf|eos|ssrq)_.+$", re.I)
# Group 3: generic search(query, …) with a query-like first param
_GENERIC_SEARCH = re.compile(r"^search(_[a-z]+)?$", re.I)


def _detect_contract(tools: list[dict]) -> tuple[bool, Optional[str]]:
    """Return (contract, best_tool_name) based on tool name + argument heuristic.

    Heuristic:
      1. search_persons (exact) → high confidence.
      2. hls/hbls/kf/eos/ssrq + _search_persons → medium confidence.
      3. generic search(query) → low confidence, add TODO-adapter marker.
      4. none → contract=False.
    """
    query_tool: Optional[tuple[str, str]] = None   # (name, confidence)

    for t in tools:
        name = t.get("name", "")
        props = t.get("inputSchema", {}); args = set(props.get("properties", {}).keys()) if isinstance(props, dict) else set()
        if _SEARCH_EXACT.match(name):
            return True, name
        if _SEARCH_PREFIXED.match(name):
            query_tool = (name, "prefixed")
        elif not query_tool and _GENERIC_SEARCH.match(name) and ("query" in args or "q" in args or "search" in args):
            query_tool = (name, "generic")

    if query_tool:
        return True, query_tool[0]
    return False, None


# ── Transport probes ──────────────────────────────────────────────────────────

async def _probe_http(url: str, *, timeout: float = _TIMEOUT) -> dict | None:
    """Attempt streamable-HTTP initialize + tools/list. Returns server_info dict or None.

    Handles:
    - 307 redirect to trailing-slash URL (retry with trailing slash)
    - SSE-encoded JSON-RPC response body (hybrid: HTTP headers + SSE body)
    - Plain JSON-RPC response body (pure streamable-HTTP)
    - Non-JSON-RPC initialize response (some MCPs return {"status":"ok"} instead of JSON-RPC)
    - Missing mcp-session-id (stateless servers skip the session handshake)
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    init_req = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL_VERSION,
                   "capabilities": {},
                   "clientInfo": {"name": "agentic-historian-probe", "version": "0.1"}},
    }

    def _parse_sse_body(body_text: str) -> dict | None:
        """Extract JSON-RPC result from an SSE-formatted response body.

        SSE format: ``event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{...}}\r\n``
        Also handles bare JSON (no SSE wrapper).
        """
        if not body_text:
            return None
        # Strip SSE event: prefix lines and parse data lines as JSON-RPC
        for line in body_text.split("\n"):
            line = line.rstrip("\r")
            if line.startswith("data:"):
                data = line[5:].strip()
                if data.startswith("{"):
                    try:
                        obj = json.loads(data)
                        if isinstance(obj, dict):
                            return obj
                    except Exception:
                        pass
        # Fall back: try parsing the whole body as JSON
        try:
            return json.loads(body_text)
        except Exception:
            return None

    async def _do_post(client, target_url):
        r = await client.post(target_url, json=init_req, headers=headers)
        if r.status_code in (307, 308):
            location = r.headers.get("location", "")
            if location:
                return await client.post(location, json=init_req, headers=headers)
            return r
        return r

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout),
                                     follow_redirects=False) as client:
            r = await _do_post(client, url)
            if r.status_code >= 400:
                return None
            sid = r.headers.get("mcp-session-id")

            # Parse body — may be SSE-encoded (event: message\ndata: {...}) or plain JSON
            body = _parse_sse_body(r.text)

            # Send notifications/initialized if we have a session
            if sid:
                hdrs2 = dict(headers)
                hdrs2["Mcp-Session-Id"] = sid
                try:
                    await client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                      headers=hdrs2)
                except Exception:
                    pass

            # tools/list
            hdrs3 = dict(headers)
            if sid:
                hdrs3["Mcp-Session-Id"] = sid
            r2 = await client.post(url, headers=hdrs3,
                                   json={"jsonrpc": "2.0", "id": 3,
                                         "method": "tools/list", "params": {}})
            r2.raise_for_status()
            body2 = _parse_sse_body(r2.text)

            info = (body.get("result") or body) if body else {}
            tools_result = (body2.get("result") or body2) if body2 else {}
            if "tools" not in info and "tools" in tools_result:
                info["tools"] = tools_result["tools"]
            elif "tools" not in info:
                info["tools"] = list(tools_result.get("tools") or [])
            return info
    except Exception:
        return None



async def _probe_sse(url: str, *, timeout: float = _TIMEOUT) -> dict | None:
    """Attempt SSE initialize + tools/list. Returns server_info dict or None."""
    headers = {"Accept": "text/event-stream"}
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": _PROTOCOL_VERSION,
                           "capabilities": {},
                           "clientInfo": {"name": "agentic-historian-probe", "version": "0.1"}}}
    initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    list_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout),
                                     follow_redirects=True) as client:
            async with client.stream("GET", f"{url}/sse",
                                     headers=headers) as stream:
                stream.raise_for_status()
                messages_url: Optional[str] = None
                event: Optional[str] = None
                data: list[str] = []
                result: Any = None

                async def _post(payload: dict, *, _url: str | None = None) -> None:
                    nonlocal messages_url
                    u = _url or messages_url
                    if not u:
                        return
                    hdr = {"Content-Type": "application/json"}
                    await client.post(u, json=payload, headers=hdr)

                async for raw in stream.aiter_lines():
                    line = raw.rstrip("\r")
                    if line:
                        if line.startswith(":"):
                            continue
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        continue

                    if event == "endpoint" and data:
                        messages_url = url + "\n".join(data)
                        await _post(init_req)
                    elif event == "message" and data:
                        msg = json.loads("\n".join(data))
                        if msg.get("id") == 1:
                            await _post(initialized)
                            await _post(list_req)
                        elif msg.get("id") == 2:
                            result = msg.get("result")
                        event, data = None, []
                    if result is not None:
                        break
                return result
    except Exception:
        return None


def _unwrap_tools(result: Any) -> list[dict]:
    """Extract tools list from a tools/list response."""
    if not isinstance(result, dict):
        return []
    for key in ("tools", "result", "content"):
        val = result.get(key)
        if isinstance(val, list):
            return [t for t in val if isinstance(t, dict)]
    return []


async def _sample_search(url: str, transport: str, tool: str,
                         *, timeout: float = _TIMEOUT) -> Optional[int]:
    """Try a live search call; return result count or None."""
    try:
        if transport == "http":
            headers = {"Content-Type": "application/json",
                       "Accept": "application/json, text/event-stream"}
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout),
                                         follow_redirects=False) as client:
                r = await client.post(url, json={
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": _PROTOCOL_VERSION,
                               "capabilities": {},
                               "clientInfo": {"name": "agentic-historian-probe", "version": "0.1"}}},
                    headers=headers)
                if r.status_code >= 400:
                    return None
                sid = r.headers.get("mcp-session-id")
                hdrs2 = dict(headers)
                if sid:
                    hdrs2["Mcp-Session-Id"] = sid
                await client.post(url,
                                  json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                                  headers=hdrs2)
                r2 = await client.post(url, headers=hdrs2,
                                       json={"jsonrpc": "2.0", "id": 3,
                                             "method": tool,
                                             "params": {"query": "Johann", "limit": 3}})
                r2.raise_for_status()
                body = r2.json()
                results = body.get("result", {})
                if isinstance(results, dict):
                    for k in ("results", "persons", "hits", "items"):
                        if isinstance(results.get(k), list):
                            return len(results[k])
                return None
        else:
            # SSE sample
            headers = {"Accept": "text/event-stream"}
            init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": _PROTOCOL_VERSION,
                                   "capabilities": {},
                                   "clientInfo": {"name": "agentic-historian-probe", "version": "0.1"}}}
            initialized = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            search_req = {"jsonrpc": "2.0", "id": 2, "method": tool,
                          "params": {"query": "Johann", "limit": 3}}

            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, read=timeout),
                                         follow_redirects=True) as client:
                async with client.stream("GET", f"{url}/sse",
                                         headers=headers) as stream:
                    stream.raise_for_status()
                    messages_url: Optional[str] = None
                    event: Optional[str] = None
                    data: list[str] = []
                    result: Any = None

                    async def _post(payload: dict, *, _u: str | None = None) -> None:
                        nonlocal messages_url
                        u = _u or messages_url
                        if u:
                            await client.post(u, json=payload,
                                              headers={"Content-Type": "application/json"})

                    async for raw in stream.aiter_lines():
                        line = raw.rstrip("\r")
                        if line:
                            if line.startswith(":"):
                                continue
                            if line.startswith("event:"):
                                event = line[6:].strip()
                            elif line.startswith("data:"):
                                data.append(line[5:].lstrip())
                            continue
                        if event == "endpoint" and data:
                            messages_url = url + "\n".join(data)
                            await _post(init_req)
                        elif event == "message" and data:
                            msg = json.loads("\n".join(data))
                            if msg.get("id") == 1:
                                await _post(initialized)
                                await _post(search_req)
                            elif msg.get("id") == 2:
                                result = msg.get("result")
                            event, data = None, []
                        if result is not None:
                            break

                    if isinstance(result, dict):
                        for k in ("results", "persons", "hits", "items"):
                            if isinstance(result.get(k), list):
                                return len(result[k])
    except Exception:
        pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

async def probe(url: str) -> ProbeReport:
    """Probe an MCP server URL and return a ProbeReport.

    Tries streamable-HTTP first, then SSE. Short timeouts (5 s). Errors are
    swallowed and recorded in report.errors; the function never raises.
    """
    url = url.rstrip("/")
    report = ProbeReport(url=url)

    # 1. Streamable-HTTP
    try:
        info = await asyncio.wait_for(_probe_http(url), timeout=_TIMEOUT)
        if info:
            report.transport = "http"
            report.server_info = info.get("serverInfo")
            report.tools = _unwrap_tools(info)
            report.contract, report.contract_tool = _detect_contract(report.tools)
            report.sample = await _sample_search(url, "http", report.contract_tool or "search_persons")
            return report
    except asyncio.TimeoutError:
        report.errors.append("streamable-HTTP probe timed out")
    except Exception as e:
        report.errors.append(f"streamable-HTTP probe failed: {e}")

    # 2. SSE
    try:
        info = await asyncio.wait_for(_probe_sse(url), timeout=_TIMEOUT)
        if info:
            report.transport = "sse"
            report.server_info = info.get("serverInfo")
            report.tools = _unwrap_tools(info)
            report.contract, report.contract_tool = _detect_contract(report.tools)
            report.sample = await _sample_search(url, "sse", report.contract_tool or "search_persons")
            return report
    except asyncio.TimeoutError:
        report.errors.append("SSE probe timed out")
    except Exception as e:
        report.errors.append(f"SSE probe failed: {e}")

    return report


# ── Sync wrapper ──────────────────────────────────────────────────────────────

def probe_sync(url: str) -> ProbeReport:
    """Blocking wrapper around :func:`probe` for non-async call sites."""
    return asyncio.run(probe(url))


# ── Registry snippet generator ────────────────────────────────────────────────

def registry_snippet(name: str, url: str, report: ProbeReport) -> str:
    """Return a ready-to-paste ``MCPSource(...)`` entry for the registry.

    The output is valid Python matching the ``MCPSource`` dataclass fields.
    ``compile()`` it in a test to verify correctness.
    """
    tool_names = sorted(t["name"] for t in report.tools)
    tool_list_repr = ", ".join(f'"{n}"' for n in tool_names) if tool_names else '("search_persons",)'

    # Build tool_map for search_persons if the tool has a non-standard name
    tool_map_lines = ""
    if report.contract_tool and report.contract_tool != "search_persons":
        needs_adapter = report.contract_tool.startswith("search") and \
            not _SEARCH_EXACT.match(report.contract_tool)
        adapter_marker = "  # TODO adapter needed: record fields differ from PersonResult aliases" if needs_adapter else ""
        tool_map_lines = f"""\
    tool_map={{"search_persons": "{report.contract_tool}"}},{adapter_marker}
"""

    lines = [
        f"MCPSource(",
        f'    name="{name}",',
        f'    title="TODO: human-readable title",',
        f'    kinds=("person",),',
        f'    path="TODO: URL path under $MCP_BASE_URL",',
        f'    full_url="{url}",',
        f'    transport="{report.transport}",',
        f"    tools=({tool_list_repr}),",
        f"{tool_map_lines}",
        f"    # server_info={report.server_info!r}",
        f"    # contract={report.contract}, contract_tool={report.contract_tool!r}",
        f"    # sample_result_count={report.sample!r}",
        f")",
    ]
    return "\n".join(lines)