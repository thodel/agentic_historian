"""Keep one Discord message per training run up to date, across restarts.

A training run takes hours or days on a shared box. `/train_start` (#362) posts
"started" and then says nothing, so the only way to learn that a run failed in
its third hour is to ask — and #418 is the issue filed because nobody asked for
eleven hours while a run was already dead.

**Everything here is a pure function of (what the registry remembers, what the
gateway just said).** The bot supplies the polling, the socket and the message
edit; this module decides, so that "does a restart report the stages again?" is
a question a unit test answers rather than something found in a channel.

Three decisions worth stating, because each one is the opposite of what the
first draft of a watcher does:

**The registry is on disk.** `{job_id: {channel_id, message_id, started_by}}`,
beside the pending confirmations (#614) and for the same reason: `/update`
restarts this bot by design (#245) and a run outlives that restart by hours. A
registry in memory means a run started at 14:00 and a deploy at 14:20 leave
nobody watching — and the message would sit at "queued" for ever, which is worse
than no message, because it reads as a fact. The trainer persists its own job
state for this reason; this is the same argument one process further out.

**No silence on first sight.** `atr_watch` deliberately announces nothing on its
first poll — it watches *everything* on the box, so a cold start would paste the
whole job history into the channel. This watcher is the other case: every run in
its registry was put there on purpose, by somebody who asked to be told. So a
run that finished while the bot was down is reported on the next poll, which is
the acceptance criterion a seeded flag would have broken.

**A stage is announced once, by the board growing a line.** Not one message per
transition and not one per tick: the message is edited in place, the stage trail
is part of it, and `decide` reports a stage as new exactly once over the run's
life because the registry remembers which were told. A terminal state gets its
own message as well, because a board that scrolled away is not news.

Run: pytest agentic_historian/tests/test_ah_363_train_watch.py -v
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from loguru import logger

import config

__all__ = [
    "POLL_S", "STAGES", "STAGE_OF_STATUS", "UNREACHABLE_GRACE_S",
    "FORGOTTEN_GRACE_S", "WatchedRun", "Update", "decide", "board",
    "terminal_notice", "note_unreachable", "note_reachable", "note_missing",
    "format_list", "path", "load", "save", "register", "drop", "find",
    "rebind", "wall_s", "hours",
]

#: How often to ask about one run. A stage boundary is worth knowing about within
#: the minute; a training step is not, and the trainer runs one job at a time, so
#: this is a handful of requests an hour (#363).
POLL_S = 60.0

#: The five stages every backend goes through, in order — the trainer's
#: ``JobStage`` literal.
STAGES = ("prepare", "compile", "train", "test", "register")

#: status → the stage a job is in while it carries that status. The trainer's
#: ``STAGE_STATUS`` read the other way round, for the records that carry a
#: status but no ``stage``.
STAGE_OF_STATUS = {
    "preparing":   "prepare",
    "compiling":   "compile",
    "training":    "train",
    "testing":     "test",
    "registering": "register",
}

#: How long the gateway must stay silent about a watched run before that silence
#: is itself worth a message. Not on the first failure: the gateway is restarted
#: for every deploy of the serving stack, and a message per deploy is how a
#: channel gets muted. Half an hour, as in ``atr_watch``.
UNREACHABLE_GRACE_S = 1800.0

#: How long the trainer may answer "no such job" before the run is given up on.
#: A 404 is not a blip — the gateway is talking — but it is not instantly fatal
#: either: a trainer restarted mid-request can answer 404 for a moment. A day is
#: long enough that only a job the trainer has really forgotten reaches it, and
#: short enough that a typo'd adoption does not sit in the registry for ever.
FORGOTTEN_GRACE_S = 86400.0


# ── the registry ─────────────────────────────────────────────────────────────

@dataclass
class WatchedRun:
    """One run being watched, and everything a restarted bot needs to resume."""

    job_id: str
    channel_id: str
    message_id: str
    started_by: str
    #: The preset name, which the **trainer's record does not have**: a preset is
    #: the bot's naming for a request body (#362). It is kept here because this
    #: is the only place it exists after the submit.
    preset: str = ""
    model_id: str = ""
    registered_at: float = field(default_factory=time.time)
    #: The status last seen from the gateway. ``None`` means not yet polled — a
    #: third value, distinct from "seen, and it was queued": the first says
    #: nothing has been measured, and the second is a measurement.
    last_status: Optional[str] = None
    #: Stage names already on the board. Persisted, so a restart does not
    #: announce ``train`` a second time.
    stages_told: list[str] = field(default_factory=list)
    #: The board as last written. Compared against the next one so an unchanged
    #: board is not re-sent: an edit per minute with identical content is the
    #: report nobody reads, and after a restart it would also be a duplicate.
    last_board: str = ""
    #: When the gateway first failed to answer about this run in the current
    #: streak, and whether that streak has been announced. Cleared on an answer.
    unreachable_since: Optional[float] = None
    unreachable_told: bool = False
    #: When the trainer first answered "no such job" for this id.
    missing_since: Optional[float] = None


def path() -> Path:
    """Beside the pending confirmations and the update marker (#614, #245)."""
    return config.DATA_DIR / ".train-runs.json"


def load() -> list[WatchedRun]:
    """Every watched run. Never raises.

    An unreadable file yields nothing rather than an exception: this is read in
    ``on_ready``, and a bot that refuses to start because a run file is corrupt
    has turned a lost progress board into an outage.
    """
    p = path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                        # noqa: BLE001
        logger.warning(f"[trainwatch] unreadable {p.name}: {exc}")
        return []
    out: list[WatchedRun] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(WatchedRun(**entry))
        except TypeError as exc:        # a field this build does not know
            logger.warning(f"[trainwatch] skipping entry: {exc}")
    return out


def save(runs: list) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in runs]
    try:
        from shared_lock import write_json_atomic
        write_json_atomic(p, payload)
    except Exception:                               # pragma: no cover — fallback
        p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def register(run: WatchedRun) -> None:
    """Add or replace one run, by job id."""
    kept = [r for r in load() if r.job_id != run.job_id]
    kept.append(run)
    save(kept)


def drop(job_id: str) -> None:
    """Forget one — it reached a terminal state, or the trainer forgot it."""
    save([r for r in load() if r.job_id != job_id])


def rebind(run: WatchedRun, message_id: str) -> WatchedRun:
    """The same run, watching a different message.

    Used when the board had to be re-posted because the original message was
    deleted while the bot was away. The run is never dropped for that: it is
    still training on the box, and losing the only record of it is the failure
    this module exists to prevent.
    """
    return replace(run, message_id=str(message_id))


def find(job_id: str) -> Optional[WatchedRun]:
    for run in load():
        if run.job_id == job_id:
            return run
    return None


# ── what to do about one poll ────────────────────────────────────────────────

@dataclass(frozen=True)
class Update:
    """What to write, and what to say on its own."""

    #: The whole content of the one message, ready to replace what is there.
    board: str
    #: A message of its own, or ``""``. Only a terminal state earns one.
    notice: str = ""
    #: Stages that reached the board in this poll, in pipeline order. Each stage
    #: appears here at most once over a run's life.
    stages_new: tuple[str, ...] = ()
    terminal: bool = False
    #: False when the board is identical to the one already posted, so the
    #: caller edits nothing. See ``WatchedRun.last_board``.
    changed: bool = True
    #: Worth pulling somebody out of what they are doing. A failure is; a run
    #: that finished well is not. Same rule as ``atr_watch.Announcement``.
    urgent: bool = False


def decide(run: WatchedRun, job: Any, *,
           now: Optional[float] = None) -> tuple[Update, WatchedRun]:
    """What to write about *run* given *job*, and the run to remember.

    *job* is a ``TrainJob`` (``agent_a.training_client``), parsed from the
    gateway's record. Returns a **new** run; the caller saves it only after the
    messages are away, so a crash in between repeats an edit rather than losing
    a terminal report. Repeating is noise and losing is the eleven hours.
    """
    now = time.time() if now is None else now
    status = _status_of(job)
    reached = _reached(job)
    stages_new = tuple(s for s in reached if s not in run.stages_told)
    terminal = bool(getattr(job, "is_terminal", lambda: False)())

    text = board(run, job, now=now)
    notice = terminal_notice(run, job, now=now) if terminal else ""

    fresh = replace(
        run,
        last_status=status,
        stages_told=list(run.stages_told) + [s for s in stages_new],
        last_board=text,
        # An answer about this run closes any silence streak, whether or not it
        # was announced.
        unreachable_since=None,
        unreachable_told=False,
        missing_since=None,
    )
    return Update(board=text, notice=notice, stages_new=stages_new,
                  terminal=terminal, changed=text != run.last_board,
                  urgent=status == "failed"), fresh


def _status_of(job: Any) -> str:
    """The job's status as a plain word, whatever shape it arrives in."""
    status = getattr(job, "status", None)
    raw = getattr(job, "status_raw", "") or ""
    value = getattr(status, "value", status)
    if value in (None, "unknown") and raw:
        # The trainer's own word, not ours: see ``JobStatus.UNKNOWN``.
        return raw
    return str(value or "?")


def _reached(job: Any) -> tuple[str, ...]:
    """Which stages the record says this job has been in, in pipeline order.

    From the stage records first, because those are retrospective and therefore
    the part that survives a bot restart: a stage that ran while nobody was
    watching is still in the list when the bot comes back.

    Deliberately **not** "every stage up to the current one". ``prepare`` is
    skipped outright when a run adopts a cached artefact, and a board claiming a
    stage that never ran would be the project's recurring mistake — a value that
    says more than was measured — in the one place a reader checks what happened.
    """
    seen: set[str] = set()
    for stage, stage_status in (getattr(job, "stages", None) or {}).items():
        name = str(getattr(stage, "value", stage))
        if name in STAGES and str(stage_status) != "pending":
            seen.add(name)
    current = getattr(job, "stage", None)
    if current in STAGES:
        seen.add(str(current))
    implied = STAGE_OF_STATUS.get(_status_of(job))
    if implied:
        seen.add(implied)
    return tuple(s for s in STAGES if s in seen)


# ── rendering ────────────────────────────────────────────────────────────────

#: Discord hard-limits a message to 2000 characters.
LIMIT = 1900


def hours(seconds: Optional[float]) -> str:
    """A duration, or the word for not having one. Never a fabricated zero."""
    if seconds is None:
        return "unbekannt"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 60:.0f} min"


def _pct(value: Optional[float]) -> str:
    """A rate as a percentage, or ``unbekannt``.

    ``is None``, not falsiness: a CER of 0.0 is the best possible measurement,
    and printing "unbekannt" for it would hide the one result worth celebrating.
    """
    if value is None:
        return "unbekannt"
    return f"{value * 100:.2f} %"


def _epoch(value: Any) -> Optional[float]:
    """A datetime as a unix timestamp, or None.

    Through ``timestamp()`` rather than by subtracting two datetimes: the record
    carries ISO strings and one of them may parse without a zone, and
    aware − naive raises. A watcher may not fall over because a timestamp came
    back without its ``Z``.
    """
    if value is None:
        return None
    try:
        return float(value.timestamp())
    except Exception:                               # noqa: BLE001
        return None


def wall_s(job: Any, *, now: Optional[float] = None) -> Optional[float]:
    """How long the run has been going, from the job's own timestamps.

    ``None`` when the record has no ``started_at`` — a queued job has not run for
    any length of time, and the registry's own ``registered_at`` is when *the
    bot* heard about it, which for an adopted run can be hours later. Measuring
    from that would print a runtime nobody measured.
    """
    started = _epoch(getattr(job, "started_at", None))
    if started is None:
        return None
    finished = _epoch(getattr(job, "finished_at", None))
    if finished is not None:
        return max(0.0, finished - started)
    now = time.time() if now is None else now
    return max(0.0, now - started)


def _trail(job: Any, reached: tuple[str, ...]) -> str:
    """The five stages, each marked with what became of it."""
    stages = {str(getattr(k, "value", k)): str(v)
              for k, v in (getattr(job, "stages", None) or {}).items()}
    current = getattr(job, "stage", None)
    marks = []
    for name in STAGES:
        state = stages.get(name, "")
        if state == "failed":
            marks.append(f"❌ {name}")
        elif name == current and not getattr(job, "is_terminal", lambda: False)():
            marks.append(f"▶ {name}")
        elif state == "completed" or (name in reached and state != "running"):
            marks.append(f"✅ {name}")
        elif name in reached:
            marks.append(f"▶ {name}")
        else:
            marks.append(f"⬚ {name}")
    return " · ".join(marks)


def _progress_line(job: Any) -> str:
    """Epochs and steps, where the record has them. Empty when it does not."""
    progress = getattr(job, "progress", None) or {}
    bits = []
    epoch, epochs = progress.get("epoch"), progress.get("epochs")
    if epoch is not None:
        bits.append(f"Epoche {epoch}" + (f"/{epochs}" if epochs else ""))
    steps = progress.get("total_steps")
    if steps:
        bits.append(f"{steps} Schritte geplant")
    pages = progress.get("pages_written")
    if pages is not None:
        bits.append(f"{pages} Seiten")
    return ", ".join(bits)


def board(run: WatchedRun, job: Any, *, now: Optional[float] = None) -> str:
    """The one message, in full — the content to edit into place.

    One message per run rather than a line per poll. A run has 60 polls an hour
    and five things worth saying, so posting would bury the channel and the board
    is the only form in which "where is it now" stays readable.
    """
    status = _status_of(job)
    reached = _reached(job)
    job_id = getattr(job, "id", "") or run.job_id
    model = getattr(job, "model_id", "") or run.model_id or "?"
    head = f"🎓 **Lauf `{job_id}`**"
    if run.preset:
        head += f" · Preset `{run.preset}`"
    head += f" · Ziel `{model}`"

    lines = [head, f"Status: **{status}**", f"Stufen: {_trail(job, reached)}"]

    queued = getattr(job, "queued_reason", None)
    if queued:
        # Verbatim, and never summarised: "waiting for job X" and "GPU 1 has
        # 2000 MB free" are the same status and call for opposite responses from
        # whoever is reading — one waits, the other goes looking for what holds
        # the card (#363).
        lines.append(f"Wartet: {queued}")

    progress = _progress_line(job)
    if progress:
        lines.append(f"Fortschritt: {progress}")

    held = wall_s(job, now=now)
    lines.append(f"Laufzeit: {hours(held)}")

    if getattr(job, "is_terminal", lambda: False)():
        if status == "failed":
            lines.append(f"Fehler: {(getattr(job, 'error', None) or '—')[:400]}")
            tail = getattr(job, "log_tail", None)
            if tail:
                lines.append("```\n" + tail[-700:] + "\n```")
        else:
            lines.append(f"CER {_pct(getattr(job, 'cer', None))} · "
                         f"WER {_pct(getattr(job, 'wer', None))}")
            where = getattr(job, "model_path", None) or getattr(job, "published", None)
            if where:
                lines.append(f"Modell: {where}")

    lines.append(f"_gestartet von <@{run.started_by}> · alle "
                 f"{POLL_S / 60:.0f} min aktualisiert_")
    return "\n".join(lines)[:LIMIT]


def terminal_notice(run: WatchedRun, job: Any, *,
                    now: Optional[float] = None) -> str:
    """The message a finished run earns on its own.

    The board is edited in place, so by the time a run ends its message may be
    hours up the channel. A result nobody scrolls back to is not a result.
    """
    status = _status_of(job)
    job_id = getattr(job, "id", "") or run.job_id
    held = hours(wall_s(job, now=now))
    if status == "failed":
        error = (getattr(job, "error", None) or "ohne Begründung im Job-Record")
        return (f"❌ Training **{job_id}** ist nach {held} fehlgeschlagen — "
                f"{error[:300]}\nDetails auf der Tafel oben, Log mit "
                f"`/atr_log job_id:{job_id}`.")
    if status == "cancelled":
        return (f"⏹️ Training **{job_id}** wurde nach {held} abgebrochen "
                f"(angefragt von <@{run.started_by}>).")
    where = getattr(job, "model_path", None) or getattr(job, "model_id", "") or "?"
    return (f"✅ Training **{job_id}** fertig nach {held} — CER "
            f"{_pct(getattr(job, 'cer', None))}, WER "
            f"{_pct(getattr(job, 'wer', None))}, Modell `{where}`.")


# ── the two things that are not a poll result ────────────────────────────────

def note_unreachable(run: WatchedRun, now: float, *,
                     grace_s: float = UNREACHABLE_GRACE_S
                     ) -> tuple[str, WatchedRun]:
    """Call when the gateway did not answer about this run.

    Announces the silence once, after ``grace_s``, and keeps the run either way.
    A run is never dropped for a failed poll: that is the one way to lose a
    training run, and the registry exists so that cannot happen.
    """
    started = run.unreachable_since or now
    held = now - started
    if run.unreachable_told or held < grace_s:
        return "", replace(run, unreachable_since=started)
    last = run.last_status or "unbekannt"
    return (f"⚠️ Der Trainer antwortet seit {hours(held)} nicht mehr über Lauf "
            f"**{run.job_id}** — zuletzt gesehen: `{last}`. Ob er noch läuft, "
            f"sagt nur die Box selbst.",
            replace(run, unreachable_since=started, unreachable_told=True))


def note_reachable(run: WatchedRun, now: float) -> tuple[str, WatchedRun]:
    """Call on an answer. Closes an announced silence, once."""
    if run.unreachable_since is None:
        return "", run
    held = now - run.unreachable_since
    clean = replace(run, unreachable_since=None, unreachable_told=False)
    if not run.unreachable_told:
        return "", clean
    return (f"Der Trainer antwortet wieder über Lauf **{run.job_id}**, nach "
            f"{hours(held)}.", clean)


def note_missing(run: WatchedRun, now: float, *,
                 grace_s: float = FORGOTTEN_GRACE_S
                 ) -> tuple[str, WatchedRun, bool]:
    """Call when the gateway answered and the trainer has no such job (404).

    ``(notice, run, drop)``. Different from silence on purpose: the gateway is
    talking, so this is an answer and not an outage. It is still given a grace
    period, because a trainer restarted mid-request answers 404 for a moment,
    and dropping a live run over that would be the very loss this registry is
    for. After the grace the run is given up on, with a message — a registry
    entry nobody will ever get an answer about is polled for ever otherwise.
    """
    started = run.missing_since or now
    held = now - started
    if held < grace_s:
        return "", replace(run, missing_since=started), False
    return (f"❓ Der Trainer kennt den Lauf **{run.job_id}** seit {hours(held)} "
            f"nicht mehr — er wird nicht weiter beobachtet. Zuletzt gesehen: "
            f"`{run.last_status or 'unbekannt'}`.",
            replace(run, missing_since=started), True)


# ── /train_list ──────────────────────────────────────────────────────────────

def format_list(jobs: list, limit: int = 10) -> str:
    """Recent runs, newest first, with status and CER where there is one.

    *jobs* are ``TrainJob`` objects. Sorted here rather than trusted: the
    gateway documents "newest first", and a list view that quietly depends on
    that is a list view that reorders itself the day the trainer changes.
    """
    if not jobs:
        return "Keine Trainingsläufe."
    def _key(job: Any) -> tuple[int, float]:
        when = _epoch(getattr(job, "created_at", None))
        # A job without a date sorts last in either direction, rather than
        # jumping to the top the way a zero would.
        return (0, 0.0) if when is None else (1, when)

    ordered = sorted(jobs, key=_key, reverse=True)
    rows = [f"{'status':<11} {'stufe':<10} {'CER':>8}  id"]
    for job in ordered[:limit]:
        cer = getattr(job, "cer", None)
        rows.append("{:<11} {:<10} {:>8}  {}".format(
            _status_of(job)[:11],
            str(getattr(job, "stage", None) or "—")[:10],
            # "—" and "0.00 %" are different answers: the first says no test ran,
            # the second says one did and was perfect.
            "—" if cer is None else f"{cer * 100:.2f}%",
            getattr(job, "id", "?") or "?"))
    out = "```\n" + "\n".join(rows) + "\n```"
    if len(ordered) > limit:
        out += f"\n({len(ordered)} insgesamt)"
    return out
