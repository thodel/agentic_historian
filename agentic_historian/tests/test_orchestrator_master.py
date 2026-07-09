"""
tests/test_orchestrator.py

Tests for orchestrator wiring — ensures agents are callable individually
and pipeline context flows correctly.
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import the module (will work even without agent_a)
import orchestrator


class FakePipelineContext:
    """Fake PipelineContext for testing without real files."""
    def __init__(self):
        self.doc_id = "test_doc_001"
        self.image_path = None
        self.transcription = "Test transcription text"
        self.source_description = ""
        self.source_json = {}
        self.entities = []
        self.corpus = {}
        self.a_meta = {}
        self.b_meta = {}
        self.c_meta = {}
        self.errors = []


@patch.object(orchestrator, "DUAL_AVAILABLE", False)
def test_pipeline_context_default():
    """PipelineContext initializes with expected fields."""
    ctx = orchestrator.PipelineContext(doc_id="doc1")
    assert ctx.doc_id == "doc1"
    assert ctx.errors == []
    assert ctx.a_meta == {}


def test_pipeline_result_dataclass():
    """PipelineResult is a dict (no wrapper class needed)."""
    result = {
        "doc_id": "test",
        "success": True,
        "transcription": "foo",
    }
    assert isinstance(result, dict)
    assert result["doc_id"] == "test"


@patch.object(orchestrator, "DUAL_AVAILABLE", False)
@patch.object(orchestrator.agent_a, "process_file")
def test_run_agent_a_individual_call(mock_process_file):
    """run_agent_a() is independently callable (no pipeline needed)."""
    mock_process_file.return_value = {
        "doc_id": "img_001",
        "transcription": "Hello world",
        "success": True,
    }

    result = orchestrator.run_agent_a("/tmp/img_001.jpg")

    assert result["doc_id"] == "img_001"
    assert result["success"] is True
    mock_process_file.assert_called_once_with(Path("/tmp/img_001.jpg"))


@patch.object(orchestrator, "DUAL_AVAILABLE", False)
@patch.object(orchestrator.agent_b, "describe")
def test_run_agent_b_individual_call(mock_describe):
    """run_agent_b() is independently callable."""
    mock_describe.return_value = {
        "doc_id": "doc_001",
        "source_description": "14th century manuscript...",
        "source_json": {"Datierung": {"wert": "1350–1380"}},
        "care_flag": {"is_care_related": False},
    }

    result = orchestrator.run_agent_b("doc_001")

    assert result["doc_id"] == "doc_001"
    assert "source_description" in result
    assert "source_json" in result
    mock_describe.assert_called_once()


@patch.object(orchestrator, "DUAL_AVAILABLE", False)
@patch.object(orchestrator.agent_c, "extract_entities")
def test_run_agent_c_individual_call(mock_extract):
    """run_agent_c() is independently callable."""
    mock_extract.return_value = {
        "doc_id": "doc_001",
        "entities": [],
        "success": True,
    }

    result = orchestrator.run_agent_c("doc_001")

    assert result["doc_id"] == "doc_001"
    mock_extract.assert_called_once()


@patch.object(orchestrator, "DUAL_AVAILABLE", False)
@patch.object(orchestrator.agent_e, "generate_report")
def test_run_agent_e_individual_call(mock_report):
    """run_agent_e() is independently callable."""
    mock_report.return_value = {
        "generated_at": "2026-01-01T00:00:00",
        "token_usage": {"estimated_tokens": 0},
    }

    result = orchestrator.run_agent_e()

    assert "generated_at" in result
    assert "token_usage" in result
    mock_report.assert_called_once()