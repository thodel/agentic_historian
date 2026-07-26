"""Offline contract tests for #336: selected text is a closest reading."""

import ast
import json
import sys
from pathlib import Path

import pytest
from rdflib import Graph, URIRef

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from eval.harness import cer_table
from knowledge_hub.rdf_export import SDHSS, closest_reading_to_rdf
from path_compare import apply_combined_choice
from runstate import RunState
from utils.publish_github import _index_md


def test_confirmation_stores_closest_reading_with_provenance():
    state = RunState(doc_id="doc-336")
    state.gate_decisions["user"] = "discord:817396"
    paths = {"vlm": "Alpha beta", "kraken": "Alpha betta"}

    text = apply_combined_choice(state, ["vlm"], paths)

    assert state.closest_reading == {
        "text": text,
        "candidates_offered": paths,
        "chosen": ["vlm"],
        "combined": False,
        "editor_pseudonym": state.closest_reading["editor_pseudonym"],
        "confirmed_at": state.closest_reading["confirmed_at"],
        "status": "revisable_editorial_choice",
    }
    assert state.closest_reading["editor_pseudonym"].startswith("editor-")
    assert "817396" not in state.closest_reading["editor_pseudonym"]


def test_public_page_calls_it_closest_available_reading():
    closest = {
        "text": "Alpha beta", "chosen": ["vlm"], "combined": False,
        "editor_pseudonym": "editor-123", "confirmed_at": "2026-07-26T00:00:00Z",
    }
    page = _index_md(
        "doc-336",
        {"pipeline.json": __import__("json").dumps(
            {"closest_reading": closest}).encode()},
        None,
    )
    assert "Closest available reading" in page
    assert "not an independently established reference text" in page


def test_pipeline_export_carries_closest_reading(tmp_path, monkeypatch):
    import config
    import orchestrator

    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "META_LOG_PATH", tmp_path / "errors.json")
    state = RunState(doc_id="doc-336")
    apply_combined_choice(state, ["vlm"], {"vlm": "Alpha beta"})
    state.save()

    orchestrator._save_pipeline_result(
        "doc-336", orchestrator.PipelineContext("doc-336"), from_runstate=True
    )
    exported = json.loads(
        (config.OUTPUTS_DIR / "doc-336_pipeline.json").read_text(encoding="utf-8")
    )
    assert exported["closest_reading"] == state.closest_reading


def test_kg_labels_editorial_text_as_closest_reading():
    graph = closest_reading_to_rdf(
        Graph(), URIRef("https://example.test/doc/336"),
        {"text": "Alpha beta", "chosen": ["vlm"],
         "candidates_offered": {"vlm": "Alpha beta"}},
    )
    assert (URIRef("https://example.test/doc/336"),
            SDHSS["hasClosestReading"], None) in graph


def test_eval_rejects_closest_reading_as_reference():
    with pytest.raises(ValueError, match="cannot be used"):
        cer_table({"vlm": "Alpha beta"}, None, {
            "text": "Alpha beta", "status": "revisable_editorial_choice",
        })


def test_selected_text_contract_has_no_truth_aliases():
    """Guard the modules that bind/export the historian-selected text.

    Legitimate independent-reference terminology remains confined to eval code.
    Any future scholarly edition adapter must be explicitly added to this list
    with a comment explaining why it is independently established.
    """
    relevant = [
        PKG / "path_compare.py",
        PKG / "runstate.py",
        PKG / "orchestrator.py",
        PKG / "utils" / "publish_github.py",
        PKG / "knowledge_hub" / "rdf_export.py",
    ]
    forbidden = {"ground_truth", "groundtruth", "gold", "reference"}
    violations = []
    for path in relevant:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.lower() in forbidden:
                violations.append(f"{path.name}:{node.lineno}:{node.id}")
    assert not violations, "selected text received a truth/reference alias: " + ", ".join(violations)
