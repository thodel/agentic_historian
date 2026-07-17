"""#289 (V-3): a live-updating Discord status board — post once, edit thereafter.

The anti-spam guarantee is the point: a 4-page × 7-engine run emits 30+ PhaseEvents
and Discord allows ~5 msg/5 s/channel, so the reporter must post ONE message and
EDIT it, throttled. And ``emit`` is called from the pipeline's worker thread, so it
must marshal to the loop — the most likely place to get it wrong.

Offline — a fake Discord channel records send/edit; no gateway. Run from the repo
root:
    pytest agentic_historian/tests/test_ah_289_progress_reporter.py
"""

import asyncio
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config                          # noqa: E402
import progress_reporter               # noqa: E402
from runstate import PhaseEvent        # noqa: E402


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.edits = []

    async def edit(self, *, content=None):
        self.content = content
        self.edits.append(content)


class _FakeChannel:
    """Records the single send + every edit; optionally raises to test best-effort."""
    def __init__(self, raise_on_send=False, raise_on_edit=False):
        self.sends = []
        self.message = None
        self._raise_on_send = raise_on_send
        self._raise_on_edit = raise_on_edit

    async def send(self, content):
        if self._raise_on_send:
            raise RuntimeError("discord send 500")
        self.sends.append(content)
        self.message = _FakeMessage(content)
        if self._raise_on_edit:
            async def _boom(*, content=None):
                raise RuntimeError("discord edit 500")
            self.message.edit = _boom
        return self.message


def _ev(phase, status="done", excerpt="", decision=""):
    return PhaseEvent(doc_id="d", phase=phase, agent="A", status=status,
                      excerpt=excerpt, decision=decision, error="")


# ── post once, edit thereafter ───────────────────────────────────────────────

def test_posts_once_and_edits_thereafter():
    async def go():
        ch = _FakeChannel()
        r = progress_reporter.ProgressReporter(
            asyncio.get_running_loop(), ch, "d", min_interval=0.0).start()
        for p in ("vlm", "agent_b", "agent_c", "publish"):
            r.emit(_ev(p))
            await asyncio.sleep(0.01)          # let the loop render between steps
        await r.close()
        return ch

    ch = asyncio.run(go())
    assert len(ch.sends) == 1                   # ONE message, never one per step
    assert ch.message.edits                     # …and it was edited as steps arrived
    assert "publish" in ch.message.content      # final board has the last step


# ── throttle: a burst produces far fewer edits than events ───────────────────

def test_a_burst_is_throttled_and_the_final_state_is_correct():
    async def go():
        ch = _FakeChannel()
        r = progress_reporter.ProgressReporter(
            asyncio.get_running_loop(), ch, "d", min_interval=0.05).start()
        for i in range(30):                     # 30 events, faster than the throttle
            r.emit(_ev(f"step{i}", decision=f"n={i}"))
        await r.close()
        return ch

    ch = asyncio.run(go())
    total = len(ch.sends) + (len(ch.message.edits) if ch.message else 0)
    assert total < 30                           # coalesced, not one call per event
    assert "n=29" in ch.message.content         # but the LAST event is shown


# ── best-effort: Discord failures never propagate ────────────────────────────

def test_a_failing_send_does_not_break_the_run():
    async def go():
        ch = _FakeChannel(raise_on_send=True)
        r = progress_reporter.ProgressReporter(
            asyncio.get_running_loop(), ch, "d", min_interval=0.0).start()
        r.emit(_ev("vlm"))
        await asyncio.sleep(0.01)
        await r.close()                         # must not raise

    asyncio.run(go())                           # completing without exception is the test


def test_a_failing_edit_does_not_break_the_run():
    async def go():
        ch = _FakeChannel(raise_on_edit=True)
        r = progress_reporter.ProgressReporter(
            asyncio.get_running_loop(), ch, "d", min_interval=0.0).start()
        r.emit(_ev("vlm"))
        await asyncio.sleep(0.01)
        r.emit(_ev("agent_b"))                  # this triggers an edit → raises internally
        await asyncio.sleep(0.01)
        await r.close()

    asyncio.run(go())


# ── the cross-thread path: emit from a worker thread ─────────────────────────

def test_events_from_a_worker_thread_reach_the_board():
    """The pipeline runs in asyncio.to_thread, so on_phase fires OFF the loop.
    emit must marshal to the loop via call_soon_threadsafe."""
    async def go():
        ch = _FakeChannel()
        r = progress_reporter.ProgressReporter(
            asyncio.get_running_loop(), ch, "d", min_interval=0.0).start()

        def worker():                           # the "pipeline" — a separate thread
            for p in ("vlm", "agent_b", "agent_c"):
                r.emit(_ev(p, excerpt=f"{p} ran"))

        await asyncio.to_thread(worker)
        await asyncio.sleep(0.02)
        await r.close()
        return ch

    ch = asyncio.run(go())
    assert len(ch.sends) == 1
    assert "agent_c" in ch.message.content      # the thread's last event landed


# ── the gate: flag off / no channel → no reporter, no Discord ────────────────

def test_make_reporter_returns_none_when_flag_off(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERBOSE_PROGRESS", False)

    async def go():
        return progress_reporter.make_reporter(_FakeChannel(), "d")
    assert asyncio.run(go()) is None


def test_make_reporter_returns_none_without_a_channel(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERBOSE_PROGRESS", True)

    async def go():
        return progress_reporter.make_reporter(None, "d")
    assert asyncio.run(go()) is None


def test_make_reporter_builds_when_enabled_with_a_channel(monkeypatch):
    monkeypatch.setattr(config, "ENABLE_VERBOSE_PROGRESS", True)

    async def go():
        r = progress_reporter.make_reporter(_FakeChannel(), "d")
        assert r is not None
        await r.close()
    asyncio.run(go())


# ── wiring: bot.py uses the reporter helper ──────────────────────────────────

def test_bot_run_command_is_registered_and_uses_the_board_helper():
    """/run must route through _run_with_board (which threads on_phase in), not
    _run_blocking directly — otherwise the flag would never take effect."""
    import inspect
    import bot

    cmd = next((c for c in bot.bot.pending_application_commands if c.name == "run"), None)
    assert cmd is not None, "/run command must be registered"
    src = inspect.getsource(cmd.callback)          # SlashCommand → underlying function
    assert "_run_with_board" in src

    # the helper exists and passes on_phase only when a reporter is built
    helper_src = inspect.getsource(bot._run_with_board)
    assert "make_reporter" in helper_src and "on_phase" in helper_src
    assert "reporter.close" in helper_src or "reporter.emit" in helper_src
