"""Know whether the engines a run needs are up — and get them back if not.

The `missiven` run of 2026-10-09 sent **16 of its 20 recognitions** at a service
that was not running:

    Kraken service 502 at http://130.92.59.240:8200/ocr:
      {"detail":"kraken engine unreachable at http://127.0.0.1:8201/recognize:
       All connection attempts failed"}

The gateway answered. The engine behind it did not. The run tried every planned
reading one at a time, lost every one of them one at a time, and published what
was left (#595 is the other half of that: a page that does not say how many of
its planned readings it was made from).

Two things were missing, and both are here.

**Nobody asked.** The gateway's ``/health`` has said which engines answer since
serving-atr-inference#30. Reading it before the first image turns sixteen lost
recognitions into one line.

**Nobody could do anything.** The repair step the infrastructure docs name is
``systemctl --user restart atr-kraken`` **on idhefix**, and from this host that is
unreachable: the documented way in is one port, ``:8200``, with ``X-API-Key``,
over the VPN. serving-atr-inference#209 puts the restart behind that port; this
module is the side that decides when to ask for it, and what the answer means.

Three rules carry the weight.

**``busy`` is not ``down``.** The gateway tells them apart and paid for the
distinction: on 17.09.2026 ``party`` answered nothing for the length of every
page it read (30–80 s), so each probe timed out and a working engine was
reported as ``reachable: false``. Since then a read timeout is ``busy: true`` —
alive and working — and only a refused connection is down. A process that
restarts on a read timeout kills a live recognition mid-page, and taking 80 s
over a page is the normal case in this work, not the exception.

**Unmeasured is not down.** ``reachable`` is ``True``, ``False`` or ``None``, and
``None`` means ``/health`` did not say. Only a measured ``False`` blocks a run.
This is not pedantry: vLLM is *never* in ``/health``'s engine list — the gateway
spawns it as subprocesses and tracks it through ``resident_models`` — so a
preflight that read silence as absence would refuse every run ever started.

**Restarted is not running.** A restart that was accepted is not an engine that
answers. The outcome says which of the two was measured, and ``unknown`` — a
transport error, where it is genuinely not known whether anything happened — is
its own value rather than a failure, because a process that reads it as failure
asks again, and asking again is how one restart becomes five.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import config

__all__ = [
    "RESTARTABLE", "PLANNED_ENGINES", "RESTARTED", "STILL_DOWN", "UNSUPPORTED",
    "REFUSED", "BUSY", "FAILED", "UNKNOWN", "COOLDOWN_S", "TIMEOUT_S",
    "HEALTH_TIMEOUT_S", "EngineState", "Preflight", "RestartOutcome", "Ledger",
    "read_health", "plan_engines", "preflight", "restart", "health", "check",
    "check_sync", "recover", "recover_sync", "format_preflight",
    "format_restart",
]

#: The engines that have a systemd unit, and so can be restarted at all.
#: ``vlm`` is deliberately absent: the gateway spawns vLLM as subprocesses of its
#: own process (one per resident model), there is no unit to restart, and a
#: command that offered to restart it would be offering something that cannot be
#: done.
RESTARTABLE = ("kraken", "trocr", "party")

#: What a run asks for when nothing narrower is known. ``ensemble.plan_models``
#: builds every plan from exactly these three — VLM, the best kraken, the best
#: TrOCR — and a batch does not know its documents' criteria before it reads
#: them, so this is the honest answer to "what will this run need". Not a list
#: kept in step by hand: ``tests/test_ah_599_engine_restart.py`` reads the
#: engines ``ensemble.plan_models`` can emit out of its source and fails if they
#: ever differ from this.
PLANNED_ENGINES = ("vlm", "kraken", "trocr")

#: Outcomes of a restart request. Five of the six are failures of different
#: kinds, and the differences are what a caller needs:
RESTARTED = "restarted"      #: it was restarted and /health answers again
STILL_DOWN = "still_down"    #: it was restarted and /health still does not answer
UNSUPPORTED = "unsupported"  #: this gateway has no restart route (404/405)
REFUSED = "refused"          #: 401/403 — the key may not do this
BUSY = "busy"                #: 409 — it is working; nothing was restarted
FAILED = "failed"            #: the gateway tried and said why not
UNKNOWN = "unknown"          #: transport error: it is not known what happened

#: How long before the same engine may be restarted again. One restart per
#: engine per run is the rule; this is what makes "per run" meaningful for a
#: long run, and what keeps an engine in a crash loop from being restarted once
#: per page.
COOLDOWN_S = 900.0

#: How long to wait for ``/health``. Short: it probes each engine with a 5 s
#: budget of its own and this is the gate in front of a run, not the run.
HEALTH_TIMEOUT_S = 20.0

#: One restart request, including the gateway's own verification wait.
#: serving-atr-inference#209 probes for up to 15 s after the restart, so this
#: must leave room for that plus the call itself.
TIMEOUT_S = 45.0


# ── what /health says ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EngineState:
    """One engine, as ``/health`` described it.

    ``reachable`` and ``busy`` are three-valued on purpose; see the module
    docstring. ``None`` is "the answer did not say", which is a different fact
    from ``False`` and must not be rounded into it.
    """

    name: str
    url: str = ""
    reachable: Optional[bool] = None
    busy: Optional[bool] = None

    @property
    def down(self) -> bool:
        """Measured down: it refused the connection and is not merely slow.

        A busy engine is up — it accepted the connection and is working. An
        unmeasured engine is not down; it is unmeasured.
        """
        return self.reachable is False and not self.busy

    @property
    def working(self) -> bool:
        """Answering, or answering slowly. Both are up."""
        return self.reachable is True


def read_health(payload: Any) -> dict[str, EngineState]:
    """``/health``'s ``engines`` list, keyed by name.

    Tolerant by design. A gateway that answers something unexpected should cost
    a caller a thinner answer, not an exception on the preflight path — the
    whole point of this module is to be the thing that does not fall over when
    the far side is in a bad way. A payload with no ``engines`` key yields an
    empty map, which reads as "nothing was measured" everywhere downstream.
    """
    if not isinstance(payload, dict):
        return {}
    out: dict[str, EngineState] = {}
    for entry in payload.get("engines") or ():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        reachable = entry.get("reachable")
        busy = entry.get("busy")
        out[name] = EngineState(
            name=name,
            url=str(entry.get("url") or ""),
            reachable=reachable if isinstance(reachable, bool) else None,
            busy=busy if isinstance(busy, bool) else None,
        )
    return out


def plan_engines(picks: Iterable[Any]) -> tuple[str, ...]:
    """The distinct engines a model plan will call, in the order it will call them.

    Takes ``ensemble.plan_models``' picks (anything with an ``engine``, or a dict
    carrying one), because the plan is the only honest answer to "what does this
    run need": it is what the run will actually ask for, rather than a list
    somebody kept in step by hand.
    """
    seen: list[str] = []
    for pick in picks or ():
        engine = pick.get("engine") if isinstance(pick, dict) else getattr(pick, "engine", None)
        engine = str(engine or "").strip().lower()
        if engine and engine not in seen:
            seen.append(engine)
    return tuple(seen)


# ── the preflight ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Preflight:
    """What a run can expect from the engines before it reads its first image."""

    needed: tuple[str, ...] = ()
    #: Needed and measured down. These are what a run loses.
    down: tuple[str, ...] = ()
    #: Needed and not in ``/health`` at all. **Not** a problem: vLLM is never
    #: there, and a gateway too old to list an engine is not a gateway whose
    #: engine is off. Reported so the reader knows the answer is partial.
    unmeasured: tuple[str, ...] = ()
    #: Down and without a unit — nothing here can bring them back.
    unrestartable: tuple[str, ...] = ()
    #: The gateway itself could not be asked. A separate field from ``down``
    #: because it is a separate fact with a separate remedy, and because
    #: reporting it as "every engine is down" would send the reader to idhefix
    #: to look at engines that are, as far as anyone knows, fine.
    gateway_error: str = ""

    @property
    def ok(self) -> bool:
        """Whether every needed engine that was measured answered.

        A gateway that could not be asked is not ok either: nothing recognises
        anything without it, so a run would fail on its first page regardless.
        """
        return not self.down and not self.gateway_error

    @property
    def fixable(self) -> tuple[str, ...]:
        """The down engines a restart could plausibly help."""
        return tuple(e for e in self.down if e in RESTARTABLE)


def _is_engine_map(health: Any) -> bool:
    """Whether ``health`` is already a ``read_health`` result rather than a payload."""
    return (isinstance(health, dict) and bool(health)
            and all(isinstance(v, EngineState) for v in health.values()))


def preflight(health: Any, needed: Iterable[str]) -> Preflight:
    """Compare what the run needs against what ``/health`` reports.

    ``health`` is the payload or an already-read map, so a caller that read
    ``/health`` for its own reasons does not read it twice.
    """
    engines = health if _is_engine_map(health) else read_health(health)

    want = tuple(dict.fromkeys(str(n).strip().lower() for n in needed or () if str(n).strip()))
    down = tuple(n for n in want if n in engines and engines[n].down)
    unmeasured = tuple(n for n in want if n not in engines)
    return Preflight(
        needed=want,
        down=down,
        unmeasured=unmeasured,
        unrestartable=tuple(n for n in down if n not in RESTARTABLE),
    )


# ── the restart ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RestartOutcome:
    """What came back. ``reachable`` is the measurement, ``outcome`` the verdict."""

    engine: str
    outcome: str
    detail: str = ""
    reachable: Optional[bool] = None

    @property
    def ok(self) -> bool:
        """Only one outcome means the engine serves again."""
        return self.outcome == RESTARTED


@dataclass
class Ledger:
    """One restart per engine per cooldown. Pure; the clock is a parameter.

    The second failure of the same engine is a report, not another attempt. An
    engine that cannot stay up is a thing to be told about, and a process that
    keeps restarting it is a process that hides it.
    """

    cooldown_s: float = COOLDOWN_S
    at: dict[str, float] = field(default_factory=dict)

    def may(self, engine: str, now: float) -> bool:
        last = self.at.get(engine)
        return last is None or (now - last) >= self.cooldown_s

    def note(self, engine: str, now: float) -> None:
        self.at[engine] = now

    def skipped(self, engine: str, now: float) -> str:
        """Why it was not tried again, in words a reader can act on."""
        last = self.at.get(engine, now)
        waited = max(0.0, now - last)
        return (f"{engine} wurde vor {waited / 60:.0f} min schon neu gestartet und "
                f"ist wieder weg — das ist ein Problem der Engine, nicht eines "
                f"fehlenden Neustarts")


async def restart(engine: str, *, timeout_s: float = TIMEOUT_S) -> RestartOutcome:
    """Ask the gateway to restart one engine (serving-atr-inference#209).

    Every status the route can answer maps to its own outcome, because a caller
    that cannot tell "this gateway has no such route" from "the restart failed"
    will report the wrong thing to whoever has to fix it.
    """
    import httpx

    if engine not in RESTARTABLE:
        return RestartOutcome(
            engine=engine, outcome=UNSUPPORTED,
            detail=f"{engine} hat keine systemd-Unit — "
                   f"neustartbar sind {', '.join(RESTARTABLE)}",
        )

    url = f"{config.ATR_GATEWAY_URL}/engines/{engine}/restart"
    headers = {"X-API-Key": config.ATR_API_KEY} if config.ATR_API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.post(url, headers=headers)
    except httpx.HTTPError as exc:
        # Not FAILED. A request that died in transit may well have restarted the
        # engine, and a caller that books this as a failure asks again.
        return RestartOutcome(
            engine=engine, outcome=UNKNOWN,
            detail=f"{type(exc).__name__} an {url} — es ist nicht bekannt, "
                   f"ob der Neustart ausgeführt wurde",
        )

    status = response.status_code
    if status in (404, 405):
        return RestartOutcome(
            engine=engine, outcome=UNSUPPORTED,
            detail=f"dieses Gateway kennt {url} nicht ({status}) — "
                   f"serving-atr-inference#209 ist dort noch nicht ausgerollt",
        )
    if status in (401, 403):
        return RestartOutcome(engine=engine, outcome=REFUSED,
                              detail=f"der API-Schlüssel darf das nicht ({status})")
    if status == 409:
        return RestartOutcome(
            engine=engine, outcome=BUSY,
            detail=f"{engine} arbeitet gerade — es wurde nichts neu gestartet",
        )
    if status >= 400:
        return RestartOutcome(engine=engine, outcome=FAILED,
                              detail=f"{url} antwortete {status}: {response.text[:300]}")

    try:
        body = response.json()
    except ValueError:
        body = {}
    reachable = body.get("reachable") if isinstance(body, dict) else None
    reachable = reachable if isinstance(reachable, bool) else None
    detail = str((body or {}).get("detail") or "")
    return RestartOutcome(
        engine=engine,
        outcome=RESTARTED if reachable else STILL_DOWN,
        detail=detail,
        reachable=reachable,
    )


# ── saying it ────────────────────────────────────────────────────────────────

def format_preflight(result: Preflight) -> str:
    """One line a run can print before it starts, or several when something is off."""
    if result.gateway_error:
        # Its own branch, and the first one. Without it a gateway nobody could
        # ask renders as "  antworten nicht" — an empty list of down engines
        # read as a list — and sends the reader to idhefix to inspect engines
        # that are, as far as anyone knows, fine.
        return (f"❌ Engine-Preflight: das Gateway konnte nicht gefragt werden "
                f"({result.gateway_error}).\n"
                f"   Damit ist unbekannt, welche Engines laufen — und ohne "
                f"Gateway erkennt der Lauf ohnehin nichts.\n"
                f"   Von diesem Host aus geht es nur über das VPN.")
    if not result.needed:
        return "Engine-Preflight: der Lauf nennt keine Engines — nichts zu prüfen."
    if result.ok:
        line = f"Engine-Preflight ok: {', '.join(result.needed)}"
        if result.unmeasured:
            # Said, not hidden: vLLM is never in /health, so this is the normal
            # case, and a reader who does not know that would otherwise read the
            # silence as a check that happened.
            line += (f" (ungeprüft, weil /health sie nicht nennt: "
                     f"{', '.join(result.unmeasured)})")
        return line + "."

    lines = [f"❌ Engine-Preflight: {', '.join(result.down)} "
             f"{'antwortet' if len(result.down) == 1 else 'antworten'} nicht."]
    if result.fixable:
        lines.append(f"   Behebbar mit einem Neustart: {', '.join(result.fixable)} "
                     f"(`/atr_restart`, oder `--restart-engines` am Lauf).")
    if result.unrestartable:
        lines.append(f"   Ohne systemd-Unit, also von hier nicht behebbar: "
                     f"{', '.join(result.unrestartable)}.")
    if result.unmeasured:
        lines.append(f"   Ungeprüft, weil /health sie nicht nennt: "
                     f"{', '.join(result.unmeasured)}.")
    lines.append("   Ein Lauf gegen eine tote Engine verliert jede Lesung, "
                 "die sie betrifft, einzeln (#599).")
    return "\n".join(lines)


def format_restart(outcome: RestartOutcome) -> str:
    """What to tell whoever asked. The verdict first, then what was measured."""
    heads = {
        RESTARTED: f"✅ {outcome.engine} wurde neu gestartet und antwortet wieder",
        STILL_DOWN: f"⚠️ {outcome.engine} wurde neu gestartet, antwortet aber noch nicht",
        UNSUPPORTED: f"❌ {outcome.engine} kann so nicht neu gestartet werden",
        REFUSED: f"⛔ Der Neustart von {outcome.engine} wurde abgelehnt",
        BUSY: f"⏳ {outcome.engine} arbeitet — nicht neu gestartet",
        FAILED: f"❌ Der Neustart von {outcome.engine} ist fehlgeschlagen",
        UNKNOWN: f"❓ Unklar, ob {outcome.engine} neu gestartet wurde",
    }
    head = heads.get(outcome.outcome, f"{outcome.engine}: {outcome.outcome}")
    if outcome.outcome == STILL_DOWN and not outcome.detail:
        return (f"{head} — ein Modell-Kaltstart kann länger dauern als das "
                f"Prüffenster; /health erneut abfragen.")
    return f"{head}{(' — ' + outcome.detail) if outcome.detail else ''}."


# ── asking the gateway ───────────────────────────────────────────────────────

async def health(*, timeout_s: float = HEALTH_TIMEOUT_S) -> tuple[dict, str]:
    """``GET /health`` → (payload, error). Never raises.

    ``/health`` is the one route the gateway serves without a key, so this works
    even where a wrong key would break everything else — which is exactly the
    situation in which someone wants to know what is up.

    Two return values rather than an exception because both halves are useful at
    once: a payload that arrived, and a reason it did not. A caller that gets
    ``({}, "...")`` must not read the empty map as "no engines are down".
    """
    import httpx

    url = f"{config.ATR_GATEWAY_URL}/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        # Name the URL: this host reaches the gateway only over the VPN, and
        # "connection failed" without an address sends the reader to the wrong box.
        return {}, f"{type(exc).__name__} an {url}"
    if response.status_code >= 400:
        return {}, f"{url} antwortete {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return {}, f"{url} antwortete kein JSON"
    return (payload if isinstance(payload, dict) else {}), ""


async def check(needed: Iterable[str] | None = None, *,
                timeout_s: float = HEALTH_TIMEOUT_S) -> Preflight:
    """Read ``/health`` and compare it with what the run needs."""
    want = tuple(needed) if needed is not None else PLANNED_ENGINES
    payload, error = await health(timeout_s=timeout_s)
    if error:
        return Preflight(needed=tuple(want), gateway_error=error)
    return preflight(payload, want)


def check_sync(needed: Iterable[str] | None = None, *,
               timeout_s: float = HEALTH_TIMEOUT_S) -> Preflight:
    """:func:`check` for the batch runner, which is threads and not a loop."""
    import asyncio
    return asyncio.run(check(needed, timeout_s=timeout_s))


async def recover(result: Preflight, ledger: Ledger, now: float) -> list[RestartOutcome]:
    """Restart what a restart can plausibly fix, at most once per engine.

    Returns one outcome per engine considered, including the ones it declined to
    try — a skipped engine is a thing to report, not a silence. An engine with
    no unit is never attempted: there is nothing to restart, and an attempt
    would answer a question nobody asked with a 404.
    """
    outcomes: list[RestartOutcome] = []
    for engine in result.fixable:
        if not ledger.may(engine, now):
            outcomes.append(RestartOutcome(engine=engine, outcome=FAILED,
                                           detail=ledger.skipped(engine, now)))
            continue
        ledger.note(engine, now)
        outcomes.append(await restart(engine))
    return outcomes


def recover_sync(result: Preflight, ledger: Ledger, now: float) -> list[RestartOutcome]:
    """:func:`recover` from a thread."""
    import asyncio
    return asyncio.run(recover(result, ledger, now))
