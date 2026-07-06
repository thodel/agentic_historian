"""Tests for Agent C entity extraction (#37).

Verifies all 8 entity types survive the pipeline and that the social/care payload
types (SOCIAL_GROUP, CARE_ACTION, ROLE) are linked to the hub's controlled
vocabulary. The LLM call is mocked, so this runs offline (no GPUStack/VPN).

Run:  python tests/test_entity_agent.py   (or: pytest)
"""

import json
import pathlib
import sys

# Make the package importable whether run via pytest or directly.
PKG = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG))

import config  # noqa: E402

config.ensure_dirs()

from agents import entity_agent  # noqa: E402

ALL_TYPES = ["PERSON", "PLACE", "ORG", "SOCIAL_GROUP",
             "CARE_ACTOR", "CARE_ACTION", "ROLE", "DATE"]

_FAKE_EXTRACTION = {"entities": [
    {"text": "Heinrich von Wiler", "type": "PERSON", "normalised": "Heinrich von Wiler", "context": "Vogt"},
    {"text": "Thun", "type": "PLACE", "normalised": "Thun", "context": "ze Thun"},
    {"text": "Rat zu Bern", "type": "ORG", "normalised": "Rat zu Bern", "context": ""},
    {"text": "arme lüt", "type": "SOCIAL_GROUP", "normalised": "arme lüt", "context": ""},
    {"text": "Pflegende", "type": "CARE_ACTOR", "normalised": "pflegende", "context": ""},
    {"text": "versorgung", "type": "CARE_ACTION", "normalised": "versorgung", "context": ""},
    {"text": "Vogt", "type": "ROLE", "normalised": "vogt", "context": ""},
    {"text": "1442", "type": "DATE", "normalised": "1442", "context": ""},
]}


def test_prompt_lists_all_eight_types():
    for t in ALL_TYPES:
        assert t in entity_agent.SYSTEM, f"{t} missing from the NER system prompt"


def test_all_eight_types_preserved_and_enriched():
    original = entity_agent.gs.chat_text
    entity_agent.gs.chat_text = lambda *a, **k: json.dumps(_FAKE_EXTRACTION)
    # Hermetic: this test asserts the LOCAL hub-seed enrichment (hub_p_example /
    # hub_loc_example). The MCP federation transport is real now (#189) and the
    # tei sources are publicly reachable, so a live hit would override the seed
    # and make this test network-dependent. Disable federation → exercise the
    # local hub chain deterministically, offline (as the module docstring says).
    mcp_was = config.ENABLE_MCP_LINKING
    config.ENABLE_MCP_LINKING = False
    try:
        result = entity_agent.extract_entities("test_p37", "dummy transcription")
    finally:
        entity_agent.gs.chat_text = original
        config.ENABLE_MCP_LINKING = mcp_was

    by_type = {e["type"]: e for e in result["entities"]}

    # 1) all 8 types survive (#37: SOCIAL_GROUP + CARE_ACTION must not be dropped)
    assert set(ALL_TYPES) == set(by_type), \
        f"missing types: {set(ALL_TYPES) - set(by_type)}"

    # 2) PERSON / PLACE link to the person/place register (seed examples)
    assert by_type["PERSON"].get("hub_id") == "hub_p_example"
    assert by_type["PLACE"].get("hub_id") == "hub_loc_example"

    # 3) social/care/role types link to the controlled vocabulary (taxonomies)
    assert by_type["SOCIAL_GROUP"].get("controlled_vocab") == "arme lüt"
    assert by_type["CARE_ACTION"].get("controlled_vocab") == "versorgung"
    assert by_type["ROLE"].get("controlled_vocab") == "vogt"


if __name__ == "__main__":
    test_prompt_lists_all_eight_types()
    test_all_eight_types_preserved_and_enriched()
    print("✅ all Agent C entity tests passed")
