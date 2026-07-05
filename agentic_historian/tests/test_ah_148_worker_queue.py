"""Tests for #148 (HITL-2a): single worker queue replaces _active_runs.

Offline: drives bot._run_blocking on a real event loop (asyncio.run). Run from
the repo root:
    pytest agentic_historian/tests/test_ah_148_worker_queue.py
"""

import asyncio
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import bot


class _FakeCtx:
    class _Author:
        id = 1
    author = _Author()

    async def _send(self, *a, **k):
        pass
    followup = type("F", (), {"send": None})()

    def __init__(self):
        self.followup.send = self._send


def test_no_active_runs_guard_remains():
    src = (PKG / "bot.py").read_text()
    assert "_active_runs" not in src, "per-user _active_runs guard must be gone"
    assert "_job_queue" in src and "asyncio.to_thread" in src


def test_two_jobs_both_complete_serially():
    async def go():
        order = []

        def job(n):
            order.append(("start", n))
            order.append(("end", n))
            return n * 10

        ctx = _FakeCtx()
        r1, r2 = await asyncio.gather(
            bot._run_blocking(ctx, job, 1),
            bot._run_blocking(ctx, job, 2),
        )
        return order, r1, r2

    order, r1, r2 = asyncio.run(go())
    # both complete — no per-user rejection (the old guard returned None)
    assert r1 == 10 and r2 == 20
    # serial FIFO through one worker: job 1 fully done before job 2 starts
    assert order == [("start", 1), ("end", 1), ("start", 2), ("end", 2)]


def test_failing_job_does_not_stall_queue():
    async def go():
        def boom():
            raise RuntimeError("kaboom")

        def ok():
            return "ok"

        ctx = _FakeCtx()
        err = None
        try:
            await bot._run_blocking(ctx, boom)
        except RuntimeError as e:
            err = str(e)
        # the worker survived — the next job still runs
        result = await bot._run_blocking(ctx, ok)
        return err, result

    err, result = asyncio.run(go())
    assert err == "kaboom" and result == "ok"


def test_blocking_work_runs_off_the_event_loop():
    """While a blocking job runs in the worker thread, the event loop keeps
    ticking (a concurrent coroutine makes progress)."""
    async def go():
        ticks = []

        def slow():
            import time
            time.sleep(0.05)
            return "done"

        async def ticker():
            for _ in range(3):
                await asyncio.sleep(0.01)
                ticks.append(1)

        ctx = _FakeCtx()
        result, _ = await asyncio.gather(bot._run_blocking(ctx, slow), ticker())
        return result, ticks

    result, ticks = asyncio.run(go())
    assert result == "done"
    assert len(ticks) == 3, "event loop must stay responsive during blocking work"
