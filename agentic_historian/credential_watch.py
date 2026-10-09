"""
credential_watch.py — say that the mailbox credentials broke, don't wait to be asked (#589).

The SwitchDrive 401 on 2026-10-09 cost two hours and presented itself as a
mystery about a folder, because **nothing was watching the endpoint**. Same gap
#418 closed for the training server, and the same sentence applies: neither
`/pull` nor `/pull_preflight` is a question somebody asks hourly, and an event
nobody is told about is found late by definition.

Everything here is a pure function of (what was seen last time, what is seen
now). The bot supplies the polling and the posting; this module decides — so
"does a restart into a dead passcode stay silent?" is a question a unit test
answers rather than one discovered in production.

Three rules, two of them borrowed from ``atr_watch`` and one deliberately not
──────────────────────────────────────────────────────────────────────────────
**Announce a state, not a reading.** A rejected passcode is announced when it
*becomes* rejected, once, and again only when it comes back. The report that
fires every poll is the report nobody reads, which is exactly how a dead
credential stayed invisible while `/pull_preflight` was, in principle, able to
show it.

**A grace period.** One failed request is not a dead passcode:
drive.switch.ch has outages and a VM loses DNS. A message per blip is how a
channel gets muted, and a muted channel costs the messages that matter.

**No silence on first sight** — and this is where it departs from ``atr_watch``,
on purpose. That watcher stays quiet on its first poll because a cold start
would otherwise paste forty historical jobs into the channel. Here there is one
binary state, so there is no flood to prevent, and seeding silently would mean a
bot that restarts into a rejected passcode never mentions it. One message is not
a flood; a hidden outage is the thing this file exists to stop.

The text comes from the probe's own ``action`` (``switchdrive._advise``), so the
channel gets the thing to change rather than a status code — and the fix is
written in one place.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from loguru import logger

__all__ = [
    "Announcement",
    "GRACE_S",
    "WatchState",
    "decide",
    "load_state",
    "save_state",
    "state_path",
]

#: How long a condition must persist before it is announced. Fifteen minutes
#: rides out a restart of the share and a transient DNS failure, and is far
#: shorter than the two hours the unwatched 401 actually cost.
GRACE_S = 900.0


@dataclass(frozen=True)
class Announcement:
    """One thing to say, and enough to say it well."""

    kind: str                  # "blocked" | "recovered"
    layer: str                 # the probe layer: config | network | auth | path
    text: str
    #: Worth pulling somebody out of whatever they are doing. A Discord message
    #: without a mention does not reliably reach a phone, and one that always
    #: mentions gets the channel muted — which costs the quiet messages too. A
    #: rejected credential stops every ingest, so it is urgent; a recovery is
    #: good news and is not.
    urgent: bool = False

    def __str__(self) -> str:
        return self.text


@dataclass
class WatchState:
    """What has already been said. Small, and boring on purpose."""

    #: The layer currently failing, "" when healthy.
    blocked: str = ""
    #: When the current condition was first seen. None when healthy.
    since: Optional[float] = None
    #: Whether the current condition has already been announced.
    told: bool = False

    def to_json(self) -> str:
        return json.dumps({"blocked": self.blocked, "since": self.since,
                           "told": self.told}, ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "WatchState":
        try:
            d = json.loads(text) or {}
        except ValueError:
            return cls()
        since = d.get("since")
        return cls(blocked=str(d.get("blocked") or ""),
                   since=float(since) if since is not None else None,
                   told=bool(d.get("told")))


def state_path() -> Path:
    """Where the watcher's memory lives.

    A function, not a constant: a constant is computed at import and then stops
    reading ``DATA_DIR`` (the ``config.page_cache_dir`` lesson).
    """
    import config
    return config.DATA_DIR / "credential_watch.json"


def load_state(path: Optional[Path] = None) -> WatchState:
    """The remembered state, or a fresh one. An unreadable file costs the
    memory and never the poll."""
    p = path or state_path()
    try:
        return WatchState.from_json(p.read_text(encoding="utf-8"))
    except OSError:
        return WatchState()


def save_state(state: WatchState, path: Optional[Path] = None) -> None:
    p = path or state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(state.to_json(), encoding="utf-8")
    except OSError as e:                         # noqa: BLE001
        logger.warning(f"[credwatch] could not save state: {e}")


def decide(state: WatchState, probe, now: float, *,
           grace_s: float = GRACE_S,
           label: str = "SwitchDrive") -> tuple[Optional[Announcement], WatchState]:
    """What to say now, and the state to remember having said it.

    Returns a *new* state. The caller saves it only **after** the message is
    away, so a crash between deciding and posting repeats a message rather than
    losing one — repeating is recoverable, and losing is the thing this exists
    to prevent (the ``atr_watch`` rule, for the same reason).
    """
    blocked = probe.blocked_at
    layer = blocked.layer if blocked is not None else ""

    if not layer:
        if state.told:
            return (Announcement(
                kind="recovered", layer=state.blocked,
                text=(f"✅ **{label}** antwortet wieder — die Blockade bei "
                      f"`{state.blocked}` ist weg. Ingest läuft.")),
                WatchState())
        return None, WatchState()

    if layer != state.blocked:
        # A different condition than the one being timed: it gets its own grace
        # rather than inheriting the previous one's elapsed time.
        return None, WatchState(blocked=layer, since=now)

    since = state.since if state.since is not None else now
    held = now - since
    if state.told or held < grace_s:
        return None, replace(state, blocked=layer, since=since)

    minutes = int(held // 60)
    text = (f"⚠️ **{label}: blockiert bei `{layer}`** seit {minutes} Minuten — "
            f"{blocked.detail}.\n{blocked.action}\n"
            f"_Vorflug: `/pull_preflight`._")
    return (Announcement(kind="blocked", layer=layer, text=text, urgent=True),
            replace(state, blocked=layer, since=since, told=True))
