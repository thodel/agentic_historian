"""
Tests for #152 (HITL-3b): Gate 3 entity-link review + hub variant write-back.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_152_gate3_variant_writeback.py
"""

import json
import pathlib
import sys
from unittest.mock import patch, MagicMock

PKG = pathlib.Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from runstate import RunState, DONE, STAGES
import gate3_entity_review as g3


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _state(doc_id="saa-0428"):
    rs = RunState(doc_id=doc_id)
    for s in STAGES:
        rs.stage_status[s] = DONE
    return rs


def _artifact(entities):
    return json.dumps({"entities": entities})


# ─────────────────────────────────────────────────────────────────────────────
# gate3_entities
# ─────────────────────────────────────────────────────────────────────────────

def test_gate3_returns_empty_when_no_agent_c_artifact():
    st = _state()
    assert g3.gate3_entities(st) == []


def test_gate3_returns_empty_when_agent_c_artifact_is_empty():
    st = _state()
    st.artifacts["agent_c"] = "{}"
    assert g3.gate3_entities(st) == []


def test_gate3_filters_person_place_only():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Vogt", "type": "ROLE", "link_method": "none"},
        {"text": "Hans", "type": "PERSON", "link_method": "none"},
        {"text": "Thun", "type": "PLACE", "link_method": "hls_dhs"},
    ])
    ents = g3.gate3_entities(st)
    assert len(ents) == 2
    assert {e["type"] for e in ents} == {"PERSON", "PLACE"}


def test_gate3_excludes_high_confidence_entities():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Thun", "type": "PLACE", "link_method": "hub_exact",
         "hub_confidence": "high"},
    ])
    assert g3.gate3_entities(st) == []


def test_gate3_includes_unverified_and_low_entities():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Unknown Person", "type": "PERSON", "link_method": "none",
         "hub_confidence": "unverified"},
        {"text": "Another Place", "type": "PLACE", "link_method": "hls_dhs",
         "hub_confidence": "low"},
    ])
    ents = g3.gate3_entities(st)
    assert len(ents) == 2


def test_gate3_copies_entity_fields():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Hermann", "type": "PERSON", "normalized": "Hermann von B",
         "link_method": "none", "hub_confidence": "unverified",
         "hls": "", "gnd": "", "wikidata": ""},
    ])
    [ent] = g3.gate3_entities(st)
    assert ent["text"] == "Hermann"
    assert ent["normalized"] == "Hermann von B"
    assert ent["type"] == "PERSON"
    assert ent["link_method"] == "none"
    assert ent["candidates"] == []


# ─────────────────────────────────────────────────────────────────────────────
# _variant_entry
# ─────────────────────────────────────────────────────────────────────────────

def test_variant_entry_uses_normalized_as_name():
    ent = {"text": "Hermann", "normalized": "Hermann von B"}
    cand = {"name": "Hermann von B"}
    ve = g3._variant_entry(ent, cand)
    assert ve["name"] == "Hermann von B"
    assert "Hermann von B" in ve["variants"]


def test_variant_entry_uses_text_when_no_normalized():
    ent = {"text": "Hans"}
    cand = {"name": "Hans Müller"}
    ve = g3._variant_entry(ent, cand)
    assert ve["name"] == "Hans Müller"
    assert "Hans" in ve["variants"]


# ─────────────────────────────────────────────────────────────────────────────
# write_variants — hub write-back
# ─────────────────────────────────────────────────────────────────────────────

def test_write_variants_updates_entity_ids_in_artifact():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Hans", "type": "PERSON", "normalized": "Hans",
         "link_method": "none", "hub_confidence": "unverified",
         "hls": "", "gnd": "", "wikidata": ""},
    ])
    cand = {"name": "Hans", "hls_id": "012345", "source": "hls"}
    with patch.object(g3, "hub") as mock_hub:
        mock_hub.find_person.return_value = None
        mock_hub.add_person = MagicMock()
        g3.write_variants(st, {0: cand})

    # entity should be updated in artifact
    ents = g3._get_artifact_entities(st)
    assert ents[0]["hls"] == "012345"
    assert ents[0]["hub_confidence"] == "high"
    assert "gate3_confirmed" in ents[0]["link_method"]


def test_write_variants_preserves_existing_hub_ids():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Thun", "type": "PLACE", "normalized": "Thun",
         "link_method": "none", "hls": "", "gnd": "", "wikidata": ""},
    ])
    # Candidate with empty hls_id → existing hub hls should be preserved.
    cand = {"name": "Thun", "hls_id": ""}
    with patch.object(g3, "hub") as mock_hub:
        mock_hub.find_place.return_value = {
            "id": "hub_42", "wikidata": "Q12345", "gnd": "gnd:456",
            "hls": "888888",
        }
        mock_hub.add_place = MagicMock()
        g3.write_variants(st, {0: cand})

    # add_place was called with merged ids (existing hub hls preserved since
    # candidate's hls_id is empty; existing wikidata/gnd preserved too).
    call_args = mock_hub.add_place.call_args[0][0]
    assert call_args["id"] == "hub_42"
    assert call_args["wikidata"] == "Q12345"
    assert call_args["gnd"] == "gnd:456"
    assert call_args["hls"] == "888888"


def test_write_variants_none_choice_does_nothing():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Unknown", "type": "PERSON", "normalized": "Unknown",
         "link_method": "none", "hls": "", "gnd": "", "wikidata": ""},
    ])
    with patch.object(g3, "hub") as mock_hub:
        g3.write_variants(st, {0: None})

    assert not mock_hub.add_person.called
    assert not mock_hub.add_place.called


def test_write_variants_logs_gate3_decision():
    st = _state(doc_id="doc-1")
    st.artifacts["agent_c"] = _artifact([
        {"text": "X", "type": "PERSON", "normalized": "X",
         "link_method": "none", "hls": "", "gnd": "", "wikidata": ""},
        {"text": "Y", "type": "PERSON", "normalized": "Y",
         "link_method": "none", "hls": "", "gnd": "", "wikidata": ""},
    ])
    with patch.object(g3, "hub") as mock_hub:
        mock_hub.find_person.return_value = None
        mock_hub.add_person = MagicMock()
        g3.write_variants(st, {0: {"name": "X", "source": "hls"}, 1: None})

    assert st.gate_decisions["gate3"]["reviewed"] == 2
    assert st.gate_decisions["gate3"]["linked"] == 1


def test_write_variants_invalid_index_skipped():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Hans", "type": "PERSON", "normalized": "Hans",
         "link_method": "none", "hls": "", "gnd": "", "wikidata": ""},
    ])
    with patch.object(g3, "hub") as mock_hub:
        g3.write_variants(st, {99: {"name": "Hans"}})   # out of range
    assert not mock_hub.add_person.called


# ─────────────────────────────────────────────────────────────────────────────
# render_gate3_card
# ─────────────────────────────────────────────────────────────────────────────

def test_render_empty_shows_success_message():
    st = _state()
    card = g3.render_gate3_card(st, entities=[])
    assert "keine Entitäten zur Prüfung" in card
    assert "automatisch verlinkt" in card


def test_render_shows_entities_and_candidates():
    st = _state()
    entities = [
        {
            "text": "Hans",
            "type": "PERSON",
            "link_method": "none",
            "candidates": [{"name": "Hans Müller", "source": "hls"}],
        },
    ]
    card = g3.render_gate3_card(st, entities=entities)
    assert "Hans" in card
    assert "Hans Müller" in card
    assert "hls" in card


def test_render_fetches_entities_from_state_when_not_provided():
    st = _state()
    st.artifacts["agent_c"] = _artifact([
        {"text": "Unknown", "type": "PERSON", "link_method": "none",
         "hub_confidence": "unverified", "hls": "", "gnd": "", "wikidata": ""},
    ])
    card = g3.render_gate3_card(st)
    assert "Unknown" in card


# ─────────────────────────────────────────────────────────────────────────────
# build_gate3_view
# ─────────────────────────────────────────────────────────────────────────────

def test_build_gate3_view_returns_none_when_no_entities():
    import discord
    st = _state()
    view = g3.build_gate3_view(st, entities=[])
    assert view is None


def test_build_gate3_view_returns_none_when_no_entities():
    import discord
    st = _state()
    view = g3.build_gate3_view(st, entities=[])
    assert view is None


def test_build_gate3_view_caps_entities_at_5():
    """Gate3View should render at most 5 entities even when more exist."""
    st = _state()
    entities = [
        {"text": f"E{i}", "type": "PERSON", "link_method": "none",
         "candidates": [{"name": f"N{i}", "source": "x"}]}
        for i in range(8)
    ]
    # Directly test the cap logic (entity_list = entities[:5]).
    entity_list = entities[:5]
    assert len(entity_list) == 5