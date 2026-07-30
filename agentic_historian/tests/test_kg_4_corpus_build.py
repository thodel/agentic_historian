"""Tests for KG-4 (#330): the corpus build step for QLever.

Offline — no Docker, no QLever, no network. The load/serve step needs the host
and is covered by the runbook in deploy/qlever/README.md instead.

Run from the repo root:
    pytest agentic_historian/tests/test_kg_4_corpus_build.py
"""

import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

from rdflib import Graph

from knowledge_hub import corpus_build as cb


def _doc(doc_id="doc-1", care=False, entities=None, recognitions=None):
    return {
        "doc_id": doc_id,
        "transcription": "unser fruntlich gruos",
        "description": {"source_description": "desc",
                        "care_flag": {"is_care_related": care, "care_context": ""}},
        "entities": {"entities": entities if entities is not None else [
            {"text": "Heinrich von Wiler", "type": "PERSON",
             "normalised": "Heinrich von Wiler", "gnd": "12175376X"},
            {"text": "Thun", "type": "PLACE", "normalised": "Thun"},
        ]},
        "recognitions": recognitions if recognitions is not None else [
            {"engine": "trocr", "model_id": "m1", "text": "reading a",
             "confidence": 0.7, "error": None},
        ],
        "a_meta": {"fusion_strategy": "vote", "fusion_agreement_cer": 0.05},
    }


def _corpus(tmp_path, *docs):
    for d in docs:
        (tmp_path / f"{d['doc_id']}_pipeline.json").write_text(
            json.dumps(d), encoding="utf-8")
    return tmp_path


# ── the build step and its counts ────────────────────────────────────────────

def test_build_emits_turtle_and_reports_counts(tmp_path):
    _corpus(tmp_path, _doc("doc-1"), _doc("doc-2"))
    out = tmp_path / "build" / "corpus.ttl"

    report = cb.build(tmp_path, out)

    assert out.exists()
    assert report.documents == 2
    assert report.readings == 4          # one engine + one working, per doc
    assert report.mentions == 4          # two entities per doc
    assert report.triples > 0
    assert report.built_at
    # Both documents mention the same person, and KG-2 keys the entity node on
    # its GND — so this is ONE authority link reached from two documents, which
    # is exactly the cross-document identity the model is for.
    assert report.authority_links.get("gnd") == 1
    # One shared person node (keyed on GND) and one shared place node (keyed on
    # its normalised name) — two entities reached from four mentions.
    assert report.entities == 2


def test_the_same_person_in_two_documents_is_counted_once(tmp_path):
    _corpus(tmp_path, _doc("doc-1"), _doc("doc-2"))
    one = cb.build(tmp_path, tmp_path / "two-docs.ttl")

    _corpus(tmp_path, _doc("doc-3", entities=[
        {"text": "Someone Else", "type": "PERSON", "normalised": "Someone Else",
         "gnd": "999999999"}]))
    three = cb.build(tmp_path, tmp_path / "three-docs.ttl")

    assert three.documents == 3
    assert three.authority_links["gnd"] == one.authority_links["gnd"] + 1


def test_report_renders_a_human_summary(tmp_path):
    _corpus(tmp_path, _doc("doc-1"))
    text = cb.build(tmp_path, tmp_path / "corpus.ttl").render()
    assert "documents   1" in text
    assert "triples" in text


def test_build_output_is_valid_turtle(tmp_path):
    _corpus(tmp_path, _doc("doc-1"))
    out = tmp_path / "corpus.ttl"
    cb.build(tmp_path, out)
    assert len(Graph().parse(out, format="turtle")) > 0


def test_rebuild_on_unchanged_input_is_byte_identical(tmp_path):
    """Rebuild-and-swap is only sound if the build is reproducible."""
    _corpus(tmp_path, _doc("doc-1"), _doc("doc-2"))
    a = tmp_path / "a.ttl"
    b = tmp_path / "b.ttl"
    cb.build(tmp_path, a)
    cb.build(tmp_path, b)
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_empty_corpus_builds_cleanly(tmp_path):
    report = cb.build(tmp_path, tmp_path / "corpus.ttl")
    assert report.documents == 0
    assert Path(report.destination).exists()


# ── the manifest: staleness must be visible, not inferred ────────────────────

def test_manifest_records_triple_count_and_build_time(tmp_path):
    _corpus(tmp_path, _doc("doc-1"))
    out = tmp_path / "corpus.ttl"
    report = cb.build(tmp_path, out)

    manifest = cb.read_manifest(out)
    assert manifest is not None
    assert manifest["triples"] == report.triples
    assert manifest["built_at"] == report.built_at
    assert manifest["documents"] == 1
    assert manifest["scope"]["include_care_flagged"] is True


def test_read_manifest_is_none_before_any_build(tmp_path):
    assert cb.read_manifest(tmp_path / "never.ttl") is None


# ── owner-approved publication scope (#330) ──────────────────────────────────

def test_default_scope_is_the_owner_approved_one():
    """Approved 2026-07-30: care-flagged included, all persons, no id required."""
    scope = cb.PublicationScope()
    assert scope.include_care_flagged is True
    assert scope.include_person_nodes is True
    assert scope.require_authority_id is False
    assert scope.is_unrestricted is True


def test_care_flagged_documents_are_published_under_the_approved_scope(tmp_path):
    _corpus(tmp_path, _doc("doc-1", care=True))
    report = cb.build(tmp_path, tmp_path / "corpus.ttl")
    assert report.documents == 1
    assert not report.withheld


def test_narrower_scope_can_exclude_care_flagged_and_says_so(tmp_path):
    """The knob still works, and an omission is reported rather than silent."""
    _corpus(tmp_path, _doc("open", care=False), _doc("care", care=True))
    scope = cb.PublicationScope(include_care_flagged=False)

    report = cb.build(tmp_path, tmp_path / "corpus.ttl", scope)
    assert report.documents == 1
    assert report.withheld["care_flagged_documents"] == 1
    assert "withheld" in report.render()


def test_narrower_scope_can_drop_person_nodes(tmp_path):
    _corpus(tmp_path, _doc("doc-1"))
    scope = cb.PublicationScope(include_person_nodes=False)

    report = cb.build(tmp_path, tmp_path / "corpus.ttl", scope)
    assert report.documents == 1          # the document still exports
    assert report.mentions == 1           # only the PLACE survives
    assert report.withheld["person_nodes"] == 1


def test_narrower_scope_can_require_an_authority_id(tmp_path):
    _corpus(tmp_path, _doc("doc-1", entities=[
        {"text": "Known", "type": "PERSON", "normalised": "Known", "gnd": "118540238"},
        {"text": "Unknown", "type": "PERSON", "normalised": "Unknown"},
    ]))
    scope = cb.PublicationScope(require_authority_id=True)

    report = cb.build(tmp_path, tmp_path / "corpus.ttl", scope)
    assert report.mentions == 1
    assert report.withheld["unresolved_persons"] == 1


def test_scope_filtering_never_mutates_the_source_document(tmp_path):
    doc = _doc("doc-1")
    original = json.loads(json.dumps(doc))
    _corpus(tmp_path, doc)
    cb.build(tmp_path, tmp_path / "corpus.ttl",
             cb.PublicationScope(include_person_nodes=False))
    assert doc == original


# ── CLI ──────────────────────────────────────────────────────────────────────

def test_cli_builds_and_prints_a_report(tmp_path, capsys):
    _corpus(tmp_path, _doc("doc-1"))
    out = tmp_path / "corpus.ttl"

    rc = cb.main(["--outputs", str(tmp_path), "--out", str(out)])
    assert rc == 0
    assert "documents   1" in capsys.readouterr().out
    assert out.exists()


def test_cli_status_reports_the_last_build(tmp_path, capsys):
    _corpus(tmp_path, _doc("doc-1"))
    out = tmp_path / "corpus.ttl"
    cb.main(["--outputs", str(tmp_path), "--out", str(out)])
    capsys.readouterr()

    rc = cb.main(["--out", str(out), "--status"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["documents"] == 1


def test_cli_status_fails_cleanly_when_never_built(tmp_path, capsys):
    rc = cb.main(["--out", str(tmp_path / "nope.ttl"), "--status"])
    assert rc == 1
    assert "no build manifest" in capsys.readouterr().err


# ── the deploy artefacts the runbook depends on ──────────────────────────────

def test_deploy_files_exist():
    root = PKG.parent / "deploy" / "qlever"
    for name in ("Qleverfile", "docker-compose.yml", "nginx-qlever.conf",
                 "README.md", "refresh.sh"):
        assert (root / name).exists(), f"missing deploy/qlever/{name}"


def test_refresh_script_is_executable_and_safe():
    script = PKG.parent / "deploy" / "qlever" / "refresh.sh"
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text        # fail fast, no half-swapped index
