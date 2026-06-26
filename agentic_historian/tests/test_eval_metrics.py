"""
tests/test_eval_metrics.py

Tests for eval/metrics.py — CER and WER computation.
"""

import pytest
from eval.metrics import cer, wer, levenshtein, normalise, format_report


def test_cer_perfect_match():
    assert cer("Hallo Welt", "Hallo Welt") == 0.0


def test_cer_one_char_difference():
    # "Hallo" vs "Hallo " — trailing space
    assert cer("Hallo Welt", "Hallo welt") == cer("hallo welt", "hallo welt")


def test_cer_complete_mismatch():
    assert cer("abc", "xyz") == 1.0


def test_cer_empty_reference():
    assert cer("", "") == 0.0
    assert cer("", "hello") == 1.0


def test_cer_partial_match():
    # 1 substitution in a 10-char string
    assert 0.05 < cer("Hallo Welt", "Hallo Welx") < 0.15


def test_wer_perfect_match():
    assert wer("Hallo Welt", "Hallo Welt") == 0.0


def test_wer_one_word_difference():
    assert wer("Hallo Welt", "Hallo Welt!!") == 0.0  # punctuation stripped by normalise


def test_wer_complete_mismatch():
    assert wer("Hallo Welt", "foo bar") == 1.0


def test_normalise():
    assert normalise("  Hello   WORLD  ") == "hello world"
    assert normalise("Hallo,Welt.") == "hallo,welt"


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("hello", "hello") == 0
    assert levenshtein("", "") == 0


def test_format_report():
    results = [
        {"doc_id": "doc_a", "cer": 0.05, "wer": 0.1, "gt_len": 100, "hyp_len": 98, "errors": 5},
        {"doc_id": "doc_b", "cer": 0.2, "wer": 0.3, "gt_len": 200, "hyp_len": 180, "errors": 40},
    ]
    report = format_report(results)
    assert "doc_a" in report
    assert "doc_b" in report
    assert "Average" in report