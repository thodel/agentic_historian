"""
utils/metrics.py

Lightweight per-run metrics tracker for Agent E.
Tracks wall-clock time per agent and cumulative GPUStack token usage.
No USD — local stack has no per-token cost.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

# Thread-safe singleton metrics store
_lock = threading.Lock()


@dataclass
class AgentRun:
    agent: str          # e.g. "Agent_A", "Agent_B"
    started_at: str     # ISO timestamp
    wall_clock_ms: int  # real elapsed milliseconds
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: bool = False


@dataclass
class SessionMetrics:
    """Accumulates metrics for one pipeline session."""
    session_id: str = field(default_factory=lambda: datetime.now().isoformat())
    runs: list[AgentRun] = field(default=list)
    _start: float = field(default_factory=time.monotonic, repr=False)

    def total_wall_clock_ms(self) -> int:
        return sum(r.wall_clock_ms for r in self.runs)

    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.runs)

    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.runs)

    def total_tokens(self) -> int:
        return self.total_prompt_tokens() + self.total_completion_tokens()


_metrics: Optional[SessionMetrics] = None


def reset_metrics():
    """Start a fresh metrics session."""
    global _metrics
    with _lock:
        _metrics = SessionMetrics()


def get_metrics() -> SessionMetrics:
    if _metrics is None:
        reset_metrics()
    return _metrics


class AgentTimer:
    """
    Context manager: measures wall-clock time for a single agent run.
    Usage:
        with AgentTimer("Agent_B") as timer:
            ... do work ...
        timer.run.prompt_tokens = 150
    """
    def __init__(self, agent_name: str, error: bool = False):
        self.agent_name = agent_name
        self.error = error
        self._start: Optional[float] = None

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *args):
        elapsed_ms = int((time.monotonic() - self._start) * 1000)
        run = AgentRun(
            agent=self.agent_name,
            started_at=datetime.now().isoformat(timespec="seconds"),
            wall_clock_ms=elapsed_ms,
            error=self.error,
        )
        m = get_metrics()
        with _lock:
            m.runs.append(run)
        return False

    @property
    def run(self) -> AgentRun:
        """Returns the AgentRun that was just recorded."""
        m = get_metrics()
        with _lock:
            return m.runs[-1]


def record_run(
    agent: str,
    wall_clock_ms: int,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error: bool = False,
):
    """Manually record a completed agent run."""
    run = AgentRun(
        agent=agent,
        started_at=datetime.now().isoformat(timespec="seconds"),
        wall_clock_ms=wall_clock_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error=error,
    )
    m = get_metrics()
    with _lock:
        m.runs.append(run)