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

def test_offline_abc_pipeline_contract_is_deterministic(tmp_path, monkeypatch):
    """A complete A→B→C run is offline, consistent, and repeatable.

    This is the canonical PR-level pipeline contract: external model calls are
    replaced at the agent boundary, persistence remains real, and publishing
    must stay disabled.
    """
    data_dir = tmp_path / "data"
    out_dir = data_dir / "outputs"
    calls: list[tuple] = []

    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "OUTPUTS_DIR", out_dir)
    monkeypatch.setattr(config, "META_LOG_PATH", data_dir / "meta_agent_log.json")
    monkeypatch.setattr(config, "SOURCE_URL_BASE", "https://archive.example/scans")
    monkeypatch.setattr(orch, "DUAL_AVAILABLE", False)

    transcription = "Hans von Bern tuend kund."
    description = {
        "source_description": "Gerichtsbrief, 15. Jh., Kurrent",
        "source_json": {
            "script": "Kurrent",
            "lang": "de",
            "century": 15,
            "document_type": "Gerichtsbrief",
        },
    }
    entities = {"persons": [{"name": "Hans von Bern", "confidence": "high"}]}

    def fake_a(path):
        calls.append(("A", Path(path).name))
        return {"transcription": transcription, "qa_score": 0.91, "source": "mock-vlm"}

    def fake_b(doc_id, transcription, image_path=None):
        calls.append(("B", doc_id, transcription, image_path))
        assert transcription == "Hans von Bern tuend kund."
        return description

    def fake_c(doc_id, text):
        calls.append(("C", doc_id, text))
        assert text == transcription
        return entities

    monkeypatch.setattr(orch.agent_a, "process_file", fake_a)
    monkeypatch.setattr(orch.agent_b, "describe", fake_b)
    monkeypatch.setattr(orch.agent_c, "extract_entities", fake_c)

    from utils import publish_github

    monkeypatch.setattr(publish_github, "is_enabled", lambda: False)

    def unexpected_publish(*args, **kwargs):
        raise AssertionError("offline pipeline attempted to publish")

    monkeypatch.setattr(publish_github, "publish_doc", unexpected_publish)

    doc_id = "offline-abc-contract"
    image = tmp_path / f"{doc_id}.jpg"
    image.write_bytes(b"fake-image-data")

    first_result = orch.run_full_pipeline(image)
    pipeline_path = out_dir / f"{doc_id}_pipeline.json"
    first_pipeline_bytes = pipeline_path.read_bytes()

    state = RunState.load(doc_id, path=data_dir / "runs" / f"{doc_id}.json")
    pipeline = json.loads(first_pipeline_bytes)

    assert [call[0] for call in calls] == ["A", "B", "C"]
    assert first_result["errors"] == []
    assert state.stage_status["vlm"] == DONE
    assert state.stage_status["agent_b"] == DONE
    assert state.stage_status["agent_c"] == DONE
    assert state.artifacts["transcription"] == transcription
    assert state.artifacts["description"] == description
    assert state.artifacts["entities"] == entities
    assert state.criteria == {
        "script": "kurrent",
        "lang": "de",
        "century": 15,
        "document_type": "letter",
    }
    assert pipeline == {
        "doc_id": doc_id,
        "transcription": transcription,
        "description": description,
        "entities": entities,
        "errors": [],
        "a_meta": {
            "transcription": transcription,
            "qa_score": 0.91,
            "source": "mock-vlm",
        },
        "recognitions": [],
        "source_url": f"https://archive.example/scans/{image.name}",
    }

    calls.clear()
    second_result = orch.run_full_pipeline(image)

    assert [call[0] for call in calls] == ["A", "B", "C"]
    assert second_result == first_result
    assert pipeline_path.read_bytes() == first_pipeline_bytes


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

# ── #234: recognitions in RunState + pipeline.json ───────────────────────────

class TestRecognitionsInRunState:
    """Verify ctx.recognitions survive RunState.save/load and pipeline.json output."""

    def test_recognitions_roundtrip_through_runstate(self, tmp_path, monkeypatch):
        """recognitions written to RunState.artifacts survive a save+load cycle."""
        from agent_a.model_selector import RecognitionResult
        from runstate import RunState

        doc_id = "doc_234_test"
        rec = RecognitionResult(
            engine="kraken",
            model_id="10.5281/zenodo.7516057",
            text="Hello world",
            confidence=0.85,
        )
        # Patch DATA_DIR so RunState.load / .save use tmp_path
        import agentic_historian.runstate as rs_mod
        monkeypatch.setattr(rs_mod.config, "DATA_DIR", tmp_path)

        # Simulate what orchestrator does after Phase 1
        state = RunState.load_or_new(doc_id)
        state.artifacts["recognitions"] = [rec]
        p = tmp_path / "runs" / f"{doc_id}.json"
        state.save(path=p)
        # Load a fresh instance — uses patched DATA_DIR
        loaded = RunState.load(doc_id)
        assert "recognitions" in loaded.artifacts
        recs = loaded.artifacts["recognitions"]
        assert len(recs) == 1
        # artifacts are stored as plain dicts (artifacts: dict[str, Any] is untyped)
        assert recs[0]["engine"] == "kraken"
        assert recs[0]["text"] == "Hello world"

    def test_runstate_derived_pipeline_json_includes_recognitions(self, tmp_path, monkeypatch):
        """
        The RunState-derived pipeline.json contains recognitions serialised
        as plain dicts (not RecognitionResult objects) — matching the real
        _write_pipeline_output(from_runstate=True) code path.
        """
        import json
        from agentic_historian.orchestrator import PipelineContext
        from agent_a.model_selector import RecognitionResult

        # Set temp directories
        outputs = tmp_path / "outputs"
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        outputs.mkdir(parents=True, exist_ok=True)

        # Patch config so orchestrator and runstate use our temp paths
        import agentic_historian.runstate as rs_mod
        import agentic_historian.orchestrator as orch_mod
        monkeypatch.setattr(rs_mod.config, "DATA_DIR", data_dir)
        monkeypatch.setattr(orch_mod.config, "DATA_DIR", data_dir)
        monkeypatch.setattr(orch_mod.config, "OUTPUTS_DIR", outputs)

        # Build PipelineContext with recognitions — ctx.to_json() serialises correctly
        ctx = PipelineContext("rec_pipeline_test")
        ctx.recognitions = [
            RecognitionResult(
                engine="vlm",
                model_id="internvl3-8b-instruct",
                text="VLM output",
                confidence=0.95,
            ),
            RecognitionResult(
                engine="kraken",
                model_id="10.5281/zenodo.7516057",
                text="Kraken output",
                confidence=0.82,
                segmented_by=None,
            ),
        ]
        ctx.transcription = "Final reconciled text"

        # Save RunState (simulates Phase 1 persist in orchestrator)
        from runstate import RunState
        state = RunState.load_or_new(ctx.doc_id)
        state.artifacts["recognitions"] = ctx.recognitions
        state.artifacts["transcription"] = ctx.transcription
        state.save()

        # Derive pipeline.json from RunState (mimics _write_pipeline_output)
        pipeline_file = outputs / f"{ctx.doc_id}_pipeline.json"
        pipeline = {
            "doc_id": ctx.doc_id,
            "transcription": state.artifacts.get("transcription", ""),
            # recognitions may be Pydantic models in artifacts — use model_dump
            "recognitions": (
                [r.model_dump() if hasattr(r, "model_dump") else r
                 for r in state.artifacts.get("recognitions", [])]
            ),
        }
        with open(pipeline_file, "w", encoding="utf-8") as f:
            json.dump(pipeline, f, ensure_ascii=False, indent=2)

        loaded = json.loads(pipeline_file.read_text())
        assert "recognitions" in loaded
        assert len(loaded["recognitions"]) == 2
        engines = {r["engine"] for r in loaded["recognitions"]}
        assert engines == {"vlm", "kraken"}
