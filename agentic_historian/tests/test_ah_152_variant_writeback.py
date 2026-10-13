"""Tests for #152 (HITL-3b): hub variant write-back on confirmed entity links.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_152_variant_writeback.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import entity_review as er
from knowledge_hub import hub
from utils.entity_resolver import ResolvedEntity


def _cand(name="Hans von Wiler", gnd="118000", hls=12345, sources=("hls", "hbls")):
    return ResolvedEntity(name=name, sources=list(sources), gnd_id=gnd, hls_id=hls)


# ── write_variant builds the right hub record ────────────────────────────────

def test_write_variant_appends_to_existing_record(monkeypatch):
    written = {}
    existing = {"id": "118000", "name": "Hans von Wiler", "variants": ["H. Wiler"]}
    monkeypatch.setattr(hub, "find_person", lambda n: existing)
    monkeypatch.setattr(hub, "add_person", lambda rec: written.update(rec))

    ent = {"text": "Hanns Wyler", "type": "PERSON"}
    er.write_variant(ent, _cand())

    assert written["id"] == "118000" and written["name"] == "Hans von Wiler"
    assert "Hanns Wyler" in written["variants"] and "H. Wiler" in written["variants"]
    assert written["gnd"] == "118000" and written["hls"] == 12345


def test_write_variant_creates_new_record(monkeypatch):
    written = {}
    monkeypatch.setattr(hub, "find_person", lambda n: None)
    monkeypatch.setattr(hub, "add_person", lambda rec: written.update(rec))

    ent = {"text": "Hanns Wyler", "type": "PERSON"}
    er.write_variant(ent, _cand(gnd="G9", hls=None))
    assert written["id"] == "G9" and written["name"] == "Hans von Wiler"
    assert written["variants"] == ["Hanns Wyler"]


def test_write_variant_place_uses_place_api(monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "find_place", lambda n: None)
    monkeypatch.setattr(hub, "add_place", lambda rec: calls.append(rec))
    monkeypatch.setattr(hub, "add_person", lambda rec: calls.append(("PERSON!", rec)))

    er.write_variant({"text": "Tun", "type": "PLACE"},
                     ResolvedEntity(name="Thun", sources=["kf"]))
    assert len(calls) == 1 and calls[0]["name"] == "Thun" and "Tun" in calls[0]["variants"]


def test_write_variant_noop_for_kein_link(monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "add_person", lambda rec: calls.append(rec))
    er.write_variant({"text": "X", "type": "PERSON"}, None)
    assert calls == []


def test_apply_entity_link_triggers_writeback(monkeypatch):
    calls = []
    monkeypatch.setattr(hub, "find_person", lambda n: None)
    monkeypatch.setattr(hub, "add_person", lambda rec: calls.append(rec))
    ent = {"text": "Hanns Wyler", "type": "PERSON", "link_method": "none"}
    er.apply_entity_link(ent, _cand())
    assert ent["link_method"] == "human_confirmed"
    assert calls and "Hanns Wyler" in calls[0]["variants"]


# ── functional: the compounding loop actually closes ─────────────────────────

def test_next_document_links_hub_exact(monkeypatch, tmp_path):
    """After write-back, a fresh hub lookup for the observed spelling matches —
    i.e. the next document would link hub_exact with zero interaction."""
    # Isolate the hub singleton onto a temp store, **by path**.
    #
    # This used to stub `KnowledgeHub._save` to a no-op, which stopped working
    # when R3 (#393) moved the mutators off `_save`: a mutation now reads the
    # file under a lock, changes it and writes it back, so a stubbed `_save` no
    # longer isolates anything — this test wrote into the live `hub.json` and
    # then failed on its own leftovers the next time it ran.
    #
    # Pointing `HUB_PATH` at `tmp_path` isolates whatever the mutators do, now
    # and after the next change to them.
    # The singleton is `_hub`. The old line patched `_HUB` with
    # `raising=False`, which silently created a new attribute and isolated
    # nothing — so this test was always writing to the live store, and only
    # `_save` being stubbed kept that invisible.
    import knowledge_hub.hub as hubmod
    store = tmp_path / "hub.json"
    monkeypatch.setattr(hubmod, "HUB_PATH", store)
    monkeypatch.setattr(hubmod, "_hub", hubmod.KnowledgeHub(path=store))

    h = hub.get_hub()
    before = hub.find_person("Hanns Wyler")
    assert before is None                              # unknown spelling initially

    er.write_variant({"text": "Hanns Wyler", "type": "PERSON"},
                     _cand(name="Hans von Wiler"))

    after = hub.find_person("Hanns Wyler")             # now matches via the variant
    assert after is not None and "Hanns Wyler" in after.get("variants", [])
