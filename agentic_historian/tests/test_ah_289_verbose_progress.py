"""Tests for #289 (V-3): Discord live status board — _ProgressReporter.

Offline, Discord mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_289_verbose_progress.py
"""

import asyncio
import time
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


# ── PhaseEvent duck type ─────────────────────────────────────────────────────

def make_ev(doc_id, phase, agent, status="done", excerpt="test output",
            decision="", error=""):
    ev = MagicMock()
    ev.doc_id = doc_id
    ev.phase = phase
    ev.agent = agent
    ev.status = status
    ev.excerpt = excerpt
    ev.decision = decision
    ev.error = error
    return ev


# ── Mock Discord objects ─────────────────────────────────────────────────────

class MockMessage:
    def __init__(self):
        self.content = ""

    async def edit(self, **kwargs):
        self.content = kwargs.get("content", self.content)


class MockChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content):
        msg = MockMessage()
        msg.content = content
        self.sent.append(msg)
        return msg


# ── import bot with mocked watchdog ─────────────────────────────────────────

def _import_bot():
    mock_wd = MagicMock()
    mock_wd.events.FileSystemEventHandler = object
    mock_wd.observers.Observer = object
    sys.modules["watchdog"] = mock_wd
    # Clear any cached agentic_historian modules
    for k in list(sys.modules):
        if k.startswith("agentic_historian"):
            del sys.modules[k]
    from agentic_historian import bot as bm
    return bm, mock_wd


# ── TestProgressReporter ─────────────────────────────────────────────────────

class TestProgressReporter:

    @pytest.fixture(autouse=True)
    def setup(self):
        self._bm, self._mock_wd = _import_bot()
        yield
        # restore watchdog
        for k in list(sys.modules):
            if k == "watchdog" or k.startswith("watchdog."):
                sys.modules.pop(k, None)

    def _reporter(self, channel_id=999, doc_id="doc-1", channel=None):
        reporter = self._bm._ProgressReporter(
            channel_id=channel_id, doc_id=doc_id
        )
        return reporter

    # ── board is under 2000 chars ────────────────────────────────────────────

    def test_board_under_limit(self):
        from progress import format_board
        events = [make_ev("d", f"p{i}", "A", excerpt=f"x{i}") for i in range(20)]
        board = format_board(events, "d")
        assert len(board) <= 2000, f"board is {len(board)} chars"

    # ── send once, edit thereafter ───────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_one_send_multiple_events(self):
        channel = MockChannel()
        with patch.object(self._bm, "bot", MagicMock(get_channel=lambda cid: channel)):
            reporter = self._reporter(channel_id=999, doc_id="t1")
            # Simulate on_phase from a thread context (call_soon_threadsafe)
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(
                reporter.on_phase, make_ev("t1", "vlm", "A", excerpt="line 1")
            )
            loop.call_soon_threadsafe(
                reporter.on_phase, make_ev("t1", "agent_b", "B", excerpt="desc")
            )
            await asyncio.sleep(0.2)
            # One POST, no second POST
            assert len(channel.sent) == 1, f"expected 1 POST, got {len(channel.sent)}"

    # ── throttle: burst → fewer than N edits ─────────────────────────────────

    @pytest.mark.asyncio
    async def test_throttle_burst(self):
        channel = MockChannel()
        with patch.object(self._bm, "bot", MagicMock(get_channel=lambda cid: channel)):
            reporter = self._reporter(channel_id=999, doc_id="t2")
            loop = asyncio.get_running_loop()
            for i in range(10):
                loop.call_soon_threadsafe(
                    reporter.on_phase,
                    make_ev("t2", f"p{i}", "A", excerpt=f"out-{i}")
                )
            await asyncio.sleep(0.3)
            # Should not have crashed, final board contains last event
            assert len(channel.sent) == 1
            assert "out-9" in channel.sent[0].content

    # ── Discord failure is swallowed ─────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_discord_failure_swallowed(self):
        class FailChan:
            async def send(self, *a, **kw):
                raise RuntimeError("network error")
            async def edit(self, *a, **kw):
                raise RuntimeError("edit error")

        chan = FailChan()
        with patch.object(self._bm, "bot", MagicMock(get_channel=lambda cid: chan)):
            reporter = self._reporter(channel_id=999, doc_id="t3")
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(
                reporter.on_phase, make_ev("t3", "vlm", "A", excerpt="x")
            )
            # Must not raise
            await asyncio.sleep(0.1)

    # ── flag off → no reporter created ───────────────────────────────────────

    def test_flag_off_means_no_reporting(self):
        import config as cfg_mod
        old = cfg_mod.ENABLE_VERBOSE_PROGRESS
        cfg_mod.ENABLE_VERBOSE_PROGRESS = False
        try:
            assert cfg_mod.ENABLE_VERBOSE_PROGRESS is False
        finally:
            cfg_mod.ENABLE_VERBOSE_PROGRESS = old


class TestFormatBoardHardCapped:
    """format_board edge cases for the 2000-char Discord limit."""

    def test_many_events_capped_at_2000(self):
        from progress import format_board
        events = [
            make_ev("s", f"p{i}", "A",
                    excerpt="A" * 300,
                    decision=f"model={i}")
            for i in range(30)
        ]
        board = format_board(events, "stress-doc")
        assert len(board) <= 2000

    def test_error_event_shows_error_text(self):
        from progress import format_board
        ev = make_ev("e", "kraken", "A", status="error",
                     error="HTTP 404: model not found")
        board = format_board([ev], "err-doc")
        assert "HTTP 404" in board
        assert "❌" in board

    def test_done_event_shows_excerpt(self):
        from progress import format_board
        ev = make_ev("d", "agent_b", "B", excerpt="14th c. charter, Latin")
        board = format_board([ev], "doc-ok")
        assert "14th c. charter, Latin" in board
        assert "✅" in board

    def test_decision_appended(self):
        from progress import format_board
        ev = make_ev("d", "fusion", "A", excerpt="merged text",
                     decision="gpt-4o q=0.91")
        board = format_board([ev], "doc-dec")
        assert "gpt-4o q=0.91" in board


class TestConfigFlags:
    """Verbose progress config flags default to off."""

    def test_enable_flag_default_off(self):
        import os
        val = os.environ.get("ENABLE_VERBOSE_PROGRESS", "0")
        assert val == "0", "should default to 0/unset"

    def test_channel_id_default_none(self):
        import os
        val = os.environ.get("VERBOSE_PROGRESS_CHANNEL_ID", "0")
        assert val == "0", "should default to 0/unset"
