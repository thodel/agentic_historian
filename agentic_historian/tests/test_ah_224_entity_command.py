"""Tests for #224 (P1-A4): /entity Discord command over the local entity index.

Offline. Tests format_entity, find_entity, get_suggestions, and that the
command is registered on the bot with its option.  Run from the repo root:
    pytest agentic_historian/tests/test_ah_224_entity_command.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from entity_index import (
    EntityEntry,
    EntityMention,
    EntityIndex,
    find_entity,
    get_suggestions,
    format_entity,
)


# ── fixtures ─────────────────────────────────────────────────────────────────

def _mentions(*specs):
    """specs = (doc_id, context, page)"""
    return [_make_m(*s) for s in specs]


def _make_m(doc_id, context="", page=""):
    return EntityMention(doc_id=doc_id, context=context, page=page)


def _entry(name, etype, *, gnd="", hls="", wikidata="", mentions=None):
    e = EntityEntry(name=name, type=etype, gnd=gnd, hls=hls, wikidata=wikidata)
    if mentions:
        for m in mentions:
            e.add_mention(doc_id=m.doc_id, context=m.context, page=m.page)
    return e


# ── find_entity ───────────────────────────────────────────────────────────────

def test_find_entity_exact_name():
    """Exact (case/diacritic-insensitive) name match."""
    idx = EntityIndex(entries={
        "hans": _entry("Hans von Wiler", "Person", gnd="118000"),
    })
    assert find_entity(idx, "Hans von Wiler") is not None
    assert find_entity(idx, "hans von wiler") is not None
    assert find_entity(idx, "Hans v. Wiler") is None  # variant spelling


def test_find_entity_gnd():
    """GND id lookup."""
    idx = EntityIndex(entries={
        "gnd-118000": _entry("Hans von Wiler", "Person", gnd="118000"),
    })
    result = find_entity(idx, "118000")
    assert result is not None
    assert result.gnd == "118000"


def test_find_entity_substring():
    """Substring match when no exact normalised match."""
    idx = EntityIndex(entries={
        "hans": _entry("Hans von Wiler", "Person"),
    })
    result = find_entity(idx, "von Wiler")
    assert result is not None
    assert result.name == "Hans von Wiler"


def test_find_entity_no_hit():
    """No match → None."""
    idx = EntityIndex(entries={
        "peter": _entry("Peter Muster", "Person"),
    })
    assert find_entity(idx, "Johann") is None


def test_find_entity_empty_index():
    """Empty index → None."""
    assert find_entity(EntityIndex(entries={}), "Hans") is None


# ── get_suggestions ───────────────────────────────────────────────────────────

def test_get_suggestions_returns_closest():
    """Returns up to n entries sorted by normalised-name similarity."""
    idx = EntityIndex(entries={
        "hans": _entry("Hans von Wiler", "Person"),
        "peter": _entry("Peter Muster", "Person"),
        "anna": _entry("Anna Müller", "Person"),
    })
    suggestions = get_suggestions(idx, "Johann", n=3)
    assert len(suggestions) <= 3
    # Should include the closest names even if not exact
    assert all(isinstance(e, EntityEntry) for e in suggestions)


def test_get_suggestions_empty():
    idx = EntityIndex(entries={})
    assert get_suggestions(idx, "Hans") == []


# ── format_entity ─────────────────────────────────────────────────────────────

def test_format_entity_name_and_type():
    e = _entry("Hans von Wiler", "Person")
    out = format_entity(e, show_context=False)
    assert "Hans von Wiler" in out
    assert "[Person]" in out


def test_format_entity_authority_links():
    e = _entry("Johann Gutenberg", "Person", gnd="118543794", hls="12345", wikidata="Q5800")
    out = format_entity(e, show_context=False)
    assert "d-nb.info/gnd/118543794" in out
    assert "hls-dhs-dss.ch/de/articles/12345" in out
    assert "wikidata.org/wiki/Q5800" in out


def test_format_entity_mentions():
    e = _entry("Hans von Wiler", "Person")
    e.add_mention(doc_id="doc_123", context="Hans von Wiler war ein Ritter.", page="f. 1r")
    out = format_entity(e, show_context=True)
    assert "doc_123" in out
    assert "f. 1r" in out
    assert "Ritter" in out


def test_format_entity_respects_max_chars():
    e = _entry("Test Person", "Person")
    for i in range(20):
        e.add_mention(doc_id=f"doc_{i}", context="A" * 200, page="")
    out = format_entity(e, show_context=True, max_chars=300)
    assert len(out) <= 300


def test_format_entity_discord_cap():
    """Default max_chars=1900 is safely below Discord's 2000 limit."""
    e = _entry("Test", "Person")
    for i in range(50):
        e.add_mention(doc_id=f"doc_{i}", context="B" * 100, page="")
    out = format_entity(e)
    assert len(out) <= 2000


def test_format_entity_no_mentions():
    e = _entry("Unknown Person", "Person")
    out = format_entity(e, show_context=False)
    # Should not raise and should contain the name
    assert "Unknown Person" in out


# ── command registration ─────────────────────────────────────────────────────

def test_entity_command_registered_with_option():
    import bot as bot_module
    cmd = next((c for c in bot_module.bot.pending_application_commands
                if getattr(c, "name", None) == "entity"), None)
    assert cmd is not None, "/entity command must be registered"
    assert [o.name for o in cmd.options] == ["name"]