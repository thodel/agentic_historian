"""Confirmations that outlive the process they were asked in.

`/update` posts a Confirm button and, on confirm, **restarts the bot** — that is
how #245 designed it: write a marker, `sys.exit(0)`, let systemd bring the
process back on the new code. So a restart is not an accident in this routine,
it is the routine.

The button did not survive one. `_ConfirmView` was built with a 300 s timeout,
which makes it a *non-persistent* view: py-cord keeps it only in the sending
process's memory, under `(component_type, message_id, custom_id)`, and a restart
deletes it. `client.add_view` could not even put it back — that method requires a
persistent view and says so ("Raises ValueError: The view is not persistent. A
persistent view has no timeout"). The pending record was an in-memory `dict`
besides, so even a routable button would have found no token.

Measured on 2026-10-10 21:15 and 21:16: two clicks, both answered by Discord with
*"This component is no longer valid."* — a string that appears nowhere in
py-cord, so it is Discord saying "the application has no handler for this".

**#150 solved exactly this for the gate cards** and wrote down why: *"The bot
restarts (deploys, crashes); a routing card whose buttons die on restart teaches
users to ignore the cards."* Stable `custom_id`s, `timeout=None`, and
`register_persistent_views` binding them back on startup. It was never applied to
the command that causes the restart.

This module is the other half of that pattern: the record a rebound view needs.
Deliberately not in `bot.py` and deliberately without Discord in it, so "is a
five-minute-old confirmation still valid after a restart" is a question a unit
test answers.

**The deadline moves to where it works.** With `timeout=None` the view no longer
expires, so the callback checks the age. That is the right place regardless: a
deadline enforced by a timer in one process is not a deadline, it is a property
of that process's lifetime — and this process exits on purpose.

Nothing secret is stored. The token is an HMAC over a timestamp and identifies a
pending action, nothing more; the channel, message and requester are already
visible in the message the button sits on.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

import config

__all__ = ["TTL_S", "LIVE", "EXPIRED", "MISSING", "Pending", "path", "load",
           "save", "add", "drop", "lookup"]

#: How long a confirmation stays valid. Was the view's timeout; now the record's
#: age, checked server-side.
TTL_S = 300.0

#: What :func:`lookup` found. Three values, because the three need different
#: sentences: a live record proceeds, an expired one says how old it was, and an
#: unknown one is a button from a build or a record that is gone. Collapsing the
#: last two into "expired" would tell somebody to wait when they should re-run.
LIVE, EXPIRED, MISSING = "live", "expired", "missing"


@dataclass
class Pending:
    """One confirmation waiting for a click."""

    token: str
    #: Which flow asked. One file holds them all, and a rebound view has to know
    #: which class to rebuild.
    kind: str
    channel_id: str
    message_id: str
    requester: str
    created_at: float = field(default_factory=time.time)
    #: Flow-specific payload — the target SHA for an update, the source for an
    #: MCP proposal. Opaque here on purpose: this module stores confirmations
    #: and must not grow a branch per flow.
    data: dict = field(default_factory=dict)

    def age_s(self, now: Optional[float] = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.created_at)

    def expired(self, now: Optional[float] = None, ttl_s: float = TTL_S) -> bool:
        return self.age_s(now) > ttl_s


def path() -> Path:
    """Beside the update marker, which is the same kind of fact (#245)."""
    return config.DATA_DIR / ".pending-confirms.json"


def load() -> list[Pending]:
    """Every stored confirmation, newest last. Never raises.

    An unreadable file yields nothing rather than an exception: this is read on
    `on_ready`, and a bot that refuses to start because a confirmation file is
    corrupt has turned a cosmetic problem into an outage.
    """
    p = path()
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:                        # noqa: BLE001
        logger.warning(f"[confirm] unreadable {p.name}: {exc}")
        return []
    out: list[Pending] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(Pending(**entry))
        except TypeError as exc:                    # a field this build does not know
            logger.warning(f"[confirm] skipping entry: {exc}")
    return out


def save(entries: list) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps([asdict(e) for e in entries], ensure_ascii=False)
    # Through the locked atomic writer, like RunState (#392): two confirmations
    # posted at once must not leave a half-written file that `load` then skips.
    try:
        from shared_lock import write_json_atomic
        write_json_atomic(p, json.loads(payload))
    except Exception:                               # pragma: no cover — fallback
        p.write_text(payload, encoding="utf-8")


def add(pending: Pending, *, ttl_s: float = TTL_S, now: Optional[float] = None) -> None:
    """Store one, dropping anything already expired.

    Pruning on write rather than on a schedule: the file is only read at startup
    and only written when somebody asks for a confirmation, so this is the one
    moment it is in hand and the cheapest place to keep it small.
    """
    kept = [e for e in load() if not e.expired(now, ttl_s) and e.token != pending.token]
    kept.append(pending)
    save(kept)


def drop(token: str) -> None:
    """Forget one — it was confirmed, cancelled, or acted on."""
    save([e for e in load() if e.token != token])


def lookup(token: str, *, now: Optional[float] = None,
           ttl_s: float = TTL_S) -> tuple[str, Optional[Pending]]:
    """``(status, pending)`` — ``LIVE``, ``EXPIRED`` or ``MISSING``.

    The expired record is returned with its status so the answer can say how old
    it was. "Expired" without a number is the kind of message that gets read as
    "broken".
    """
    for entry in load():
        if entry.token == token:
            if entry.expired(now, ttl_s):
                return EXPIRED, entry
            return LIVE, entry
    return MISSING, None
