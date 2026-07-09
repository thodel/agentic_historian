"""
Tests for #235 (P2-2): TrOCR line-model segmentation path.

Run from repo root:
    .venv/bin/python -m pytest tests/test_ah_235_tocr_segmentation.py -v
"""

import sys, os, tempfile
from pathlib import Path

PKG = str(Path(__file__).resolve().parents[1])
if PKG not in sys.path:
    sys.path.insert(0, PKG)

import numpy as np
from PIL import Image as PILImage
from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_temp_image(width=300, height=100) -> Path:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    PILImage.fromarray(arr).save(path)
    return Path(path)


def _two_lines():
    """Two horizontal baseline polygons at y≈10 and y≈50."""
    return [
        {"baseline": [[5, 10], [295, 10]]},
        {"baseline": [[5, 50], [295, 50]]},
    ]


# ── mock factory ──────────────────────────────────────────────────────────────

def _make_hf_mocks(decode_return=None):
    """
    Build processor + model mocks for HF OCR path.

    decode_return: list of strings that processor.batch_decode() should return.
                   One entry per generate() call. Defaults to two distinct strings.
    """
    decode_return = decode_return or ["line one text", "line two text"]

    # Token IDs per generate() call.  generate() must return a list (e.g. [0])
    # so that batch_decode sees args[0][0] == 0 as the token index into decode_return.
    per_call_tokens = [[[i]] for i in range(len(decode_return))]

    # Processor instance: AutoProcessor.from_pretrained(model_id) → this
    # batch_decode is called once per generate() call; side_effect makes each
    # call return just the text for that line (index 0 picks the only element).
    # Processor instance: AutoProcessor.from_pretrained(model_id) → this
    proc_instance = MagicMock()
    # side_effect is called once per generate() output; extract the token ID
    # from the nested list [[token_id]] and return the matching decode string.
    proc_instance.batch_decode.side_effect = lambda args, **kwargs: [decode_return[args[0][0]]]
    tensors_instance = MagicMock()
    tensors_instance.to.return_value = tensors_instance
    proc_instance.return_value = tensors_instance  # processor(images=...) returns tensors
    tensors_instance = MagicMock()
    tensors_instance.to.return_value = tensors_instance
    proc_instance.return_value = tensors_instance  # processor(images=...) returns tensors

    # Model instance: AutoModelCls.from_pretrained(model_id) → this
    model_instance = MagicMock()
    model_instance.generate.side_effect = per_call_tokens
    model_instance.to.return_value = model_instance

    # Auto-classes whose .from_pretrained() returns the above instances
    mock_proc_cls = MagicMock()
    mock_proc_cls.from_pretrained.return_value = proc_instance

    mock_model_cls = MagicMock()
    mock_model_cls.from_pretrained.return_value = model_instance

    return mock_proc_cls, mock_model_cls, proc_instance, model_instance


# ── test: requires_line_images=True → kraken segment called ──────────────────

def test_requires_line_images_calls_kraken_segment():
    """When ``requires_line_images=True`` kraken.segment is called per page (#235)."""
    from agent_a import dual_pipeline as dp
    from agent_a.models import HFModel

    fake_model = HFModel(
        name="TrOCR-test",
        model_id="dh-unibe/tcroftest",
        lang="la",
        requires_line_images=True,
        notes="",
    )

    img = _make_temp_image()

    mock_proc_cls, mock_model_cls, _p, _m = _make_hf_mocks(
        decode_return=["line one text", "line two text"],
    )

    mock_client = MagicMock()
    mock_client.segment.return_value = {"lines": _two_lines()}
    mock_kraken = MagicMock()
    mock_kraken.__enter__.return_value = mock_client
    mock_kraken.__exit__.return_value = None

    with (
        patch.object(dp, "_ensure_transformers_ready"),
        patch.object(dp, "_AutoProcessor", mock_proc_cls),
        patch.object(dp, "_AutoModelCls", mock_model_cls),
        patch("agent_a.models.hf_model_for_lang", return_value=fake_model),
        patch("agent_a.dual_pipeline.KrakenHTTPClient", return_value=mock_kraken),
        patch("torch.cuda.is_available", return_value=False),
    ):
        text, engine = dp._run_hf_ocr(img, lang="la")

    mock_client.segment.assert_called_once()
    assert "line one text" in text
    assert "line two text" in text
    assert engine == "dh-unibe/tcroftest"

    os.unlink(img)


# ── test: requires_line_images=False → kraken NOT called ─────────────────────

def test_requires_line_images_false_skips_segment():
    """When ``requires_line_images=False`` kraken segment is NOT called (#235)."""
    from agent_a import dual_pipeline as dp
    from agent_a.models import HFModel

    fake_model = HFModel(
        name="LightOn-test",
        model_id="wjbmattingly/LightOnOCR-2-1B",
        lang="la",
        requires_line_images=False,
        notes="",
    )

    img = _make_temp_image()

    mock_proc_cls, mock_model_cls, _p, _m = _make_hf_mocks(
        decode_return=["full-page result"],
    )

    with (
        patch.object(dp, "_ensure_transformers_ready"),
        patch.object(dp, "_AutoProcessor", mock_proc_cls),
        patch.object(dp, "_AutoModelCls", mock_model_cls),
        patch("agent_a.models.hf_model_for_lang", return_value=fake_model),
        patch("torch.cuda.is_available", return_value=False),
        patch("agent_a.dual_pipeline.KrakenHTTPClient") as mk,
    ):
        text, engine = dp._run_hf_ocr(img, lang="la")

    mk.return_value.__enter__.assert_not_called()
    assert "full-page result" in text
    assert engine == "wjbmattingly/LightOnOCR-2-1B"

    os.unlink(img)


# ── test: KrakenClientError → error tuple ─────────────────────────────────────

def test_segmentation_failure_returns_error():
    """KrakenClientError from kraken → error string, not raised (#235)."""
    from agent_a import dual_pipeline as dp
    from agent_a.kraken_client import KrakenClientError
    from agent_a.models import HFModel

    fake_model = HFModel(
        name="TrOCR-test",
        model_id="dh-unibe/tcroftest",
        lang="la",
        requires_line_images=True,
        notes="",
    )

    img = _make_temp_image()

    mock_proc_cls, mock_model_cls, _p, _m = _make_hf_mocks()

    mock_client = MagicMock()
    mock_client.segment.side_effect = KrakenClientError("connection refused")
    mock_kraken = MagicMock()
    mock_kraken.__enter__.return_value = mock_client
    mock_kraken.__exit__.return_value = None

    with (
        patch.object(dp, "_ensure_transformers_ready"),
        patch.object(dp, "_AutoProcessor", mock_proc_cls),
        patch.object(dp, "_AutoModelCls", mock_model_cls),
        patch("agent_a.models.hf_model_for_lang", return_value=fake_model),
        patch("agent_a.dual_pipeline.KrakenHTTPClient", return_value=mock_kraken),
        patch("torch.cuda.is_available", return_value=False),
    ):
        text, msg = dp._run_hf_ocr(img, lang="la")

    assert text == ""
    assert "kraken segmentation failed" in msg.lower()

    os.unlink(img)


# ── test: empty lines list → error tuple ─────────────────────────────────────

def test_empty_lines_returns_error():
    """Empty lines list from kraken → error string (#235)."""
    from agent_a import dual_pipeline as dp
    from agent_a.models import HFModel

    fake_model = HFModel(
        name="TrOCR-test",
        model_id="dh-unibe/tcroftest",
        lang="la",
        requires_line_images=True,
        notes="",
    )

    img = _make_temp_image()

    mock_proc_cls, mock_model_cls, _p, _m = _make_hf_mocks()

    mock_client = MagicMock()
    mock_client.segment.return_value = {"lines": []}
    mock_kraken = MagicMock()
    mock_kraken.__enter__.return_value = mock_client
    mock_kraken.__exit__.return_value = None

    with (
        patch.object(dp, "_ensure_transformers_ready"),
        patch.object(dp, "_AutoProcessor", mock_proc_cls),
        patch.object(dp, "_AutoModelCls", mock_model_cls),
        patch("agent_a.models.hf_model_for_lang", return_value=fake_model),
        patch("agent_a.dual_pipeline.KrakenHTTPClient", return_value=mock_kraken),
        patch("torch.cuda.is_available", return_value=False),
    ):
        text, msg = dp._run_hf_ocr(img, lang="la")

    assert text == ""
    assert "no lines found" in msg.lower()

    os.unlink(img)


# ── test: no HF model configured → error tuple ────────────────────────────────

def test_no_hf_model_returns_error():
    """No model for lang → error tuple (#235)."""
    from agent_a import dual_pipeline as dp

    with patch("agent_a.models.hf_model_for_lang", return_value=None):
        text, msg = dp._run_hf_ocr(Path("/nonexistent.jpg"), lang="la")

    assert text == ""
    assert "no hf model" in msg.lower()