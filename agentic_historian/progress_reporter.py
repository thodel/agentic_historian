"""progress_reporter.py — one live-updating Discord status board per run (#289, V-3).

Streams the pipeline's ``PhaseEvent``s (V-2, #288) into a single Discord message,
rendered with ``progress.format_board`` (V-1, #287), editing that one message as
steps arrive.

**Thread-safety is the whole game.** ``on_phase`` (→ :meth:`ProgressReporter.emit`)
is called from the pipeline's WORKER THREAD (``asyncio.to_thread`` in bot.py), not
the event loop. ``emit`` therefore does the minimum: append the event under a lock
and wake the loop via ``call_soon_threadsafe``. **Every** Discord call happens on
the loop, inside :meth:`_render_loop`. Touching the channel/message or an
``asyncio`` primitive directly from ``emit`` would corrupt the loop or deadlock.

**Anti-spam.** One message, edited — never one message per step. A 4-page ×
7-engine run emits 30+ events; Discord allows ~5 messages / 5 s / channel, so
per-step posting would rate-limit or spam. Edits are throttled to at most one per
``min_interval`` seconds, and the final state is always flushed.

**Best-effort.** Any Discord error is caught and logged; reporting must never break
a run.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from loguru import logger


class ProgressReporter:
    """A throttled, thread-safe live board for one run. Build on the loop, feed
    from the worker thread via :meth:`emit`, and :meth:`close` on the loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop, channel: Any, doc_id: str,
                 *, min_interval: float = 2.0) -> None:
        self._loop = loop
        self._channel = channel
        self._doc_id = doc_id
        self._min_interval = min_interval

        self._events: list = []
        self._lock = threading.Lock()          # guards _events across the two threads
        self._message = None
        self._last_body: str | None = None
        self._wake = asyncio.Event()
        self._done = False
        self._task: asyncio.Task | None = None

    # ── lifecycle (loop thread) ──────────────────────────────────────────────

    def start(self) -> "ProgressReporter":
        """Start the render task. Call once, on the event loop, before the run."""
        if self._task is None:
            self._task = self._loop.create_task(self._render_loop())
        return self

    async def close(self) -> None:
        """Flush the final state and stop the render task. Call on the loop."""
        self._done = True
        self._wake.set()
        if self._task is not None:
            try:
                await self._task
            except Exception as e:                      # pragma: no cover — defensive
                logger.warning(f"[progress] reporter close failed: {e}")

    # ── ingest (WORKER THREAD) ───────────────────────────────────────────────

    def emit(self, ev: Any) -> None:
        """``on_phase`` sink. Runs on the pipeline's worker thread — append and
        wake the loop ONLY; never do Discord or asyncio work here."""
        with self._lock:
            self._events.append(ev)
        try:
            self._loop.call_soon_threadsafe(self._wake.set)
        except RuntimeError:                            # loop already closed
            pass

    # ── render (loop thread) ─────────────────────────────────────────────────

    async def _render_loop(self) -> None:
        try:
            while not self._done:
                await self._wake.wait()
                self._wake.clear()
                await self._flush()
                if self._done:
                    break
                # cool-down: coalesce a burst of events into one edit
                await asyncio.sleep(self._min_interval)
        finally:
            await self._flush()                         # always reflect final state

    async def _flush(self) -> None:
        with self._lock:
            if not self._events:
                return
            snapshot = list(self._events)
        try:
            from progress import format_board
            body = format_board(snapshot, self._doc_id)
        except Exception as e:                          # pragma: no cover — defensive
            logger.warning(f"[progress] render failed: {e}")
            return
        if body == self._last_body:                     # nothing changed → no API call
            return
        try:
            if self._message is None:
                self._message = await self._channel.send(body)
            else:
                await self._message.edit(content=body)
            self._last_body = body
        except Exception as e:
            logger.warning(f"[progress] board update failed: {e}")


def make_reporter(channel: Any, doc_id: str, *, min_interval: float = 2.0):
    """Build+start a :class:`ProgressReporter`, or return ``None`` when it should
    not run: verbose progress disabled, no channel, or no running loop.

    ``channel`` None (a background run with no ``VERBOSE_PROGRESS_CHANNEL_ID``)
    → ``None`` → the run stays log-only (V-2's default sink still logs each step).
    """
    import config
    if not getattr(config, "ENABLE_VERBOSE_PROGRESS", False):
        return None
    if channel is None:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:                                # pragma: no cover — no loop
        return None
    return ProgressReporter(loop, channel, doc_id, min_interval=min_interval).start()
