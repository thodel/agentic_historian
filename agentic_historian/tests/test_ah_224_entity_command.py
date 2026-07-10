"""Tests for #224 (P1-A4): /entity Discord command over the local entity index.

Offline. Covers the pure core (lookup/suggest, diacritic-insensitive, GND-merged),
the pure formatter (links, doc list, 2000-char cap), and that the command is
registered on the bot with its option (pattern of test_ah_91_search_command).

    pytest agentic_historian/tests/test_ah_224_entity_command.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import entity_index


# ── fixtures ──────────────────────────────────────────────────────────────────

def _write_entities(dir_: Path, doc_id: str, entities: list[dict]) -> None:
    (dir_ / f"{doc_id}_entities.json").write_text(
        json.dumps({"entities": entities}, ensure_ascii=False), encoding="utf-8")


def _index_two_docs(tmp_path: Path):
    # Same person via GND across two docs (canonical spelling kept stable as the
    # umlaut form), plus another person for suggestion tests.
    _write_entities(tmp_path, "doc-1", [
        {"text": "Johann Müller", "normalised": "Johann Müller", "type": "person",
         "gnd_id": "118000", "hls_id": "12345", "wikidata_id": "Q42",
         "context": "…Johann Müller von Bern…"},
    ])
    _write_entities(tmp_path, "doc-2", [
        {"text": "Johann Müller", "normalised": "Johann Müller", "type": "person",
         "gnd_id": "118000", "context": "…dem Müller…"},
        {"text": "Anna Keller", "normalised": "Anna Keller", "type": "person",
         "context": "…Anna Keller…"},
    ])
    return entity_index.build_index(tmp_path)


# ── core: lookup / suggest ────────────────────────────────────────────────────

def test_lookup_exact(tmp_path):
    idx = _index_two_docs(tmp_path)
    e = entity_index.lookup(idx, "Johann Müller")
    assert e is not None and e.type == "person" and e.gnd == "118000"


def test_lookup_diacritic_insensitive(tmp_path):
    idx = _index_two_docs(tmp_path)
    # accent-stripped, case-folded, and umlaut-transliterated queries all hit
    assert entity_index.lookup(idx, "johann muller") is not None      # ü→u
    assert entity_index.lookup(idx, "JOHANN MÜLLER") is not None      # case
    assert entity_index.lookup(idx, "Johann Mueller") is not None     # ü↔ue


def test_gnd_merged_returns_all_documents(tmp_path):
    idx = _index_two_docs(tmp_path)
    e = entity_index.lookup(idx, "Johann Müller")
    docs = {m.doc_id for m in e.mentions}
    assert docs == {"doc-1", "doc-2"}          # merged across both spellings


def test_no_hit_returns_suggestions(tmp_path):
    idx = _index_two_docs(tmp_path)
    assert entity_index.lookup(idx, "Johan Mülir") is None
    sugg = entity_index.suggest(idx, "Johan Mülir")
    assert "Johann Müller" in sugg


def test_no_hit_no_close_names_returns_empty(tmp_path):
    idx = _index_two_docs(tmp_path)
    assert entity_index.suggest(idx, "Xzykwq Qwertz") == []


# ── formatter ─────────────────────────────────────────────────────────────────

def test_format_entity_has_links_and_docs(tmp_path):
    idx = _index_two_docs(tmp_path)
    out = entity_index.format_entity(entity_index.lookup(idx, "Johann Müller"))
    assert "Johann Müller" in out and "_person_" in out
    assert "d-nb.info/gnd/118000" in out and "wikidata.org/wiki/Q42" in out
    assert "/route doc-1" in out and "/route doc-2" in out


def test_format_entity_includes_pages_link_when_site_base_given(tmp_path):
    idx = _index_two_docs(tmp_path)
    out = entity_index.format_entity(entity_index.lookup(idx, "Johann Müller"),
                                     site_base="https://thodel.github.io/agentic-historian-outputs")
    assert "entities/gnd-118000/" in out and "Katalogseite" in out


def test_format_entity_respects_2000_char_cap():
    entry = entity_index.EntityEntry(name="Vielzitiert", type="person")
    for i in range(500):
        entry.add_mention(doc_id=f"doc-{i:04d}", context="x")
    out = entity_index.format_entity(entry, max_chars=2000)
    assert len(out) <= 2000
    assert "weitere" in out                      # trimmed with a remainder note


# ── command registration ──────────────────────────────────────────────────────

def test_entity_command_registered_with_option():
    import bot as bot_module
    cmd = next((c for c in bot_module.bot.pending_application_commands
                if getattr(c, "name", None) == "entity"), None)
    assert cmd is not None, "/entity command must be registered"
    assert [o.name for o in cmd.options] == ["name"]
