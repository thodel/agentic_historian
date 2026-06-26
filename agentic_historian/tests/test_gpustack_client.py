"""
tests/test_gpustack_client.py

Tests for gpustack_client reasoning-model handling.
gpt-oss-120b emits reasoning_content FIRST, then content.
The client must handle null content correctly.
"""

import pytest
from unittest.mock import patch, MagicMock

from utils import gpustack_client as gs


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = MagicMock()
        self.message.content = content
        self.finish_reason = finish_reason


class FakeUsage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class FakeResponse:
    def __init__(self, content, finish_reason="stop", prompt_tokens=100, completion_tokens=50):
        self.choices = [FakeChoice(content, finish_reason)]
        self.usage = FakeUsage(prompt_tokens, completion_tokens)


@patch.object(gs, "get_client")
def test_chat_text_returns_content_normal(mock_get_client):
    """Normal response: content is present."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = FakeResponse("Hallo Welt")
    mock_get_client.return_value = mock_client

    result = gs.chat_text("Hallo")
    assert result == "Hallo Welt"
    mock_client.chat.completions.create.assert_called_once()


@patch.object(gs, "get_client")
def test_chat_text_reasoning_model_null_content(mock_get_client):
    """gpt-oss-120b can return null content while reasonining — should return empty string gracefully."""
    mock_client = MagicMock()
    # Simulates the reasoning model: finish_reason=length, content=null
    mock_client.chat.completions.create.return_value = FakeResponse(None, "length")
    mock_get_client.return_value = mock_client

    # Should not raise — returns empty string
    result = gs.chat_text("Hallo", max_tokens=10)
    assert result == ""


@patch.object(gs, "get_client")
def test_chat_text_reasoning_model_with_content_after_reasoning(mock_get_client):
    """gpt-oss-120b reasoning model: when content is present, return it."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = FakeResponse(
        '{"ok": true}',
        prompt_tokens=200,
        completion_tokens=30,
    )
    mock_get_client.return_value = mock_client

    result = gs.chat_text("Give me JSON", max_tokens=500)
    assert result == '{"ok": true}'


@patch.object(gs, "get_client")
def test_image_url_local_file(mock_get_client):
    """Local file path should become base64 data URI."""
    result = gs.image_url("/tmp/test.jpg")
    assert result.startswith("data:image/jpeg;base64,")


@patch.object(gs, "get_client")
def test_image_url_http_url_pass_through(mock_get_client):
    """HTTP(S) URLs should pass through unchanged."""
    result = gs.image_url("https://example.com/image.jpg")
    assert result == "https://example.com/image.jpg"