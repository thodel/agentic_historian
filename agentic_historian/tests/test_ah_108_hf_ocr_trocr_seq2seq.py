"""
test_ah_108_hf_ocr_trocr_seq2seq.py - issue #108.
[P1] HF OCR path uses AutoModelForCTC but TrOCR models are seq2seq.
Acceptance: loader selects the correct class for a TrOCR model id (unit test, mocked).
"""

from __future__ import annotations
import sys
from pathlib import Path
from unittest.mock import MagicMock

# ── Set up mock modules BEFORE any test code imports agent_a ──────────────────
_mock_torch = MagicMock()
_mock_torch.cuda.is_available.return_value = False
_mock_torch.bfloat16 = float

# torch.no_grad must be a proper context manager
class _NoGradCtx:
    def __enter__(self): return None
    def __exit__(self, *args): return False
_mock_torch.no_grad = MagicMock(return_value=_NoGradCtx())

_mock_transformers = MagicMock(name="transformers")
_mock_transformers.AutoProcessor.from_pretrained = MagicMock(
    name="AutoProcessor.from_pretrained"
)
_mock_transformers.AutoModelForVision2Seq.from_pretrained = MagicMock(
    name="AutoModelForVision2Seq.from_pretrained"
)
_mock_transformers.AutoModelForCTC.from_pretrained = MagicMock(
    name="AutoModelForCTC.from_pretrained"
)

_mock_pil = MagicMock(name="PIL")
_mock_pil_image = MagicMock(name="PIL.Image")
_mock_pil.Image.open = MagicMock(name="PIL.Image.open")
_mock_img_instance = MagicMock()
_mock_img_instance.convert.return_value = MagicMock()
_mock_pil.Image.open.return_value = _mock_img_instance

sys.modules["torch"] = _mock_torch
sys.modules["torch.cuda"] = MagicMock()
sys.modules["transformers"] = _mock_transformers
sys.modules["PIL"] = _mock_pil
sys.modules["PIL.Image"] = _mock_pil_image

import pytest
from agent_a.models import HFModel
from agent_a.models import hf_model_for_lang as _real_hf_model_for_lang


# ── Mock instances (module-level so they persist across tests) ─────────────────

# model.to(device) must return itself so the model is still usable after .to()
_mock_model_instance = MagicMock(name="model_instance")
_mock_model_instance.generate.return_value = [[71, 72, 73]]
_mock_model_instance.to.return_value = _mock_model_instance   # key: chainable

_mock_transformers.AutoModelForVision2Seq.from_pretrained.return_value = _mock_model_instance

# processor must return a dict-like that survives **unpacking in generate(**inputs)
class _Inputs(dict):
    def to(self, _device): return self

_mock_processor_instance = MagicMock(name="processor_instance")
_mock_processor_instance.batch_decode.return_value = ["Transcribed text from TrOCR model"]
_mock_processor_instance.return_value = _Inputs(pixel_values="mock-tensor")

_mock_transformers.AutoProcessor.from_pretrained.return_value = _mock_processor_instance


def _mock_hf_model(lang: str, require_line: bool = False):
    return HFModel(
        model_id="test/mock-trocr",
        name="Mock TrOCR",
        lang=lang,
        requires_line_images=False,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAcceptance:
    """
    Acceptance: _run_hf_ocr uses AutoModelForVision2Seq (seq2seq decoder),
    NOT AutoModelForCTC.  Also verify .generate() (seq2seq decode) is called.
    """

    def test_vision2seq_is_called_not_ctc(self):
        from agent_a import dual_pipeline, models

        _mock_transformers.AutoModelForVision2Seq.from_pretrained.reset_mock()
        _mock_transformers.AutoModelForCTC.from_pretrained.reset_mock()

        models.hf_model_for_lang = _mock_hf_model
        try:
            text, model_id = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")

            assert _mock_transformers.AutoModelForVision2Seq.from_pretrained.called, \
                "AutoModelForVision2Seq was NOT called"
            assert not _mock_transformers.AutoModelForCTC.from_pretrained.called, \
                "AutoModelForCTC WAS called - wrong class for seq2seq model"
            assert text == "Transcribed text from TrOCR model"
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang

    def test_generate_is_called_on_model(self):
        from agent_a import dual_pipeline, models

        _mock_model_instance.generate.reset_mock()

        models.hf_model_for_lang = _mock_hf_model
        try:
            text, _ = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")

            _mock_model_instance.generate.assert_called()
            assert text == "Transcribed text from TrOCR model"
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang


class TestErrorPaths:
    def test_no_hf_model_returns_error(self):
        from agent_a import dual_pipeline, models

        models.hf_model_for_lang = lambda lang: None
        try:
            text, err = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="xy")
            assert text == "" and "No HF model" in err
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang

    def test_import_error_propagates_as_error(self):
        from agent_a import dual_pipeline

        _mock_transformers.AutoProcessor.from_pretrained.side_effect = \
            ImportError("No module named 'transformers'")
        try:
            text, err = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")
            assert text == "" and "Missing dependency" in err
        finally:
            _mock_transformers.AutoProcessor.from_pretrained.side_effect = None
            _mock_transformers.AutoProcessor.from_pretrained.return_value = _mock_processor_instance

    def test_model_load_error_returns_error_string(self):
        from agent_a import dual_pipeline, models

        _mock_transformers.AutoModelForVision2Seq.from_pretrained.side_effect = \
            OSError("Model not found")
        models.hf_model_for_lang = _mock_hf_model
        try:
            text, err = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")
            assert text == "" and "Model not found" in err
        finally:
            _mock_transformers.AutoModelForVision2Seq.from_pretrained.side_effect = None
            _mock_transformers.AutoModelForVision2Seq.from_pretrained.return_value = _mock_model_instance
            models.hf_model_for_lang = _real_hf_model_for_lang

    def test_lang_without_hf_model_returns_error(self):
        from agent_a import dual_pipeline, models

        models.hf_model_for_lang = lambda lang: None
        try:
            text, err = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="xyz")
            assert text == "" and "No HF model" in err
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang


class TestRequiresLineImagesEarlyReturn:
    """Real models require line images. Verify the early-return path."""

    def test_returns_line_image_error(self):
        from agent_a import dual_pipeline, models

        models.hf_model_for_lang = lambda lang: HFModel(
            model_id="real/model", name="Real", lang=lang,
            requires_line_images=True,
        )
        try:
            text, err = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")
            assert text == "" and "line-image mode requires kraken pre-segmentation" in err
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang


class TestEndToEnd:
    def test_full_path_returns_transcription(self):
        from agent_a import dual_pipeline, models

        _mock_transformers.AutoModelForVision2Seq.from_pretrained.reset_mock()

        models.hf_model_for_lang = _mock_hf_model
        try:
            text, model_id = dual_pipeline._run_hf_ocr(Path("/fake/page.tif"), lang="la")
            assert text == "Transcribed text from TrOCR model"
            assert "mock-trocr" in model_id
        finally:
            models.hf_model_for_lang = _real_hf_model_for_lang


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
