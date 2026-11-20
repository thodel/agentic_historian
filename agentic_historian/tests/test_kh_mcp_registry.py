"""Offline tests for the MCP-federated Knowledge Hub registry.

No network: these assert the registry is well-formed and that adding a source
is a pure data change picked up by every accessor. Run from the repo root:
    pytest agentic_historian/tests/test_kh_mcp_registry.py
"""

import sys
from pathlib import Path

PKG = str(Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

from knowledge_hub import mcp_registry as reg


def test_registry_is_valid():
    """The registry passes its own invariants."""
    reg.validate()  # raises on any malformed entry


def test_expected_live_sources_present():
    """The four tei MCPs plus the external wikidata MCP are registered."""
    names = {s.name for s in reg.list_sources()}
    assert {"eos", "hbls", "hls", "kf", "wikidata"} <= names


def test_names_unique():
    names = [s.name for s in reg.SOURCES]
    assert len(names) == len(set(names))


def test_tei_sources_resolve_under_base_url():
    """Non-external sources derive their URL from config.MCP_BASE_URL."""
    import config
    for s in reg.list_sources(include_external=False):
        assert s.url == f"{config.MCP_BASE_URL}/{s.path}"
        assert s.url.startswith("https://")


def test_external_source_has_no_tei_url():
    """External (gateway) sources like wikidata do not derive a tei URL."""
    wd = reg.get_source("wikidata")
    assert wd.external is True
    assert wd.url == ""


def test_sources_for_kind_filters():
    """PERSON sources include the biographical registers; fulltext EOS and KF (#812)."""
    person_sources = {s.name for s in reg.sources_for_kind("person")}
    assert {"hbls", "hls", "kf"} <= person_sources
    fulltext_sources = {s.name for s in reg.sources_for_kind("fulltext")}
    assert "eos" in fulltext_sources


def test_sources_for_kind_rejects_unknown():
    import pytest
    with pytest.raises(ValueError):
        reg.sources_for_kind("nonsense")


def test_get_source_unknown_raises():
    import pytest
    with pytest.raises(KeyError):
        reg.get_source("does-not-exist")


def test_authority_sources_flagged():
    """Sources that yield durable ids (HLS/GND/Wikidata) are marked authority."""
    assert reg.get_source("hls").authority is True
    assert reg.get_source("hbls").authority is True
    assert reg.get_source("wikidata").authority is True


def test_humanlineage_is_no_longer_a_source():
    """Withdrawn from Zenodo on 19.11.2026 (copyright; ch-h-bot#640), its MCP
    off at nginx: no agent may offer or ask it. Removed 20.11.2026."""
    import pytest
    assert "humanlineage" not in {s.name for s in reg.SOURCES}
    assert all("humanlineage" not in s.url for s in reg.SOURCES)
    with pytest.raises(KeyError):
        reg.get_source("humanlineage")


def test_the_layer_client_says_why_it_has_no_source():
    """The Bestandsgraph layer module still exists (off by default, #899); if
    someone switches it on, it says why instead of a bare KeyError."""
    import pytest

    from knowledge_hub.sources import humanlineage as hl
    with pytest.raises(RuntimeError, match="withdrawn"):
        hl.MCPClient()


def test_repointing_base_url_moves_all_tei_sources(monkeypatch):
    """The core methodology promise: one env var repoints the whole federation.

    Because MCPSource.url is derived from config.MCP_BASE_URL at access time,
    changing the base moves every non-external source with no code edits.
    """
    import config
    monkeypatch.setattr(config, "MCP_BASE_URL", "https://staging.example/mcp")
    assert reg.get_source("hls").url == "https://staging.example/mcp/hls/mcp"
    # external source is unaffected
    assert reg.get_source("wikidata").url == ""


def test_every_tei_source_is_streamable_http_at_its_mcp_path():
    """Since #800 every source on tei answers at /mcp/<name>/mcp; /mcp/<name>
    is a landing page (302). Agent F's first run on tei found eos, kf, hbls and
    hls still at the old paths (#812)."""
    for s in reg.list_sources(include_external=False):
        assert s.transport == "http", s.name
        assert s.url.endswith(f"/mcp/{s.name}/mcp"), s.url


def test_only_hls_needs_basic_auth():
    needs = {s.name: s.basic_auth_config for s in reg.SOURCES if s.basic_auth_config}
    assert needs == {"hls": "HLS_BASIC"}
