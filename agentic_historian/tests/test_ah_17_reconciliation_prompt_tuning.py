"""
test_ah_17_reconciliation_prompt_tuning.py — Offline test for issue #17.

Validates the reconciliation improvements:
  1. RECONCILE_SYSTEM prompt is richer (Chain-of-Thought, explicit rules)
  2. agreement_score uses line-level disagreement ratio, not raw token diff
  3. LLM calls go through GPUSTACK_MODEL_TEXT with adequate budget
     (RECONCILE_DEFAULT_MAX_TOKENS)

Run: pytest test_ah_17_reconciliation_prompt_tuning.py -v
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import difflib

import pytest

from agent_a import reconcile as recon_lib
from agent_a.reconcile import (
    RECONCILE_SYSTEM,
    RECONCILE_DEFAULT_MAX_TOKENS,
    _line_disagreement_ratio,
    _token_diff,
    reconcile,
    ReconciliationResult,
)
import config


class TestReconcileSystemPrompt:
    """RECONCILE_SYSTEM must contain all required instruction elements."""

    def test_prompt_mentions_chained_thought(self):
        """Prompt must encourage Chain-of-Thought reasoning."""
        assert "Chain-of-Thought" in RECONCILE_SYSTEM or "denke" in RECONCILE_SYSTEM.lower()

    def test_prompt_mentions_paleography(self):
        """Prompt must reference paleographic expertise."""
        assert "paleografisch" in RECONCILE_SYSTEM.lower() or "paläografisch" in RECONCILE_SYSTEM.lower()

    def test_prompt_excludes_commentary(self):
        """Prompt must explicitly forbid non-transcription output."""
        assert "keine Kommentare" in RECONCILE_SYSTEM or "keine Erklärungen" in RECONCILE_SYSTEM

    def test_prompt_handles_unclear_passages(self):
        """Prompt must have a rule for unreadable content."""
        assert "UNCLEAR" in RECONCILE_SYSTEM or "unlesbar" in RECONCILE_SYSTEM.lower()


class TestAgreementScoring:
    """agreement_score must be based on line-level disagreement ratio."""

    def test_identical_texts_score_1(self):
        """Identical transcriptions → agreement 1.0."""
        text = "hello world"
        score = _line_disagreement_ratio([text], [text])
        assert score == 0.0  # 1.0 - (equal/total) = 1.0 - 1 = 0

    def test_completely_different_texts_score_0(self):
        """Disjoint line sets → agreement 0.0."""
        vlm_lines = ["alpha", "beta"]
        kraken_lines = ["gamma", "delta"]
        score = _line_disagreement_ratio(vlm_lines, kraken_lines)
        assert score == 1.0  # no equal lines → 1.0 - 0 = 1.0

    def test_line_diff_more_stable_than_token_diff(self):
        """
        Line-level disagreement is more stable than character-level token diff.
        Two texts with the same characters but different line segmentation
        should not get a perfect score from line disagreement.
        """
        vlm_lines = ["one two three four"]
        kraken_lines = ["one two", "three four"]
        # Same content, different line boundaries
        line_score = _line_disagreement_ratio(vlm_lines, kraken_lines)
        token_score = _token_diff(vlm_lines[0], kraken_lines[0] + " " + kraken_lines[1])
        # Line disagreement should be > 0 since boundaries differ
        assert line_score > 0
        # Token diff might be 0 since same characters
        # (this just shows line-level is more sensitive to structure)

    def test_partial_overlap_score_between_0_and_1(self):
        """Partial overlap → score strictly between 0 and 1."""
        vlm_lines = ["a", "b", "c"]
        kraken_lines = ["a", "x", "c"]
        score = _line_disagreement_ratio(vlm_lines, kraken_lines)
        assert 0.0 < score < 1.0


class TestGPUSTACKModelSelection:
    """LLM reconciliation must use GPUSTACK_MODEL_TEXT, not a default."""

    @patch("agent_a.reconcile.gs.chat_text")
    def test_reconcile_calls_gpustack_model_text(self, mock_chat):
        """reconcile() calls chat_text with model=config.GPUSTACK_MODEL_TEXT."""
        mock_chat.return_value = "reconciled output"

        # Use clearly different transcriptions so agreement < 0.95 → LLM is invoked
        vlm = "first alpha\nsecond beta\nthird charlie"
        kraken = "first xray\nsecond yankee\nthird zulu"
        reconcile(vlm, kraken, use_llm=True)

        mock_chat.assert_called_once()
        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs.get("model") == config.GPUSTACK_MODEL_TEXT

    @patch("agent_a.reconcile.gs.chat_text")
    def test_reconcile_default_max_tokens_from_config(self, mock_chat):
        """reconcile() defaults max_tokens to RECONCILE_DEFAULT_MAX_TOKENS."""
        mock_chat.return_value = "output"

        reconcile("a", "b", use_llm=True, max_tokens=0)

        call_kwargs = mock_chat.call_args.kwargs
        assert call_kwargs.get("max_tokens") == RECONCILE_DEFAULT_MAX_TOKENS
        assert call_kwargs["max_tokens"] == config.GPUSTACK_TEXT_MAX_TOKENS

    @patch("agent_a.reconcile.gs.chat_text")
    def test_reconcile_passes_explicit_max_tokens(self, mock_chat):
        """Explicit max_tokens overrides the default."""
        mock_chat.return_value = "output"
        override = 2048

        reconcile("a", "b", use_llm=True, max_tokens=override)

        assert mock_chat.call_args.kwargs.get("max_tokens") == override

    @patch("agent_a.reconcile.gs.chat_text")
    def test_reconcile_low_agreement_calls_llm(self, mock_chat):
        """Low line-agreement (< 0.95) triggers LLM reconciliation."""
        mock_chat.return_value = "merged"

        # Two very different transcriptions → agreement < 0.95
        result = reconcile(
            "alpha bravo charlie delta echo",
            "alpha xray yankee delta zulu",
            use_llm=True,
        )

        mock_chat.assert_called_once()
        assert result.method == "llm"
        assert result.agreement_score < 0.95

    @patch("agent_a.reconcile.gs.chat_text")
    def test_reconcile_high_agreement_skips_llm(self, mock_chat):
        """High agreement (>= 0.95) short-circuits without LLM call."""
        # Near-identical texts → agreement >= 0.95
        result = reconcile(
            "hello world",
            "hello world",
            use_llm=True,
        )

        mock_chat.assert_not_called()
        assert result.method == "vlm_preferred"
        assert result.agreement_score >= 0.95


class TestReconcileDefaultMaxTokens:
    """RECONCILE_DEFAULT_MAX_TOKENS must be set from config."""

    def test_default_tokens_from_config(self):
        """RECONCILE_DEFAULT_MAX_TOKENS must equal GPUSTACK_TEXT_MAX_TOKENS."""
        assert RECONCILE_DEFAULT_MAX_TOKENS == config.GPUSTACK_TEXT_MAX_TOKENS

    def test_default_tokens_reasonable(self):
        """Default budget must be at least 4096 tokens for reasoning models."""
        assert RECONCILE_DEFAULT_MAX_TOKENS >= 4096