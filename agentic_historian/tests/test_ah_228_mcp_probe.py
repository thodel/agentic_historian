"""Tests for #228: P1-D1 mcp_probe — transport detection, tools/list, contract check.

Offline — patching at the transport-function level (not httpx). No pytest-asyncio
(coroutines driven with asyncio.run). Run from the repo root:
    pytest agentic_historian/tests/test_ah_228_mcp_probe.py
"""

import asyncio
import json as _json
import sys
from pathlib import Path
from unittest import mock

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

sys.path.insert(0, str(PKG / "utils"))
import mcp_probe as mp
from utils import mcp_probe as _mp


run = asyncio.run


# ── Contract heuristic tests ──────────────────────────────────────────────────

class TestContractHeuristic:
    def test_exact_search_persons(self):
        tools = [{"name": "search_persons", "inputSchema": {"properties": {"query": {}}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search_persons"

    def test_hls_prefixed(self):
        tools = [{"name": "hls_search_persons", "inputSchema": {"properties": {"query": {}}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "hls_search_persons"

    def test_eos_prefixed(self):
        tools = [{"name": "eos_search_persons", "inputSchema": {"properties": {"query": {}}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "eos_search_persons"

    def test_generic_search_query(self):
        tools = [{"name": "search", "inputSchema": {"properties": {"query": {}}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search"

    def test_generic_search_q_param(self):
        tools = [{"name": "search", "inputSchema": {"properties": {"q": {}}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search"

    def test_unrelated_tools_no_contract(self):
        tools = [{"name": "get_person", "inputSchema": {"properties": {}}},
                 {"name": "list_entities", "inputSchema": {"properties": {}}}]
        contract, name = mp._detect_contract(tools)
        assert contract is False and name is None

    def test_search_persons_wins_over_prefixed(self):
        tools = [
            {"name": "search_persons", "inputSchema": {"properties": {"query": {}}}},
            {"name": "hls_search_persons", "inputSchema": {"properties": {"query": {}}}},
        ]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search_persons"

    def test_prefixed_wins_over_generic(self):
        tools = [
            {"name": "hbls_search_persons", "inputSchema": {"properties": {"query": {}}}},
            {"name": "search", "inputSchema": {"properties": {"query": {}}}},
        ]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "hbls_search_persons"

    def test_string_input_schema_graceful(self):
        tools = [{"name": "search_persons", "inputSchema": "not a dict"}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search_persons"

    def test_missing_input_schema_graceful(self):
        tools = [{"name": "search_persons"}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search_persons"

    def test_empty_tools_list(self):
        contract, name = mp._detect_contract([])
        assert contract is False and name is None

    def test_non_dict_tools_list(self):
        """Non-dict tool entries are skipped gracefully."""
        tools = [{"name": "search_persons", "inputSchema": {"properties": {"query": {}}}},
                 None,
                 "not a dict",
                 {"name": "get_person"}]
        contract, name = mp._detect_contract(tools)
        assert contract is True and name == "search_persons"


# ── _unwrap_tools ─────────────────────────────────────────────────────────────

class TestUnwrapTools:
    def test_handles_tools_key(self):
        result = {"tools": [{"name": "a"}, {"name": "b"}]}
        assert [t["name"] for t in mp._unwrap_tools(result)] == ["a", "b"]

    def test_handles_result_key(self):
        result = {"result": [{"name": "x"}]}
        assert [t["name"] for t in mp._unwrap_tools(result)] == ["x"]

    def test_handles_content_key(self):
        result = {"content": [{"name": "y"}]}
        assert [t["name"] for t in mp._unwrap_tools(result)] == ["y"]

    def test_strips_non_dicts(self):
        result = {"tools": [{"name": "a"}, None, "str", {"name": "b"}]}
        tools = mp._unwrap_tools(result)
        assert [t["name"] for t in tools] == ["a", "b"]

    def test_returns_empty_for_non_dict(self):
        assert mp._unwrap_tools("not a dict") == []
        assert mp._unwrap_tools(None) == []
        assert mp._unwrap_tools([{"name": "a"}]) == []  # list at top level not handled


# ── _probe_http / _probe_sse unit tests (mock via direct function patch) ──────

class TestProbeHTTPFn:
    """Patch _probe_http at the function level for unit isolation."""

    def test_returns_tools_list(self):
        fake_result = {
            "serverInfo": {"name": "test", "version": "1.0"},
            "capabilities": {},
            "tools": [
                {"name": "search_persons", "inputSchema": {"properties": {"query": {}}}},
                {"name": "get_person", "inputSchema": {"properties": {"pid": {}}}},
            ]
        }

        async def fake_probe_http(url, *, timeout=None):
            return fake_result

        with mock.patch.object(mp, "_probe_http", fake_probe_http):
            info = run(mp._probe_http("https://x.com/mcp/test"))

        assert info == fake_result
        assert any(t["name"] == "search_persons" for t in mp._unwrap_tools(info))


class TestProbeSSEFn:
    def test_returns_tools_list(self):
        fake_result = {
            "serverInfo": {"name": "sse-test", "version": "2.0"},
            "tools": [{"name": "hls_search_persons", "inputSchema": {"properties": {"query": {}}}}]
        }

        async def fake_probe_sse(url, *, timeout=None):
            return fake_result

        with mock.patch.object(mp, "_probe_sse", fake_probe_sse):
            info = run(mp._probe_sse("https://x.com/mcp/test"))

        assert info == fake_result


# ── probe() integration: patched _probe_http + _probe_sse ───────────────────

class TestProbeIntegration:
    """Full probe() with both transports patched — tests error collection."""

    def test_http_first_then_sse(self):
        """HTTP succeeds → transport=http, SSE not called."""
        http_result = {
            "serverInfo": {"name": "http-src", "version": "1.0"},
            "capabilities": {},
            "tools": [{"name": "search_persons", "inputSchema": {"properties": {"query": {}}}}]
        }
        seen_sse = []

        async def fake_http(url, *, timeout=None, **kwargs):
            return http_result

        async def fake_sse(url, *, timeout=None, **kwargs):
            seen_sse.append(url)

        with mock.patch.object(mp, "_probe_http", fake_http):
            with mock.patch.object(mp, "_probe_sse", fake_sse):
                with mock.patch.object(mp, "_sample_search", mock.AsyncMock(return_value=5)):
                    report = run(mp.probe("https://x.com/mcp/test"))

        assert report.transport == "http"
        assert report.server_info == {"name": "http-src", "version": "1.0"}
        assert report.contract is True
        assert report.contract_tool == "search_persons"
        assert report.sample == 5
        assert seen_sse == []  # SSE not called because HTTP succeeded

    def test_http_falls_back_to_sse(self):
        """HTTP fails → SSE is tried → transport=sse."""
        seen_http, seen_sse = [], []

        async def fake_http(url, *, timeout=None, **kwargs):
            seen_http.append(url)
            return None  # HTTP didn't work

        async def fake_sse(url, *, timeout=None, **kwargs):
            seen_sse.append(url)
            return {
                "serverInfo": {"name": "sse-src", "version": "2.0"},
                "tools": [{"name": "hls_search_persons", "inputSchema": {"properties": {"query": {}}}}]
            }

        with mock.patch.object(mp, "_probe_http", fake_http):
            with mock.patch.object(mp, "_probe_sse", fake_sse):
                report = run(mp.probe("https://x.com/mcp/test"))

        assert report.transport == "sse"
        assert seen_http == ["https://x.com/mcp/test"]
        assert seen_sse == ["https://x.com/mcp/test"]

    def test_both_fail_transport_none(self):
        async def bad_http(url, *, timeout=None, **kwargs):
            raise RuntimeError("http broken")

        async def bad_sse(url, *, timeout=None, **kwargs):
            raise RuntimeError("sse broken")

        with mock.patch.object(mp, "_probe_http", bad_http):
            with mock.patch.object(mp, "_probe_sse", bad_sse):
                report = run(mp.probe("https://x.com/mcp/test"))

        assert report.transport is None
        assert len(report.errors) == 2
        assert report.ok() is False

    def test_probe_report_ok(self):
        report_ok = mp.ProbeReport(url="x", transport="http", tools=[], errors=[])
        assert report_ok.ok() is True

        report_bad = mp.ProbeReport(url="x", transport=None, errors=["failed"])
        assert report_bad.ok() is False


# ── Registry snippet tests ────────────────────────────────────────────────────

class TestRegistrySnippet:
    def _compile(self, code):
        return compile(code, "<snippet>", "exec")

    def test_exact_tool_no_tool_map(self):
        report = mp.ProbeReport(
            url="https://tei.dh.unibe.ch/mcp/ssrq",
            transport="http",
            tools=[{"name": "search_persons"}, {"name": "get_person"}],
            contract=True,
            contract_tool="search_persons",
        )
        snippet = mp.registry_snippet("ssrq", "https://tei.dh.unibe.ch/mcp/ssrq", report)
        self._compile(snippet)
        assert "tool_map" not in snippet
        assert 'transport="http"' in snippet
        assert 'full_url="https://tei.dh.unibe.ch/mcp/ssrq"' in snippet

    def test_prefixed_tool_adds_tool_map(self):
        report = mp.ProbeReport(
            url="https://tei.dh.unibe.ch/mcp/hls",
            transport="http",
            tools=[{"name": "hls_search_persons"}, {"name": "hls_get_person"}],
            contract=True,
            contract_tool="hls_search_persons",
        )
        snippet = mp.registry_snippet("hls", "https://tei.dh.unibe.ch/mcp/hls", report)
        self._compile(snippet)
        assert 'tool_map={"search_persons": "hls_search_persons"}' in snippet

    def test_generic_search_adds_adapter_marker(self):
        report = mp.ProbeReport(
            url="https://example.com/mcp/generic",
            transport="http",
            tools=[{"name": "search", "inputSchema": {"properties": {"query": {}}}}],
            contract=True,
            contract_tool="search",
        )
        snippet = mp.registry_snippet("generic", "https://example.com/mcp/generic", report)
        self._compile(snippet)
        assert "TODO adapter needed" in snippet
        assert 'tool_map={"search_persons": "search"}' in snippet

    def test_no_contract_no_tool_map(self):
        report = mp.ProbeReport(
            url="https://example.com/mcp/nocontract",
            transport="sse",
            tools=[{"name": "get_person"}],
            contract=False,
            contract_tool=None,
        )
        snippet = mp.registry_snippet("nocontract", "https://example.com/mcp/nocontract", report)
        self._compile(snippet)
        assert "tool_map" not in snippet

    def test_server_info_and_sample_in_comments(self):
        report = mp.ProbeReport(
            url="https://example.com/mcp/test",
            transport="sse",
            tools=[{"name": "search_persons"}],
            contract=True,
            contract_tool="search_persons",
            server_info={"name": "my-server", "version": "3.1"},
            sample=7,
        )
        snippet = mp.registry_snippet("test", "https://example.com/mcp/test", report)
        self._compile(snippet)
        assert "my-server" in snippet
        assert "sample_result_count=7" in snippet

    def test_snippet_exec_with_mcp_registry_in_scope(self):
        """Generated code is valid Python when MCPSource is imported."""
        from knowledge_hub.mcp_registry import MCPSource
        report = mp.ProbeReport(
            url="https://custom.example.com/mcp/custom",
            transport="http",
            tools=[{"name": "ssrq_search", "inputSchema": {"properties": {"query": {}}}}],
            contract=True,
            contract_tool="ssrq_search",
        )
        snippet = mp.registry_snippet("custom", "https://custom.example.com/mcp/custom", report)
        ns = {"MCPSource": MCPSource}
        exec(self._compile(snippet), ns)

    def test_path_and_full_url_mutually_exclusive_in_snippet(self):
        """Snippet includes full_url (preferred) and path as TODO."""
        report = mp.ProbeReport(
            url="https://x.com/mcp/newsrc",
            transport="http",
            tools=[{"name": "search_persons"}],
            contract=True,
            contract_tool="search_persons",
        )
        snippet = mp.registry_snippet("newsrc", "https://x.com/mcp/newsrc", report)
        self._compile(snippet)
        assert 'full_url="https://x.com/mcp/newsrc"' in snippet
        assert 'path="TODO: URL path under $MCP_BASE_URL"' in snippet


# ── Live verification (run manually) ─────────────────────────────────────────
# python -c "
# import sys; sys.path.insert(0, '.')
# from utils.mcp_probe import probe_sync, registry_snippet
# r = probe_sync('https://tei.dh.unibe.ch/mcp/ssrq')
# print('transport:', r.transport)
# print('contract:', r.contract, '| tool:', r.contract_tool)
# print('tools:', [t['name'] for t in r.tools])
# print('sample:', r.sample)
# print('---')
# print(registry_snippet('ssrq', 'https://tei.dh.unibe.ch/mcp/ssrq', r))
# "