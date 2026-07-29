"""Tests for KG-1 (#327): persist authority identifiers from the MCP federation.

Fully offline — the federated search is injected, so no MCP, no network, no
GPUStack. Run from the repo root:
    pytest agentic_historian/tests/test_kg_1_authority_links.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import entity_index as ei
from utils.mcp_client import FederatedResult, PersonResult


def P(source, pid, name, **kw):
    return PersonResult(source=source, pid=pid, name=name, **kw)


def fake_search(persons=(), failed=()):
    """Build an injectable ``(query, limit) -> FederatedResult`` callable."""
    def _search(query, limit=20):
        return FederatedResult(persons=list(persons), failed_sources=list(failed))
    return _search


# ── ids + URIs ───────────────────────────────────────────────────────────────

def test_resolved_entity_carries_ids_and_public_uris():
    links, res = ei.resolve_entity_links(
        "Henin Rost",
        search=fake_search([P("hls", "42", "Henin Rost",
                              hls_id=42, gnd_id="12175376X")]),
    )
    by = {l.source: l for l in links}
    assert by["gnd"].id == "12175376X"
    assert by["gnd"].uri == "https://d-nb.info/gnd/12175376X"
    assert by["hls"].uri == "https://hls-dhs-dss.ch/de/articles/42"
    assert "hls" in res.answered


def test_authority_uri_is_none_for_unknown_source():
    assert ei.authority_uri("hbls", "hbls:1:51") is None
    assert ei.authority_uri("gnd", "") is None
    assert ei.authority_uri("gnd", "118540238") == "https://d-nb.info/gnd/118540238"


# ── link, don't copy ─────────────────────────────────────────────────────────

def test_no_source_biography_fields_are_persisted():
    """The record carries identity and trust — never source payload."""
    links, _ = ei.resolve_entity_links(
        "Henin Rost",
        search=fake_search([P("hls", "42", "Henin Rost", hls_id=42,
                              gnd_id="118540238",
                              life_dates="1300–1370",
                              occupation="Schultheiss",
                              notes="a biography we must not keep")]),
    )
    assert links
    allowed = {"source", "id", "uri", "confidence", "conflicts"}
    for l in links:
        assert set(l.as_dict()) == allowed

    blob = json.dumps([l.as_dict() for l in links], ensure_ascii=False)
    for leaked in ("1300", "1370", "Schultheiss", "biography"):
        assert leaked not in blob


# ── a dark source is not a "no match" ────────────────────────────────────────

def test_unreachable_source_is_recorded_and_degrades_gracefully():
    links, res = ei.resolve_entity_links(
        "Henin Rost",
        search=fake_search([P("hls", "42", "Henin Rost", hls_id=42)],
                           failed=["hbls"]),
    )
    assert "hbls" in res.queried          # we did ask
    assert "hbls" in res.unavailable      # …and it was dark
    assert "hbls" not in res.answered
    assert "hls" in res.answered
    assert links                          # a dead source degrades, never fails


def test_no_match_is_distinct_from_unavailable():
    links, res = ei.resolve_entity_links("Nobody At All", search=fake_search([]))
    assert links == []
    assert res.unavailable == []          # everything answered; nothing matched
    assert res.answered == res.queried


def test_total_federation_failure_marks_every_source_dark():
    def boom(query, limit=20):
        raise RuntimeError("federation down")

    links, res = ei.resolve_entity_links("Henin Rost", search=boom)
    assert links == []
    assert res.unavailable == res.queried
    assert res.answered == []


# ── conflicts: never an arbitrary pick ───────────────────────────────────────

def test_two_gnd_ids_keep_both_lower_confidence_and_name_the_conflict():
    links, _ = ei.resolve_entity_links(
        "Hans Wiler",
        search=fake_search([
            P("hls",  "1", "Hans Wiler", gnd_id="111111111"),
            P("hbls", "2", "Hans Wiler", gnd_id="222222222"),
        ]),
    )
    gnd = [l for l in links if l.source == "gnd"]
    assert {l.id for l in gnd} == {"111111111", "222222222"}   # both kept
    for l in gnd:
        assert "multi_gnd" in l.conflicts
        assert l.confidence != "high"


def test_uncontested_id_keeps_its_confidence_and_has_no_conflicts():
    links, _ = ei.resolve_entity_links(
        "Hans Wiler",
        search=fake_search([P("hls", "1", "Hans Wiler", gnd_id="111111111")]),
    )
    gnd = [l for l in links if l.source == "gnd"]
    assert len(gnd) == 1
    assert gnd[0].conflicts == []
    assert gnd[0].confidence == "high"


# ── never invent a URI ───────────────────────────────────────────────────────

def test_source_without_public_uri_yields_null_uri():
    links, _ = ei.resolve_entity_links(
        "Henin Rost",
        search=fake_search([P("hbls", "hbls:1:51", "Henin Rost")]),
    )
    hbls = [l for l in links if l.source == "hbls"]
    assert hbls and hbls[0].id == "hbls:1:51"
    assert hbls[0].uri is None
    # every emitted URI is either absent or a real https authority URI
    assert all(l.uri is None or l.uri.startswith("https://") for l in links)


# ── stable identity across documents ─────────────────────────────────────────

def _write_doc(dirpath: Path, doc_id: str, name: str) -> None:
    (dirpath / f"{doc_id}_entities.json").write_text(
        json.dumps({"entities": [
            {"text": name, "type": "PERSON", "context": f"... in {doc_id} ..."}
        ]}),
        encoding="utf-8",
    )


def test_same_entity_in_two_documents_is_one_identity_record(tmp_path):
    _write_doc(tmp_path, "doc-a", "Henin Rost")
    _write_doc(tmp_path, "doc-b", "Henin Rost")

    index = ei.build_index(tmp_path)
    ei.resolve_index(index, search=fake_search([
        P("hls", "42", "Henin Rost", hls_id=42, gnd_id="12175376X",
          life_dates="1300–1370", occupation="Schultheiss"),
    ]))

    assert len(index.entries) == 1
    entry = next(iter(index.entries.values()))
    assert {m.doc_id for m in entry.mentions} == {"doc-a", "doc-b"}
    assert entry.gnd == "12175376X"           # uncontested → promoted
    assert entry.resolution is not None

    out = ei.write_index_json(index, tmp_path / "entity_index.json")
    blob = out.read_text(encoding="utf-8")
    assert "12175376X" in blob
    for leaked in ("1300", "Schultheiss"):    # still no copied source payload
        assert leaked not in blob


def test_contested_id_is_not_promoted_to_the_flat_field(tmp_path):
    _write_doc(tmp_path, "doc-a", "Hans Wiler")

    index = ei.build_index(tmp_path)
    ei.resolve_index(index, search=fake_search([
        P("hls",  "1", "Hans Wiler", gnd_id="111111111"),
        P("hbls", "2", "Hans Wiler", gnd_id="222222222"),
    ]))

    entry = next(iter(index.entries.values()))
    assert entry.gnd == ""                    # contested → never "the" id
    assert len([l for l in entry.links if l.source == "gnd"]) == 2


def test_non_person_types_are_left_unresolved(tmp_path):
    (tmp_path / "doc-a_entities.json").write_text(
        json.dumps({"entities": [
            {"text": "Thun", "type": "PLACE", "context": "zu Thun"}
        ]}),
        encoding="utf-8",
    )
    index = ei.build_index(tmp_path)
    ei.resolve_index(index, search=fake_search([P("hls", "9", "Thun", hls_id=9)]))

    entry = next(iter(index.entries.values()))
    assert entry.links == []                  # no place search exists yet
    assert entry.resolution is None


def test_index_json_is_deterministic(tmp_path):
    _write_doc(tmp_path, "doc-a", "Henin Rost")
    index = ei.build_index(tmp_path)
    ei.resolve_index(index, search=fake_search([
        P("hls", "42", "Henin Rost", hls_id=42, gnd_id="12175376X")]))

    a = ei.write_index_json(index, tmp_path / "a.json").read_text(encoding="utf-8")
    b = ei.write_index_json(index, tmp_path / "b.json").read_text(encoding="utf-8")
    assert a == b


# ── the markdown renderer still works off the shared URI table ───────────────

def test_authority_link_markdown_uses_the_shared_templates():
    entry = ei.EntityEntry(name="Henin Rost", type="PERSON",
                           gnd="12175376X", hls="42", wikidata="Q1")
    md = ei._authority_links(entry)
    assert "[GND](https://d-nb.info/gnd/12175376X)" in md
    assert "[HLS](https://hls-dhs-dss.ch/de/articles/42)" in md
    assert "[Wikidata](https://www.wikidata.org/wiki/Q1)" in md
