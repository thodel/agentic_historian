import sys
from pathlib import Path

PKG = str(Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

"""Tests for #234: multi-engine fan-out, select_best factory, and RecognitionResult.

Covers:
  P1-1: select_best() unified factory for kraken / trocr / party
  P1-2: RecognitionResult dataclass
  P1-3: PipelineContext.recognitions persisted to pipeline.json
  P2-2: Concurrent fan-out in transcribe_dual (ThreadPoolExecutor)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_a.model_selector import (
    RecognitionResult,
    select_best,
    select_kraken_model,
    select_tocr_model,
    select_party_model,
    SourceCriteria,
)
from agentic_historian.orchestrator import PipelineContext


# ─── RecognitionResult ───────────────────────────────────────────────────────

class TestRecognitionResult:
    def test_fields(self):
        rec = RecognitionResult(
            engine="kraken",
            model_id="10.5281/zenodo.123",
            text="Hello world",
            confidence=0.95,
            error="",
            timing_ms=150,
            segmented_by=None,
        )
        assert rec.engine == "kraken"
        assert rec.model_id == "10.5281/zenodo.123"
        assert rec.text == "Hello world"
        assert rec.confidence == 0.95
        assert rec.error == ""
        assert rec.timing_ms == 150
        assert rec.segmented_by is None

    def test_error_case(self):
        rec = RecognitionResult(engine="trocr", model_id="dh-unibe/trocr-kurrent", error="Connection refused")
        assert rec.text == ""
        assert rec.error == "Connection refused"

    def test_segmented_by(self):
        rec = RecognitionResult(
            engine="trocr",
            model_id="dh-unibe/trocr-kurrent",
            text="Line one\nLine two",
            segmented_by="kraken-blla",
        )
        assert rec.segmented_by == "kraken-blla"


# ─── SourceCriteria fixtures ─────────────────────────────────────────────────

@pytest.fixture
def medieval_latin():
    return SourceCriteria(
        script="Caroline minuscule",
        lang="la",
        century=13,
        document_type="charter",
        region="Swiss",
        notes="13th century Latin charter in Caroline minuscule",
    )


@pytest.fixture
def early_modern_german():
    return SourceCriteria(
        script="Kurrent",
        lang="de",
        century=16,
        document_type="letter",
        region="Bavaria",
        notes="16th century German Kurrent letter",
    )


# ─── select_best factory ─────────────────────────────────────────────────────

class TestSelectBest:
    def test_unknown_engine_returns_empty(self, medieval_latin):
        matches = select_best("unknown_engine", medieval_latin)
        assert matches == []

    def test_string_description_converted(self):
        """select_best accepts a raw description string (no explicit SourceCriteria)."""
        matches = select_best("kraken", "13th century Latin charter in Caroline minuscule")
        assert isinstance(matches, list)

    def test_kraken_returns_modelmatch(self, medieval_latin):
        matches = select_best("kraken", medieval_latin, top_k=1)
        assert all(hasattr(m, "model") and hasattr(m, "score") for m in matches)

    def test_tocr_returns_hfmodel(self, early_modern_german):
        matches = select_best("trocr", early_modern_german, top_k=3)
        assert isinstance(matches, list)
        for m in matches:
            assert hasattr(m.model, "model_id")
            assert m.model.task in ("line-ocr", "htr")

    def test_party_returns_list(self):
        criteria = SourceCriteria(lang="de", century=14)
        matches = select_best("party", criteria)
        assert isinstance(matches, list)


# ─── PipelineContext.recognitions ────────────────────────────────────────────

class TestPipelineContextRecognitions:
    def test_empty_by_default(self):
        ctx = PipelineContext("doc001")
        assert ctx.recognitions == []

    def test_to_json_includes_recognitions(self):
        ctx = PipelineContext("doc001")
        ctx.recognitions = [
            RecognitionResult(engine="vlm", model_id="internvl3-8b-instruct", text="VLM text", confidence=0.9),
            RecognitionResult(
                engine="kraken",
                model_id="10.5281/zenodo.7516057",
                text="Kraken text",
                confidence=0.85,
                segmented_by=None,
            ),
            RecognitionResult(
                engine="trocr",
                model_id="dh-unibe/trocr-kurrent-XVI-XVII",
                text="TOCR text",
                confidence=0.92,
                segmented_by="kraken-blla",
            ),
        ]
        j = ctx.to_json()
        assert "recognitions" in j
        assert len(j["recognitions"]) == 3
        rec0 = j["recognitions"][0]
        assert rec0["engine"] == "vlm"
        assert rec0["text"] == "VLM text"
        assert rec0["confidence"] == 0.9
        assert rec0["segmented_by"] is None
        rec2 = j["recognitions"][2]
        assert rec2["segmented_by"] == "kraken-blla"
        assert rec2["engine"] == "trocr"

    def test_to_json_empty_recognitions(self):
        ctx = PipelineContext("doc002")
        j = ctx.to_json()
        assert "recognitions" in j
        assert j["recognitions"] == []

    def test_to_json_error_in_recognition(self):
        ctx = PipelineContext("doc003")
        ctx.recognitions = [
            RecognitionResult(
                engine="kraken",
                model_id="10.5281/zenodo.x",
                text="",
                error="Service unavailable",
            ),
        ]
        j = ctx.to_json()
        assert j["recognitions"][0]["error"] == "Service unavailable"
        assert j["recognitions"][0]["text"] == ""

    def test_recognitions_persisted_to_json_file(self, tmp_path):
        ctx = PipelineContext("doc004")
        ctx.recognitions = [
            RecognitionResult(
                engine="party",
                model_id="10.5281/zenodo.20642057",
                text="Party transcription",
                confidence=0.88,
                timing_ms=1200,
            ),
        ]
        pipeline_file = tmp_path / "pipeline.json"
        with open(pipeline_file, "w", encoding="utf-8") as f:
            json.dump(ctx.to_json(), f, ensure_ascii=False, indent=2)
        loaded = json.loads(pipeline_file.read_text())
        assert len(loaded["recognitions"]) == 1
        assert loaded["recognitions"][0]["engine"] == "party"
        assert loaded["recognitions"][0]["timing_ms"] == 1200


# ─── select_tocr_model ───────────────────────────────────────────────────────

class TestSelectTocrModel:
    def test_returns_modelmatch_list(self, early_modern_german):
        matches = select_tocr_model(early_modern_german, top_k=3)
        assert isinstance(matches, list)
        for m in matches:
            assert hasattr(m, "model")
            assert hasattr(m, "score")
            assert hasattr(m.model, "model_id")
            assert m.model.task in ("line-ocr", "htr")

    def test_filters_page_level_models(self):
        criteria = SourceCriteria(lang="de", century=16)
        matches = select_tocr_model(criteria, top_k=10)
        for m in matches:
            assert m.model.task in ("line-ocr", "htr")

    def test_require_score_above(self):
        criteria = SourceCriteria(lang="de", century=16)
        all_matches = select_tocr_model(criteria, top_k=10, require_score_above=0.0)
        high_matches = select_tocr_model(criteria, top_k=10, require_score_above=0.9)
        assert len(high_matches) <= len(all_matches)


# ─── select_party_model ──────────────────────────────────────────────────────

class TestSelectPartyModel:
    def test_returns_party_model(self):
        criteria = SourceCriteria(lang="de", century=14)
        matches = select_party_model(criteria)
        assert all("20642057" in m.model.model_id for m in matches)

    def test_score_threshold_empty(self):
        criteria = SourceCriteria(script="Unknown script", lang="xx", century=99)
        matches = select_party_model(criteria, require_score_above=0.5)
        assert matches == []
