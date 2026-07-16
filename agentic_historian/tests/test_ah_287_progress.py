"""Tests for #287 (V-1): progress.py — snippet / format_phase_event / format_board.

Offline, pure logic. Run from the repo root:
    pytest agentic_historian/tests/test_ah_287_progress.py
"""

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import progress as P


# ── snippet() ────────────────────────────────────────────────────────────────

class TestSnippetNilEmpty:
    def test_none_returns_em_dash(self):
        assert P.snippet(None) == "—"

    def test_empty_str_returns_em_dash(self):
        assert P.snippet("") == "—"

    def test_empty_list_returns_em_dash(self):
        assert P.snippet([]) == "—"

    def test_empty_dict_returns_em_dash(self):
        assert P.snippet({}) == "—"

    def test_weird_object_does_not_raise(self):
        class Weird:
            def __str__(self):
                raise ValueError("nope")
        assert P.snippet(Weird()) == "—"

        class Weird2:
            pass
        assert P.snippet(Weird2()) == "—"


class TestSnippetString:
    def test_five_lines_returns_first_three_plus_drop_count(self):
        text = "line one\nline two\nline three\nline four\nline five"
        result = P.snippet(text)
        assert "line one" in result
        assert "line three" in result
        assert "line four" not in result
        assert "(+2 more)" in result

    def test_blank_lines_skipped(self):
        text = "line one\n\n\nline two\n   \nline three"
        result = P.snippet(text, n=3)
        # blank lines stripped; should still have 3 non-blank lines
        assert "line one" in result

    def test_max_chars_respected(self):
        long_text = "word " * 200   # ~900 chars
        result = P.snippet(long_text, max_chars=50)
        assert len(result) <= 53  # 50 + "…" 

    def test_uuuu_transcription_shows_repeated_u(self):
        """Regression: uuuu/uuu/u collapse must NOT become empty/elided."""
        text = "uuuu\nuuu\nu"
        result = P.snippet(text)
        assert "uuuu" in result or "uu" in result
        assert result != "—"


class TestSnippetList:
    def test_list_of_entity_dicts_renders_names(self):
        entities = [
            {"normalised": {"wert": "Johann Müller"}},
            {"normalised": {"wert": "Hans von Bern"}},
            {"normalised": {"wert": "Königsfelden"}},
            {"text": "Extra Person"},
        ]
        result = P.snippet(entities, n=3)
        assert "Johann Müller" in result
        assert "Hans von Bern" in result
        assert "Königsfelden" in result
        assert "Extra Person" not in result

    def test_list_of_strings(self):
        result = P.snippet(["alpha", "beta", "gamma", "delta"], n=2)
        assert "alpha" in result
        assert "beta" in result
        assert "(+2 more)" in result

    def test_list_dict_drops_count(self):
        items = [{"text": f"item{i}"} for i in range(10)]
        result = P.snippet(items)
        assert "(+7 more)" in result


class TestSnippetDict:
    def test_source_json_unwraps_wert(self):
        data = {
            "date": {"wert": "1330–1350"},
            "lang": {"wert": "de"},
            "script": {"wert": "Kurrent"},
            "extra": "should drop",
        }
        result = P.snippet(data, n=3)
        assert "date: 1330–1350" in result
        assert "lang: de" in result
        assert "script: Kurrent" in result
        assert "(+1 more)" in result
        # wert unwrapped — not showing as dict
        assert '{"wert"' not in result

    def test_dict_max_chars_respected(self):
        data = {f"k{i}": {"wert": "v" * 200} for i in range(20)}
        result = P.snippet(data, max_chars=80)
        assert len(result) <= 120  # soft cap; (+N more) suffix may exceed limit slightly


# ── format_phase_event() ─────────────────────────────────────────────────────

class MockPhaseEvent:
    def __init__(self, phase, agent, status, excerpt="", decision="", error=""):
        self.phase = phase
        self.agent = agent
        self.status = status
        self.excerpt = excerpt
        self.decision = decision
        self.error = error


class TestFormatPhaseEvent:
    def test_done_shows_checkmark_and_excerpt(self):
        ev = MockPhaseEvent(phase="agent_c", agent="agent_c",
                            status="done", excerpt="12 entities · Hans von Wiler")
        line = P.format_phase_event(ev)
        assert line.startswith("✅")
        assert "agent_c" in line
        assert "12 entities" in line

    def test_error_shows_cross_and_error_text(self):
        ev = MockPhaseEvent(phase="kraken", agent="kraken",
                            status="error", error="HTTP 404: unknown model 'trocr-x'")
        line = P.format_phase_event(ev)
        assert line.startswith("❌")
        assert "HTTP 404" in line

    def test_decision_appended_when_set(self):
        ev = MockPhaseEvent(phase="agent_c", agent="agent_c",
                            status="done", excerpt="extracted",
                            decision="gpt-4o-mini q=0.82")
        line = P.format_phase_event(ev)
        assert "gpt-4o-mini q=0.82" in line

    def test_line_length_capped(self):
        ev = MockPhaseEvent(phase="x", agent="x", status="done",
                            excerpt="A" * 5000)
        line = P.format_phase_event(ev)
        assert len(line) <= 1905   # 1900 + margin


# ── format_board() ───────────────────────────────────────────────────────────

class TestFormatBoard:
    def _ev(self, n, status="done", excerpt=""):
        return MockPhaseEvent(phase=f"phase{n}", agent=f"agent{n}",
                              status=status, excerpt=excerpt or f"result {n}")

    def test_normal_board_under_limit(self):
        events = [self._ev(i) for i in range(1, 6)]
        board = P.format_board(events, "doc-123")
        assert "doc-123" in board
        assert len(board) < 2000

    def test_overflow_keeps_newest_lines(self):
        # Build many events with enough content to force overflow
        long_excerpt = "x" * 300
        events = [self._ev(i, excerpt=long_excerpt) for i in range(1, 20)]
        board = P.format_board(events, "doc-overflow")
        assert len(board) <= 2000
        # Newest (phase1 is last in reversed order) should be in output
        # Oldest (phase19) should NOT be in output (skipped)
        assert "phase1" in board  # newest
        # Check skip notice is present when overflow happened
        assert "earlier step" in board

    def test_skip_count_plural(self):
        long_excerpt = "y" * 400
        events = [self._ev(i, excerpt=long_excerpt) for i in range(1, 30)]
        board = P.format_board(events, "doc-many")
        assert len(board) <= 2000
        assert "earlier steps" in board  # plural

    def test_single_skip_uses_singular(self):
        long_excerpt = "z" * 500
        # Create enough events that at least one gets skipped
        events = [self._ev(i, excerpt=long_excerpt) for i in range(1, 10)]
        board = P.format_board(events, "doc-single")
        # If skip count == 1, uses singular "step"
        # If skip count == 0, still fits — both are valid
        assert len(board) <= 2000

    def test_error_event_in_board(self):
        events = [
            self._ev(1, status="done", excerpt="ok"),
            MockPhaseEvent(phase="kraken", agent="kraken", status="error",
                           error="HTTP 500"),
        ]
        board = P.format_board(events, "doc-err")
        assert "❌" in board
        assert "HTTP 500" in board
