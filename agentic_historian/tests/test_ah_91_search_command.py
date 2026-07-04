"""Tests for #91 (KH-5): /search Discord command.

Offline. Tests the pure formatter and that the command is registered on the bot
with its option. Run from the repo root:
    pytest agentic_historian/tests/test_ah_91_search_command.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from agents import search_agent
from agents.search_agent import SearchResponse
from utils.entity_resolver import ResolvedEntity


def _entity(name, **kw):
    kw.setdefault("sources", ["hls"])
    return ResolvedEntity(name=name, **kw)


# ── formatter ────────────────────────────────────────────────────────────────

def test_format_no_hits():
    out = search_agent.format_response(SearchResponse(query="Nobody"))
    assert "keine Treffer" in out and "«Nobody»" in out


def test_format_lists_entities_with_links():
    resp = SearchResponse(
        query="Wiler", raw_count=5,
        entities=[
            _entity("Hans von Wiler", sources=["hbls", "hls"], confidence="high",
                    life_dates="1300–1370", gnd_id="118000", hls_id=12345),
            _entity("Peter Muster", sources=["kf"], confidence="high"),
        ],
    )
    out = search_agent.format_response(resp)
    assert "Hans von Wiler" in out and "(1300–1370)" in out
    assert "hbls, hls" in out
    assert "d-nb.info/gnd/118000" in out and "hls-dhs-dss.ch/de/12345" in out
    assert "2 Treffer" in out


def test_format_flags_review_and_failed_sources():
    resp = SearchResponse(
        query="q", raw_count=2,
        entities=[_entity("Anna Muster", sources=["hls", "kf"], confidence="medium",
                          needs_review=True)],
        failed_sources=["eos"],
    )
    out = search_agent.format_response(resp)
    assert "⚠️" in out and "eos" in out and "_medium_" in out


def test_format_respects_max_chars():
    ents = [_entity(f"Person Nummer {i} von Irgendwo", sources=["hls"]) for i in range(50)]
    resp = SearchResponse(query="x", entities=ents, raw_count=50)
    out = search_agent.format_response(resp, top=10, max_chars=300)
    assert len(out) <= 300


def test_format_shows_remaining_count():
    ents = [_entity(f"Person {i}", sources=["hls"]) for i in range(50)]
    resp = SearchResponse(query="x", entities=ents, raw_count=50)
    out = search_agent.format_response(resp, top=10)  # default max_chars fits
    assert "und 40 weitere" in out


# ── command registration ─────────────────────────────────────────────────────

def test_search_command_registered_with_option():
    import bot as bot_module
    cmd = next((c for c in bot_module.bot.pending_application_commands
                if getattr(c, "name", None) == "search"), None)
    assert cmd is not None, "/search command must be registered"
    assert [o.name for o in cmd.options] == ["query"]
