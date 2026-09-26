"""Does this model fit on that card right now — and if not, what is in the way.

The answer existed already, as two JSON blocks a human had to lay side by side:
``/gpu`` says what is on the cards, ``/models`` says what a model needs, and
nothing subtracted one from the other. So the result arrived from the vLLM cold
start — ``GPU 1 has 9742 MB free, qwen3.5-4b-german-xix-v2 needs 15848 MB`` —
rather than before it. ``gateway_models`` tipped both reports out unprocessed;
that the wrong card once stood in there went unnoticed for months (#452).

This module does the subtraction. It is V1 of the VRAM-headroom epic (#470) and
the precondition for the runner preflight (V2, #472) and the headroom lease (V4,
#473). It reads the probes that are already taken, adds no endpoint, and has no
side effects: nothing here starts, stops or evicts anything.

## The two numbers, and why they are not one

**What a model needs** is not its ``vram_mb``. The serving gateway refuses a
launch below ``ceil(vram_mb x 1.15) + 2048`` — 1.15 for the KV cache that
``vram_mb`` does not cover, 2048 MiB left to the card — and that threshold, not
the bare weight figure, is what a preflight has to compare against. For
``qwen3.5-4b-german-xix-v2`` with ``vram_mb: 12000`` it comes to exactly the
15848 MiB the failed launch of 2026-09-21 named.

Those constants are **mirrored** from ``serving-atr-inference``'s
``manager.MIN_HEADROOM`` and ``RESERVE_MB``. Mirrored, because this repo cannot
import that one, and named here so the duplication is visible: if the gateway
changes its threshold and this file does not, the preflight becomes a preflight
that lies, which is worse than none. :func:`needed_mib` carries the arithmetic
alone so a drift is one edit and one test, not a search.

**What is in the way** is not one number either. Memory on the card belongs to
somebody, and only some of those somebodies may ever be asked to give it back:

- ``ours`` — processes in a unit starting with ``atr-`` (the gateway and its
  vLLM children, party, trocr, kraken). The only rows V4 may ever clear.
- ``theirs`` — everything else, with the service and the user that owns it. On
  idhefix card 0 that is four ``gunicorn`` workers of the user ``change``
  holding 10 392 MiB between them, untouched for 41 days. They belong in the
  report and are never an option, and a report that summed them with ours would
  invite exactly the mistake V4 must not make.

## Unknown is not the same as fitting

A model with no ``vram_mb``, or with no ``gpu_affinity`` to say which card it
lands on, or a probe that failed, gives ``fits=None``. Not ``True``. A default of
``True`` would be the precise failure this module exists to prevent: a preflight
that waves through the launch it was built to catch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

__all__ = ["Headroom", "Occupant", "gpu_headroom", "headroom_report", "needed_mib"]

#: Mirrored from serving-atr-inference ``manager.MIN_HEADROOM``. Below this vLLM
#: either refuses outright or serves a context too short to hold a page.
MIN_HEADROOM = 1.15

#: Mirrored from serving-atr-inference ``manager.RESERVE_MB``. Left to the card
#: on top of the model's share: the process's own CUDA context, fragmentation,
#: and whatever the small engine services grow into while this one is resident.
RESERVE_MB = 2048

#: Unit-name prefix that marks a process as ours. Same rule the gateway applies
#: when it sets ``own_service`` on a row; recomputed here rather than trusted,
#: so a payload from an older gateway without that field still splits correctly.
OWN_UNIT_PREFIXES = ("atr-",)


def needed_mib(vram_mb: int) -> int:
    """The gateway's own refusal threshold for a model of this size.

    One function, because it is the single place where this repo restates an
    arithmetic that lives in another one. ``12000 -> 15848``, which is the number
    the cold start of 2026-09-21 printed.
    """
    return math.ceil(vram_mb * MIN_HEADROOM) + RESERVE_MB


@dataclass(frozen=True)
class Occupant:
    """One process holding memory on the card, and enough to judge it by."""

    pid: int
    used_mib: int
    service: str | None = None
    user: str | None = None
    age_s: float | None = None
    #: No ``/proc`` entry: the process is gone and its memory is not. The row
    #: worth waking somebody for, and the one case where "ours" and "reclaimable"
    #: come apart — no eviction frees it, only a kill.
    orphaned: bool = False

    @property
    def label(self) -> str:
        who = self.service or ("[orphan]" if self.orphaned else "?")
        return f"{who} (pid {self.pid}, {self.used_mib} MiB)"


@dataclass(frozen=True)
class Headroom:
    """Whether ``model`` fits on its card right now, and what holds the memory."""

    model: str
    #: From ``gpu_affinity``. None when the registry does not pin the model to a
    #: card, in which case this module will not guess which one it lands on.
    card: int | None = None
    #: :func:`needed_mib` of the model's ``vram_mb``. None when unknown.
    needed_mib: int | None = None
    free_mib: int | None = None
    #: How much is missing. 0 when it fits, None when the question is unanswered.
    shortfall_mib: int | None = None
    #: True, False, or **None for unknown** — never True by default.
    fits: bool | None = None
    #: Rows in an ``atr-*`` unit: the gateway, its vLLM children, the engines.
    ours: list[Occupant] = field(default_factory=list)
    #: Everybody else's, with the service and user that own it.
    theirs: list[Occupant] = field(default_factory=list)
    #: The sentence a human reads instead of doing the subtraction.
    reason: str = ""

    @property
    def ours_mib(self) -> int:
        return sum(o.used_mib for o in self.ours)

    @property
    def theirs_mib(self) -> int:
        return sum(o.used_mib for o in self.theirs)

    @property
    def largest_ours(self) -> Occupant | None:
        """The biggest row V4 could ever ask for. None when we hold nothing."""
        return max(self.ours, key=lambda o: o.used_mib, default=None)

    def as_dict(self) -> dict:
        """JSON for the MCP tool — the dataclass plus the three derived totals."""
        return {
            "model": self.model,
            "card": self.card,
            "needed_mib": self.needed_mib,
            "free_mib": self.free_mib,
            "shortfall_mib": self.shortfall_mib,
            "fits": self.fits,
            "reason": self.reason,
            "ours_mib": self.ours_mib,
            "theirs_mib": self.theirs_mib,
            "ours": [vars(o) for o in self.ours],
            "theirs": [vars(o) for o in self.theirs],
        }


def _payload(probe: dict, key: str) -> dict | None:
    """A probe's answer, or None when that probe did not come back.

    ``gateway_probe`` files a failure under the probe's own key, as
    ``{"error": …}`` for a transport failure or ``{"status": …, "body": …}`` for
    a non-200. Either way there is no report to read, and the difference between
    "the card is full" and "nobody answered" has to survive to ``fits``.
    """
    value = probe.get(key)
    if not isinstance(value, dict):
        return None
    if "error" in value or "status" in value:
        return None
    return value


def _spec(models: dict, model_id: str) -> dict | None:
    for spec in models.get("models") or []:
        if isinstance(spec, dict) and spec.get("id") == model_id:
            return spec
    return None


def _occupants(card: dict) -> tuple[list[Occupant], list[Occupant]]:
    ours: list[Occupant] = []
    theirs: list[Occupant] = []
    for row in card.get("processes") or []:
        service = row.get("service")
        occupant = Occupant(
            pid=int(row.get("pid") or 0),
            used_mib=int(row.get("used_mib") or 0),
            service=service,
            user=row.get("user"),
            age_s=row.get("age_s"),
            orphaned=bool(row.get("orphaned")),
        )
        mine = bool(service and service.startswith(OWN_UNIT_PREFIXES))
        (ours if mine else theirs).append(occupant)
    ours.sort(key=lambda o: o.used_mib, reverse=True)
    theirs.sort(key=lambda o: o.used_mib, reverse=True)
    return ours, theirs


def _card(gpu: dict, index: int) -> dict | None:
    for card in gpu.get("cards") or []:
        if isinstance(card, dict) and card.get("index") == index:
            return card
    return None


def gpu_headroom(model_id: str, probe: dict | None = None) -> Headroom:
    """Whether ``model_id`` fits on its card right now.

    ``probe`` is a :func:`mcp_atr.server.gateway_probe` result. Passing one in is
    the normal case — the caller has just taken it, and asking the gateway twice
    for the same two reports would be two chances for them to disagree. Omitted,
    one is taken here.

    Never raises. Every way of not knowing ends in ``fits=None`` with a ``reason``
    that says which one, because the alternative — an exception on the preflight
    path, or a cheerful ``True`` — is how a batch ends up discovering the answer
    from the cold start it was supposed to avoid.
    """
    if probe is None:
        from mcp_atr.server import gateway_probe

        probe = gateway_probe()

    models = _payload(probe, "models")
    gpu = _payload(probe, "gpu_serving")

    if models is None:
        return Headroom(model=model_id,
                        reason="/models did not answer; nothing to compare against")
    spec = _spec(models, model_id)
    if spec is None:
        return Headroom(model=model_id,
                        reason=f"{model_id} is not in /models — "
                               "it is not registered, or not servable on this host")

    vram_mb = spec.get("vram_mb") or 0
    card_index = spec.get("gpu_affinity")
    need = needed_mib(vram_mb) if vram_mb > 0 else None

    if gpu is None:
        return Headroom(model=model_id, card=card_index, needed_mib=need,
                        reason="/gpu did not answer; free memory is unknown, "
                               "which is not the same as sufficient")
    if need is None:
        return Headroom(model=model_id, card=card_index,
                        reason=f"{model_id} declares no vram_mb; "
                               "how much it needs is unrecorded, not zero")
    if card_index is None:
        return Headroom(model=model_id, needed_mib=need,
                        reason=f"{model_id} has no gpu_affinity; which card it "
                               "lands on is the ModelManager's choice at launch")

    card = _card(gpu, card_index)
    if card is None:
        return Headroom(model=model_id, card=card_index, needed_mib=need,
                        reason=f"/gpu reports no card {card_index} on "
                               f"{gpu.get('host') or 'the serving host'}")

    ours, theirs = _occupants(card)
    free = int(card.get("memory_free_mib") or 0)
    fits = free >= need
    shortfall = 0 if fits else need - free

    if fits:
        reason = (f"{model_id} fits on gpu {card_index}: needs {need} MiB "
                  f"(vram_mb {vram_mb} x {MIN_HEADROOM} + {RESERVE_MB}), "
                  f"{free} MiB free")
    else:
        biggest = max(ours, key=lambda o: o.used_mib, default=None)
        holding = (f"; our largest is {biggest.label}" if biggest
                   else "; we hold nothing on this card")
        foreign = sum(o.used_mib for o in theirs)
        not_ours = (f", and {foreign} MiB belongs to "
                    f"{', '.join(sorted({o.service or '?' for o in theirs}))}"
                    if theirs else "")
        reason = (f"{model_id} does not fit on gpu {card_index}: needs {need} MiB "
                  f"(vram_mb {vram_mb} x {MIN_HEADROOM} + {RESERVE_MB}), "
                  f"{free} MiB free, short {shortfall} MiB{holding}{not_ours}")

    return Headroom(model=model_id, card=card_index, needed_mib=need, free_mib=free,
                    shortfall_mib=shortfall, fits=fits, ours=ours, theirs=theirs,
                    reason=reason)


def headroom_report(probe: dict) -> dict:
    """:func:`gpu_headroom` for every model ``/models`` lists, as JSON.

    What ``gateway_models`` adds to the two raw reports: the line somebody would
    otherwise compute by hand, for each model, from the same probe.
    """
    models = _payload(probe, "models")
    if models is None:
        return {"error": "/models did not answer; no headroom could be computed"}
    return {spec["id"]: gpu_headroom(spec["id"], probe).as_dict()
            for spec in models.get("models") or []
            if isinstance(spec, dict) and spec.get("id")}
