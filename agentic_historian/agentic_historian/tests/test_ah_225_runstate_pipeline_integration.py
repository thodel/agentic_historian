"""#225: RunState is the single source of truth — every pipeline phase records
its output into data/runs/<doc_id>.json, and pipeline.json is derived from it.

Also verifies:
  - atomic save (os.replace / no partial writes)
  - source_url is stored in RunState

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_225_runstate_pipeline_integration.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config
import orchestrator as orch
from runstate import RunState, STAGES, DONE, PENDING


# ─── helpers ────────────────────────────────────────────────────────────────

class _MockAgentA:
    """Returns a canned transcription + qa_score."""

    @staticmethod
    def process_file(fp):
        return {"transcription": "Hans von Bern tuend kund…", "qa_score": 0.85}


class _MockAgentB:
    @staticmethod
    def describe(doc_id, transcription, image_path=None):
        return {
            "source_description": "Gerichtsverhandlung, 15. Jh., Kurrent",
            "source_json": {
                "script": "Kurrent",
                "lang": "de",
                "century": 15,
                "document_type": "Gerichtsbrief",
            },
        }


class _MockAgentC:
    @staticmethod
    def extract_entities(doc_id, transcription):
        return {"persons": [{"name": "Hans von Bern"}]}


class _NoOpHub:
    @staticmethod
    def add_doc(*a, **k):
        pass


# ─── tests ──────────────────────────────────────────────────────────────────

def test_runstate_recorded_after_vlm(tmp_path, monkeypatch):
    """Phase 1 marks VLM done in RunState before Agent B even starts."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(config, "HOT_FOLDER", tmp_path / "hot")
    monkeypatch.setattr(config, "PROCESSED_FOLDER", tmp_path / "processed")

    # Mock pipeline agents so we only exercise RunState recording
    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr

    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    doc_id = "saa-0001-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")  # fake JPEG

    # Patch run_full_pipeline_group's agent_a.transcribe_image (not process_file)
    # The group path calls transcribe_image; single-doc calls process_file.
    # For simplicity we test single-doc path (run_full_pipeline).
    # Make DUAL_AVAILABLE False to hit the simpler non-dual HTR branch.
    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)

    orch.run_full_pipeline(str(fp))

    # Check RunState
    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.stage_status["vlm"] == DONE, f"VLM not done: {state.stage_status['vlm']}"
    assert state.artifacts.get("transcription") == "Hans von Bern tuend kund…"
    assert state.artifacts.get("a_meta", {}).get("qa_score") == 0.85


def test_runstate_recorded_after_agent_b(tmp_path, monkeypatch):
    """Phase 2 marks agent_b done and stores the description."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(config, "HOT_FOLDER", tmp_path / "hot")
    monkeypatch.setattr(config, "PROCESSED_FOLDER", tmp_path / "processed")

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    doc_id = "saa-0002-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline(str(fp))

    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.stage_status["agent_b"] == DONE
    desc = state.artifacts.get("description", {})
    assert "Gerichtsverhandlung" in str(desc)
    # VLM is also done (was set in Phase 1)
    assert state.stage_status["vlm"] == DONE


def test_runstate_recorded_after_agent_c(tmp_path, monkeypatch):
    """Phase 4 marks agent_c done and stores entities."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(config, "HOT_FOLDER", tmp_path / "hot")
    monkeypatch.setattr(config, "PROCESSED_FOLDER", tmp_path / "processed")

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    doc_id = "saa-0003-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline(str(fp))

    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.stage_status["agent_c"] == DONE
    assert "Hans von Bern" in json.dumps(state.artifacts.get("entities", {}))


def test_runstate_source_url_stored(tmp_path, monkeypatch):
    """source_url is recorded in RunState and ends up in pipeline.json."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://drive.example.com/scans")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    doc_id = "saa-0004-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline(str(fp))

    # RunState must carry the derived URL
    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.source_url == f"https://drive.example.com/scans/{doc_id}.jpg", \
        f"source_url = {state.source_url!r}"

    # pipeline.json must also carry it (from_runstate path)
    pipeline_file = out_dir / f"{doc_id}_pipeline.json"
    assert pipeline_file.exists()
    pipeline = json.loads(pipeline_file.read_text(encoding="utf-8"))
    assert pipeline.get("source_url") == f"https://drive.example.com/scans/{doc_id}.jpg"


def test_runstate_source_url_explicit_override(tmp_path, monkeypatch):
    """An explicitly-passed source_url takes precedence over config-derived."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://drive.example.com/scans")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    doc_id = "saa-0005-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")

    explicit_url = "https://my.override.com/page/42"
    orch.run_full_pipeline(str(fp), source_url=explicit_url)

    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.source_url == explicit_url


def test_pipeline_json_derived_from_runstate(tmp_path, monkeypatch):
    """pipeline.json shape matches what ctx.to_json() used to produce,
    so existing consumers (publish_github) are not broken."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr
    monkeypatch.setattr(ar, "process_file", lambda fp: _MockAgentA.process_file(fp))
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)
    doc_id = "saa-0006-test"
    fp = tmp_path / f"{doc_id}.jpg"
    fp.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline(str(fp))

    pipeline_file = out_dir / f"{doc_id}_pipeline.json"
    assert pipeline_file.exists()
    pipeline = json.loads(pipeline_file.read_text(encoding="utf-8"))

    # Required fields that publish_github and other consumers depend on
    assert pipeline["doc_id"] == doc_id
    assert "transcription" in pipeline
    assert "description" in pipeline
    assert "entities" in pipeline
    assert "errors" in pipeline
    # a_meta is derived from RunState artifacts
    assert "a_meta" in pipeline
    # source_url absent when not configured
    assert "source_url" not in pipeline


def test_runstate_grouped_pipeline(tmp_path, monkeypatch):
    """run_full_pipeline_group records all four phases into RunState."""
    runs_dir = tmp_path / "runs"
    out_dir = tmp_path / "outputs"
    runs_dir.mkdir()
    out_dir.mkdir()

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://base")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    import agents.text_recognition as ar
    import agents.source_description as br
    import agents.entity_agent as cr

    def fake_transcribe(img):
        return {"transcription": f"page {img.name}", "qa_score": 0.9}

    monkeypatch.setattr(ar, "transcribe_image", fake_transcribe)
    monkeypatch.setattr(ar, "process_file", lambda fp: fake_transcribe(fp))
    monkeypatch.setattr(ar, "save_transcription", lambda *a, **k: None)
    monkeypatch.setattr(br, "describe",
                       lambda doc_id, transcription, image_path=None: _MockAgentB.describe(
                           doc_id, transcription, image_path))
    monkeypatch.setattr(cr, "extract_entities",
                       lambda doc_id, transcription: _MockAgentC.extract_entities(
                           doc_id, transcription))

    doc_id = "order-001-group"
    pages = [tmp_path / f"page_{i}.jpg" for i in [1, 2]]
    for p in pages:
        p.write_bytes(b"\xff\xd8\xff")

    orch.run_full_pipeline_group(doc_id, [str(p) for p in pages])

    state = RunState.load(doc_id, path=runs_dir / f"{doc_id}.json")
    assert state.stage_status["vlm"] == DONE
    assert state.stage_status["agent_b"] == DONE
    assert state.stage_status["agent_c"] == DONE
    # agent_d was not requested
    assert state.stage_status["agent_d"] == PENDING


def test_runstate_atomic_save_no_tmp_leftover(tmp_path, monkeypatch):
    """save() must use os.replace (atomic rename); no .tmp file left behind."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)

    rs = RunState(doc_id="atomic-test")
    rs.stage_status["vlm"] = DONE
    rs.artifacts["transcription"] = "test"
    p = rs.save(path=tmp_path / "atomic-test.json")

    # The final file must exist
    assert p.exists()
    # No leftover .tmp
    leftovers = list(tmp_path.glob("*.json.tmp"))
    assert leftovers == [], f"atomicity violation: leftover tmp files {leftovers}"
    # Content is valid JSON
    loaded = RunState.load("atomic-test", path=p)
    assert loaded.stage_status["vlm"] == DONE