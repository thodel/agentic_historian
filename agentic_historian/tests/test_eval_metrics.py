"""tests/test_eval_metrics.py — Tests for eval/metrics.py"""

from eval.metrics import cer, wer, levenshtein, normalise, format_report


def test_cer_perfect_match():
    assert cer("Hallo Welt", "Hallo Welt") == 0.0


def test_cer_complete_mismatch():
    assert cer("abc", "xyz") == 1.0


def test_cer_empty_reference():
    assert cer("", "") == 0.0
    assert cer("", "hello") == 1.0


def test_cer_partial():
    # ~1 substitution in 10 chars
    d = levenshtein("Hallo Welt", "Hallo Welx")
    assert 0.05 < d / 10 < 0.15


def test_wer_perfect_match():
    assert wer("Hallo Welt hier", "Hallo Welt hier") == 0.0


def test_wer_complete_mismatch():
    assert wer("Hallo Welt", "foo bar baz") == 1.0


def test_wer_one_word_diff():
    assert 0.2 < wer("Hallo Welt gut", "Hallo Welt schlecht") < 0.4


def test_normalise():
    assert normalise("  Hello   WORLD  ") == "hello world"
    assert normalise("Hallo,Welt.") == "hallo,welt"


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("hello", "hello") == 0
    assert levenshtein("", "") == 0


def test_format_report():
    results = [
        {"doc_id": "doc_a", "cer": 0.05, "wer": 0.10, "gt_len": 100, "hyp_len": 98, "errors": 5},
        {"doc_id": "doc_b", "cer": 0.20, "wer": 0.30, "gt_len": 200, "hyp_len": 180, "errors": 40},
    ]
    report = format_report(results)
    assert "doc_a" in report
    assert "doc_b" in report
    assert "Average" in report
    assert "0.125" in report  # avg CER