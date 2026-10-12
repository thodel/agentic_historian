"""Two operator commands for a run in flight: read its log, stop it (#364).

T4 of the training epic. `/train_log` and `/train_cancel` are the two things an
operator does to a run that is already going, and both have a failure mode that
matters more than the happy path:

**A log tail cut at the wrong end is worse than no log.** Discord stops at 2000
characters. A traceback is at the *end* of a log, so a cut that keeps the
beginning throws away the only part anybody opened the command for. The cut is
marked where it happened, with how many lines were dropped — an unmarked tail
reads as the whole log, and then the missing line is the one being looked for.

**A 404 and a 409 each mean two different things on these routes.** The trainer
answers 404 both for an unknown job and for a stage that has no log yet; and 409
both for a job that is *already over* and for one that is *being started this
instant* (``_cancel_unstarted`` holds a claim and refuses with "ask again in a
few seconds"). Told the wrong one, an operator stops waiting for a run that is
about to start, or waits for one that ended hours ago.

So neither is read off the detail text. After a refusal the **job record** is
fetched and asked, because the record is the authority and a message is not:
`stage_hint` and `conflict` below both take the job, not the string.

Everything here is pure. The socket, the clock and the Discord message are in
`bot.py`; this decides what the answer says, so "does a 10 000-line log fit in a
message, and which end survives" is a question a unit test answers.

Run: pytest agentic_historian/tests/test_ah_364_train_ops.py -v
"""

from __future__ import annotations

from typing import Any, Optional

__all__ = [
    "STAGES", "LOG_LINES_DEFAULT", "LOG_LINES_MAX", "LIMIT",
    "log_tail", "unknown_stage", "stage_hint", "cancel_result", "conflict",
    "status_of",
]

#: The five stages, in pipeline order — the trainer's ``JobStage`` literal.
STAGES = ("prepare", "compile", "train", "test", "register")

#: Lines requested by default. The trainer allows 1–5000 and defaults to 200;
#: 200 lines of a kraken log do not fit in a Discord message anyway, so asking
#: for fewer saves the round trip's bytes rather than the reader's attention.
LOG_LINES_DEFAULT = 80

#: The most a caller may ask for. The trainer's own ceiling is 5000; past a few
#: hundred the answer is cut here regardless, and `/atr_log` is not the way to
#: read a whole training log.
LOG_LINES_MAX = 500

#: Discord hard-limits a message to 2000 characters; the fence and the cut
#: notice need the rest.
LIMIT = 1800


def log_tail(lines: Any, *, limit: int = LIMIT) -> str:
    """The end of a log, in a code block, with any cut marked.

    Keeps the **last** lines, not the first. A log is read from the end: the
    traceback, the OOM, the last step reached. ``atr_status._fence`` cuts the
    other way — it keeps ``body[:limit]`` — which is right for a table and wrong
    for a log, and the difference only shows on a log long enough to be cut,
    which is exactly the interesting one.

    Whole lines are dropped, never half of one: a line cut in the middle looks
    like a line the trainer wrote that way.
    """
    rows = _rows(lines)
    if not rows:
        return ""
    kept: list[str] = []
    size = 0
    for row in reversed(rows):
        size += len(row) + 1
        if kept and size > limit:
            break
        kept.append(row)
    kept.reverse()
    body = "\n".join(kept)
    dropped = len(rows) - len(kept)
    head = (f"… (vorne gekürzt, {dropped} von {len(rows)} Zeilen weggelassen)\n"
            if dropped else "")
    # One last cut for the pathological case: a single line longer than the
    # whole budget. Its end survives, because that is where the error is.
    if len(body) > limit:
        body = "…" + body[-(limit - 1):]
    return f"```\n{head}{body}\n```"


def _rows(lines: Any) -> list[str]:
    """The log as a list of lines, whatever shape it arrived in.

    The trainer sends ``{"lines": [...]}``; a string is split. A dict or a
    number yields nothing rather than its characters — the mistake #595 made
    with ``recognitions``, where a string was counted one reading per letter.
    """
    if isinstance(lines, str):
        return lines.splitlines()
    if isinstance(lines, (list, tuple)):
        return [str(row).rstrip("\n") for row in lines]
    return []


def unknown_stage(stage: str) -> str:
    """A stage name that is not one of the five. Lists them.

    Answered here, without asking the gateway: the five are a literal in the
    trainer's contract, so a wrong name is known to be wrong before the request
    — and the answer that helps is the list, not "404".
    """
    return (f"❌ `{stage}` ist keine Stufe. Es gibt fünf, in dieser Reihenfolge: "
            + " → ".join(f"`{s}`" for s in STAGES) + ".")


def stage_hint(stage: str, job: Any) -> str:
    """No log for *stage* on a job that exists — so say what the job does have.

    The trainer's 404 reads "no train log for job X", which is true and tells
    nobody what to ask for instead. The job record knows which stages ran, so
    this names them. A job still queued has none, and that is its own sentence:
    "not yet" and "never will be" are different answers.
    """
    stages = _stage_status(job)
    ran = [s for s in STAGES if stages.get(s) not in (None, "pending")]
    job_id = getattr(job, "id", "") or "?"
    status = status_of(job)
    if not ran:
        return (f"Für **{job_id}** gibt es noch kein `{stage}`-Log — der Lauf ist "
                f"`{status}` und hat noch keine Stufe begonnen.")
    return (f"Für **{job_id}** gibt es kein `{stage}`-Log. Begonnen haben: "
            + ", ".join(f"`{s}` ({stages[s]})" for s in ran)
            + f". Der Lauf ist `{status}`.")


def cancel_result(job: Any) -> str:
    """What the cancel actually did, read off the record it returned.

    The trainer SIGTERMs the process group and the **runner** marks the job
    cancelled, so the record that comes back can still say `training` — the
    signal is away, the status has not caught up. Saying "cancelled" then would
    be a claim about something that had not happened yet; the watcher reports
    the terminal state when it arrives (#363).
    """
    job_id = getattr(job, "id", "") or "?"
    status = status_of(job)
    if status == "cancelled":
        error = getattr(job, "error", None) or ""
        extra = f" ({error})" if error else ""
        return f"⏹️ **{job_id}** ist abgebrochen{extra}."
    if status in ("completed", "failed"):
        # It finished between the click and the call. Rare, and still the truth.
        return (f"**{job_id}** ist in der Zwischenzeit `{status}` geworden — "
                f"es wurde nichts abgebrochen.")
    return (f"⏹️ Abbruch für **{job_id}** ist abgeschickt (SIGTERM an die "
            f"Prozessgruppe); der Lauf steht noch auf `{status}`. Den "
            f"Endzustand meldet der Watcher, sobald der Runner ihn schreibt.")


def conflict(job_id: str, job: Any, detail: str = "") -> str:
    """A 409 from the cancel route, told apart by the job record.

    The route refuses with 409 for two opposite reasons: the job is **already
    terminal**, or it is **being started this very moment** and the claim is
    held. The detail text differs, but reading it would make this depend on the
    trainer's wording; the record says which it is, and that is the authority.

    ``job is None`` means the record could not be fetched either — then the
    trainer's own detail is passed on rather than guessed at.
    """
    if job is None:
        return (f"❌ Abbruch für **{job_id}** abgelehnt: {detail or 'Konflikt'}")
    status = status_of(job)
    if _terminal(job):
        return (f"**{job_id}** ist schon `{status}` — es gab nichts "
                f"abzubrechen.")
    return (f"**{job_id}** wird in diesem Moment gestartet, deshalb der "
            f"Konflikt. In ein paar Sekunden nochmal: dann hat der Lauf einen "
            f"Runner, und der Abbruch stoppt ihn.")


# ── reading a job record without caring how it was built ─────────────────────

def status_of(job: Any) -> str:
    """The job's status as a plain word, whatever shape the record arrived in."""
    status = getattr(job, "status", None)
    raw = getattr(job, "status_raw", "") or ""
    value = getattr(status, "value", status)
    if value in (None, "unknown") and raw:
        return raw
    return str(value or "?")


def _terminal(job: Any) -> bool:
    checker = getattr(job, "is_terminal", None)
    if callable(checker):
        return bool(checker())
    return status_of(job) in ("completed", "failed", "cancelled")


def _stage_status(job: Any) -> dict[str, str]:
    """stage name → that stage's status, from a ``TrainJob``."""
    out: dict[str, str] = {}
    for stage, status in (getattr(job, "stages", None) or {}).items():
        out[str(getattr(stage, "value", stage))] = str(status)
    current: Optional[str] = getattr(job, "stage", None)
    if current in STAGES:
        out.setdefault(str(current), "running")
    return out
