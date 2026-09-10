"""Announce what happened on the training server, instead of waiting to be asked.

`/atr_jobs` and `/atr_gpu` answer "what is running". They do not help with the
case #418 was filed for: `20260908T101611Z-qwen3vl-german-pages-v1` ran for
**11 h 24 m**, reached 785 of 2355 steps, died, and nobody knew for hours because
nobody happened to ask. The same shape as the data-loader worker that held
27,530 MiB for sixteen hours while both cards read 0 % utilisation.

Neither is a question. Both are events, and an event nobody is told about is
found late by definition.

**Everything here is a pure function of (what we saw last time, what we see
now).** The bot supplies the polling and the posting; this module decides, so
that "does a cold start flood the channel with forty historical jobs?" is a
question a unit test answers rather than a thing discovered in production.

Two rules carry most of the weight:

**Silence on first sight.** A watcher with no memory considers everything new.
The first poll after a restart records the world and announces nothing — the
alternative is that every deploy pastes the entire job history into the channel,
and a channel that does that once is muted for ever.

**Announce a state, not a reading.** A failed job is announced when it *becomes*
failed, once. Memory nothing accounts for is announced once it has persisted,
once, and again only if it goes away and comes back. The report that fires every
poll is the report nobody reads — which is exactly how the sixteen-hour orphan
stayed invisible while `/atr_gpu` was, in principle, able to show it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "TERMINAL",
    "ORPHAN_GRACE_S",
    "Announcement",
    "WatchState",
    "decide",
    "load_state",
    "save_state",
]

#: A job in one of these has finished moving and is worth one message.
TERMINAL = ("completed", "failed", "cancelled")

#: How long memory nothing accounts for must persist before it is announced.
#: A sweep started by hand is legitimate work and does not need a message the
#: second it starts; one still holding a card an hour later is worth knowing
#: about, whoever started it.
ORPHAN_GRACE_S = 3600.0


@dataclass(frozen=True)
class Announcement:
    """One thing to say, and enough to say it well."""

    kind: str          # "job" | "memory"
    key: str           # job id, or pid
    text: str
    #: Worth pulling someone out of whatever they are doing. A Discord message
    #: without a mention does not reliably reach a phone — that depends on the
    #: reader's channel setting — and one that always mentions gets the channel
    #: muted, which costs the non-urgent messages too. So: a failure and memory
    #: nothing accounts for are urgent; a run that finished well is not.
    urgent: bool = False

    def __str__(self) -> str:
        return self.text

    def render(self, mention: str = "") -> str:
        """The message as posted, with the mention only where it is earned."""
        if mention and self.urgent:
            return f"{mention} {self.text}"
        return self.text


@dataclass
class WatchState:
    """What has already been announced. Small, and boring on purpose."""

    #: job id → the status we last announced (or recorded, on a cold start)
    jobs: dict[str, str] = field(default_factory=dict)
    #: pid → the size we announced it holding, so it is not announced twice
    flagged: dict[str, int] = field(default_factory=dict)
    #: False until the first poll has been recorded; nothing is announced before.
    seeded: bool = False

    def to_json(self) -> str:
        return json.dumps({"jobs": self.jobs, "flagged": self.flagged,
                           "seeded": self.seeded}, indent=2)

    @classmethod
    def from_dict(cls, raw: dict) -> "WatchState":
        return cls(jobs=dict(raw.get("jobs") or {}),
                   flagged={str(k): int(v) for k, v in (raw.get("flagged") or {}).items()},
                   seeded=bool(raw.get("seeded", False)))


def _hours(seconds: float | None) -> str:
    if not seconds:
        return "?"
    return f"{seconds / 3600:.0f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


#: A line that names what actually went wrong. Anchored at the start of a
#: stripped line so a mention inside prose does not match.
_EXCEPTION_RE = re.compile(
    r"^[\w.]*(?:Error|Exception|Failed|Interrupt|Killed)\b\s*:", re.IGNORECASE)

#: tqdm redraws and the ANSI cursor moves it leaves behind. The last line of a
#: failed training log is almost always one of these, not the cause.
_NOISE = ("it/s]", "s/it]", "%|", "[A")


def _error_summary(error: str, cap: int = 240) -> str:
    """The line of a failure a person would want, not the first or the last one.

    Measured against the 32 failed jobs on the box, neither end works. The first
    line is our own wrapper — ``StageFailed in train: train failed: python exited
    1. Last lines of logs/train.log:`` — identical for every engine and every
    cause. The last line is a tqdm redraw or the bare ANSI sequence ``[A``.

    The cause sits in between, as the final exception of the tail we captured. So
    scan backwards for a line that names an exception, and fall back to the first
    line when there is none — a cancellation reads ``cancelled on request`` and
    has no traceback at all.
    """
    lines = [line.strip() for line in (error or "").splitlines() if line.strip()]
    if not lines:
        return ""
    for line in reversed(lines):
        if any(bit in line for bit in _NOISE):
            continue
        if _EXCEPTION_RE.match(line):
            return line[:cap]
    return lines[0][:cap]


def _job_line(job: dict) -> str:
    """One job's outcome, with the number a person would ask for next."""
    status = job.get("status")
    progress = job.get("progress") or {}
    metrics = job.get("metrics") or {}
    bits = [f"**{job.get('id')}** — {status}"]

    if status == "failed" and job.get("error"):
        # One line, chosen: the whole tail is already on the job record, and a
        # traceback pasted into a channel is how a message gets scrolled past.
        summary = _error_summary(str(job["error"]))
        if summary:
            bits.append(summary)
    if status == "completed":
        cer = metrics.get("cer")
        if cer is not None:
            bits.append(f"CER {cer:.4f}")
        if job.get("published"):
            bits.append(str(job["published"])[:120])
    epoch, epochs = progress.get("epoch"), progress.get("epochs")
    if epoch or epochs:
        bits.append(f"Epoche {epoch or '?'}/{epochs or '?'}")
    return "\n".join(bits)


def _memory_line(proc: dict, card_index: Any) -> str:
    what = "verwaist" if proc.get("orphaned") else "ohne Dienst"
    line = (f"**{proc.get('used_mib')} MiB auf GPU {card_index} seit "
            f"{_hours(proc.get('age_s'))}** — {what}, pid {proc.get('pid')}"
            f" ({proc.get('user') or 'unbekannt'})")
    if proc.get("command"):
        line += f"\n`{str(proc['command'])[:160]}`"
    return line


def decide(state: WatchState, jobs_payload: dict, gpu_payload: dict,
           grace_s: float = ORPHAN_GRACE_S) -> tuple[list[Announcement], WatchState]:
    """What to say now, and the state to remember having said it.

    Returns a *new* state; the caller saves it only after the messages are away,
    so a crash between deciding and posting repeats a message rather than losing
    one. Repeating is recoverable and losing is the thing this exists to prevent.
    """
    jobs = {j.get("id"): j for j in (jobs_payload or {}).get("jobs") or [] if j.get("id")}
    seen_jobs = {job_id: (job.get("status") or "?") for job_id, job in jobs.items()}

    flagged: dict[str, int] = {}
    candidates: list[tuple[dict, Any]] = []
    for card in (gpu_payload or {}).get("cards") or []:
        for proc in card.get("processes") or []:
            if proc.get("registered") or proc.get("own_service"):
                continue
            # A named unit belonging to someone else is a capacity fact, not an
            # incident — the neighbours' RAG service has held 10 GB for months.
            if proc.get("service") and not proc.get("orphaned"):
                continue
            if (proc.get("age_s") or 0) < grace_s:
                continue
            flagged[str(proc.get("pid"))] = int(proc.get("used_mib") or 0)
            candidates.append((proc, card.get("index")))

    fresh = WatchState(jobs=seen_jobs, flagged=flagged, seeded=True)

    if not state.seeded:
        # Cold start: record the world, say nothing about how it got that way.
        return [], fresh

    out: list[Announcement] = []
    for job_id, status in seen_jobs.items():
        if status in TERMINAL and state.jobs.get(job_id) != status:
            # "cancelled" is someone's own doing, so it is news but not an alarm.
            out.append(Announcement("job", job_id, _job_line(jobs[job_id]),
                                    urgent=status == "failed"))

    for proc, card_index in candidates:
        pid = str(proc.get("pid"))
        if pid not in state.flagged:
            out.append(Announcement("memory", pid, _memory_line(proc, card_index),
                                    urgent=True))

    return out, fresh


# ── persistence ─────────────────────────────────────────────────────────────

def load_state(path: str | Path) -> WatchState:
    """The stored state, or an unseeded one. A corrupt file is not fatal.

    Losing the file means one silent poll, which is the safe direction: the
    alternative reading — treat unreadable as empty-but-seeded — would announce
    every terminal job in the list at once.
    """
    try:
        return WatchState.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return WatchState()


def save_state(path: str | Path, state: WatchState) -> None:
    """Write via a temporary file, so a crash cannot leave half a state."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(state.to_json(), encoding="utf-8")
    tmp.replace(path)
