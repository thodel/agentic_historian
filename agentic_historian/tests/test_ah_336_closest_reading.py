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


# ── the guard must catch the call a developer would actually write ───────────

def test_eval_rejects_the_closest_reading_TEXT_not_only_the_dict():
    """The realistic circular-measurement mistake passes the *text*, not the dict:

        cer_table(recs, fused, reference=state.closest_reading["text"])

    A dict-only guard misses exactly that, so it would not have prevented anything.
    The text is tagged ClosestReadingText, and the harness refuses that type.
    """
    from runstate import ClosestReadingText
    tagged = ClosestReadingText("unser fruntlich gruos vor liebe getruwe")

    with pytest.raises(ValueError, match="cannot be used"):
        cer_table({"vlm": "voellig andere lesart"}, None, tagged)


def test_a_genuine_reference_string_is_still_accepted():
    """The guard must not block real evaluation — only the circular kind."""
    out = cer_table({"vlm": "Alpha beta"}, None, "Alpha beta")
    assert out["engines"]["vlm"]["cer"] == pytest.approx(0.0)


def test_the_confirmed_text_is_tagged_at_the_source(tmp_path, monkeypatch):
    """apply_combined_choice must produce a tagged text, or the guard never fires."""
    import config, path_compare
    from runstate import ClosestReadingText, RunState
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PREFERENCES_LOG_PATH", tmp_path / "p.jsonl")
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    st = RunState(doc_id="d-tag")
    paths = {"trocr/a": "eine lesart", "kraken/b": "andere lesart"}
    path_compare.apply_combined_choice(st, ["trocr/a"], paths, editor="817396")

    assert isinstance(st.closest_reading["text"], ClosestReadingText)


def test_the_editor_pseudonym_is_salted(tmp_path, monkeypatch):
    """An unsalted digest of a Discord id is reversible by brute force over an
    enumerable id space — and this value reaches the published RDF export."""
    import hashlib
    import config, path_compare
    from runstate import RunState
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    st = RunState(doc_id="d-salt")
    paths = {"trocr/a": "eine lesart", "kraken/b": "andere lesart"}
    path_compare.apply_combined_choice(st, ["trocr/a"], paths, editor="817396")

    pseudo = st.closest_reading["editor_pseudonym"]
    assert "817396" not in pseudo
    naive = "editor-" + hashlib.sha256(b"817396").hexdigest()[:12]
    assert pseudo != naive, "must be salted, not a bare hash of the platform id"


def test_the_raw_platform_id_is_never_persisted(tmp_path, monkeypatch):
    import config, path_compare
    from runstate import RunState
    monkeypatch.setattr(config, "FEEDBACK_DIR", tmp_path)
    monkeypatch.setattr(config, "PSEUDONYM_SALT_PATH", tmp_path / ".salt")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    st = RunState(doc_id="d-raw")
    paths = {"trocr/a": "eine lesart", "kraken/b": "andere lesart"}
    path_compare.apply_combined_choice(st, ["trocr/a"], paths, editor="817396")

    assert "817396" not in json.dumps(st.model_dump(mode="json"), ensure_ascii=False)
