"""
tests/test_gpustack_client.py

Tests for the GPUStack client (OpenAI-compatible).
"""

import pytest
from unittest.mock import patch, MagicMock
from openai import APIConnectionError

import utils.gpustack_client as gs


class TestChatText:
    """chat_text() wrapper tests."""

    @patch.object(gs, "get_client")
    def test_chat_text_returns_content_normal(self, mock_get_client):
        """Normal chat returns the response content."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hallo Welt"
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = gs.chat_text("Hallo", max_tokens=100)
        assert result == "Hallo Welt"

    @patch.object(gs, "get_client")
    def test_chat_text_reasoning_model_null_content(self, mock_get_client):
        """Empty content due to length is retried; if still empty raise EmptyCompletion.

        Current main raises EmptyCompletion on null content (not returning "").
        We test graceful handling by mocking the retry to still return empty,
        which should raise EmptyCompletion.
        """
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].finish_reason = "length"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        # The client retries once with doubled budget; after that if still empty
        # it raises EmptyCompletion. We patch _create to simulate this.
        with patch.object(gs, "_create", return_value=(None, "length")):
            with pytest.raises(gs.EmptyCompletion, match="returned no content"):
                gs.chat_text("Hallo", max_tokens=10)

    @patch.object(gs, "get_client")
    def test_chat_text_reasoning_model_with_content_after_reasoning(self, mock_get_client):
        """Reasoning model that eventually returns content succeeds."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Final answer"
        mock_response.choices[0].finish_reason = "stop"
        mock_client.chat.completions.create.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = gs.chat_text("What is 2+2?", max_tokens=100)
        assert result == "Final answer"


class TestImageUrl:
    """image_url() path tests."""

    def test_image_url_http_url_pass_through(self):
        """HTTP/HTTPS URLs are returned unchanged."""
        url = "https://example.com/image.jpg"
        assert gs.image_url(url) == url

    def test_image_url_local_file(self):
        """Local file path is base64-encoded as a data URI."""
        result = gs.image_url("/tmp/test.jpg")
        assert result.startswith("data:image/jpeg;base64,")
        # Verify it's valid base64 (nontrivial content since file is random bytes)
        b64_data = result.split(",")[1]
        import base64
        decoded = base64.b64decode(b64_data)
        assert len(decoded) == 100  # our test file is 100 bytes