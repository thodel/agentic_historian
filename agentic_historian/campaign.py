"""
campaign.py — G2: a bounded vote campaign on one holding (#399).

G1 (#398) ranks the pending Gate-2 pages. A ranking alone still waits for
someone to come looking, and the epic's done-criterion is a number: after one
campaign on a real holding, the dominant script×century bucket crosses n≥10 and
``ENABLE_ROUTING_PRIOR`` becomes evaluable on real preference data. So this is
the deliberate push — a budgeted set of cards posted into one thread, and a
report that says what the votes bought.

What a "Bestand" is here, and what it is not
────────────────────────────────────────────
#399 words the command as ``/campaign <bestand>``. There is no reliable holding
on a RunState to filter by, and the repository has **two** conventions that
disagree:

* ``provenance.bestand_of`` splits the doc_id on ``/`` — right for the ATR batch
  path ``Marbach/letter-0001``;
* ``entity_agent.bestand_from_doc_id`` splits on ``__`` — right for the folded
  ``PageRef.key`` ``Marbach__letter-0001__001``.

Neither fits the two shapes that actually reach a RunState: the orchestrator
mints ``fp.stem`` (``BAT_664_r_00027``) and ``ingest.pull_folder_orders`` mints
``Path(order).name`` — the *last* path component, so the holding is not in the
id at all. Worse, ``bestand_of`` answers a flat id with the whole id, which
looks like a holding and is not one.

So the scope here is a **doc_id prefix**, said plainly in every message this
module renders, and never called a holding without that qualification. A
historian who types a prefix that matches nothing is shown the prefixes that do
exist — because "no document matched" and "the matching documents have no
pending votes" want different next actions and are identical as an empty
report.

Completion is derived, never recorded
─────────────────────────────────────
#399 is explicit that the preference log's format is methodology and must not be
extended. A campaign that recorded its own completion would be a second source
of truth about whether a page was voted, and the two would drift the first time
a card was answered outside the campaign thread. So the store holds the **ask**
— which cards were posted, and the bucket coverage at the moment of asking —
and ``votes_queue.decided_pages`` answers whether each asked page is done.

The baseline is what makes "crossed" sayable
────────────────────────────────────────────
Without the coverage snapshot taken at start, a report can only say a bucket *is*
above ten, not that this campaign got it there — and the epic asks for the
second. Three outcomes, kept apart: ``crossed`` (was short, is sufficient now),
``already`` (was sufficient before the campaign began, so the campaign gets no
credit), ``still_short``.

At the end the live counts are frozen into the record. A finished campaign read
a month later must report its own numbers, not every comparison the project has
collected since.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from loguru import logger

import config
import votes_queue as vq

__all__ = [
    "Campaign",
    "DEFAULT_BUDGET",
    "MAX_BUDGET",
    "POST_DELAY",
    "Plan",
    "Progress",
    "SCOPE_RULE",
    "campaign_events",
    "campaigns_dir",
    "bucket_report",
    "end",
    "format_plan",
    "format_report",
    "known_scopes",
    "latest",
    "matches",
    "plan",
    "post_cards",
    "progress",
]

#: Store format, so a later change is recognisable rather than a surprise.
CAMPAIGN_SCHEMA = "ah-campaign/1"

#: Cards per campaign when nobody names a budget.
DEFAULT_BUDGET = 10

#: The ceiling. A campaign posts one card per document into one thread; beyond
#: this the thread stops being readable and the posting alone takes minutes.
MAX_BUDGET = 50

#: Seconds between two posted cards. Discord's per-channel budget is roughly
#: five messages in five seconds; py-cord retries a 429 by itself, but a burst
#: that *earns* the 429 has already made the thread arrive out of order.
POST_DELAY = 1.5

#: What the scope argument actually matches, in one sentence, so a surprising
#: result is diagnosable from the message itself.
SCOPE_RULE = ("Bestand = Präfix der `doc_id` (ohne Beachtung der Gross- und "
              "Kleinschreibung) — es gibt auf der RunState keinen separat "
              "geführten Bestand")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def campaigns_dir() -> Path:
    """Where campaign records live.

    A function, not a module constant: a constant is computed once at import and
    then stops reading ``DATA_DIR``, which is the same trap as a derived value
    that ignores what it is derived from (the ``config.page_cache_dir`` lesson).
    """
    return config.DATA_DIR / "campaigns"


# ── scope ────────────────────────────────────────────────────────────────────

def matches(doc_id: str, scope: str) -> bool:
    """Whether ``doc_id`` falls in ``scope`` — a case-insensitive prefix.

    An empty scope matches everything, which is the honest reading of "no
    restriction" and lets ``/campaign`` run over the whole corpus.
    """
    scope = (scope or "").strip()
    if not scope:
        return True
    return (doc_id or "").lower().startswith(scope.lower())


def known_scopes(states: Optional[Iterable] = None, *, limit: int = 12) -> list[str]:
    """Plausible scope values, for the message after a prefix matched nothing.

    The leading component of each known doc_id under either convention, plus the
    bare ids. Deliberately generous: this is a hint, and a hint that omits the
    one the historian wanted is worse than one that offers too many.
    """
    out: set[str] = set()
    try:
        if states is None:
            from runstate import RunState
            ids = RunState.known_doc_ids()
        else:
            ids = [getattr(s, "doc_id", "") for s in states]
    except Exception as e:                      # noqa: BLE001 — a hint may not raise
        logger.warning(f"[campaign] could not list doc ids: {e}")
        return []
    for doc_id in ids:
        if not doc_id:
            continue
        out.add(doc_id)
        for sep in ("__", "/"):
            head = doc_id.split(sep, 1)[0]
            if head and head != doc_id:
                out.add(head)
    return sorted(out)[:limit]


# ── the plan ─────────────────────────────────────────────────────────────────

@dataclass
class Plan:
    """Which cards a campaign would post, and what it leaves out."""

    scope: str
    budget: int
    #: Documents to post, best-ranked first. One card per document, because that
    #: is the unit ``path_compare`` renders: one card carries every page of the
    #: document that has candidates.
    cards: list = field(default_factory=list)
    #: ``(doc_id, page)`` for every pending page on those documents — the ask,
    #: and the denominator of completion. Longer than ``cards`` whenever a
    #: document has several undecided pages, which is why both are reported: a
    #: budget of 5 cards can be 30 decisions, and "budget 5" does not sound like
    #: thirty to the person being asked.
    roster: list = field(default_factory=list)
    #: Documents matching the scope that have a pending page at all.
    matched: int = 0
    #: Of those, how many the budget left out.
    skipped: int = 0
    #: The G1 queue this came from, so an empty plan can explain itself.
    queue: Optional[vq.Queue] = None

    @property
    def pages(self) -> int:
        return len(self.roster)

    def why_empty(self) -> str:
        """Why there is nothing to post. Four situations, four next actions."""
        if self.queue is not None and not self.queue.entries:
            # Nothing is pending anywhere; G1 already tells these three apart.
            return self.queue.why_empty()
        if not self.matched:
            hint = ", ".join(f"`{s}`" for s in known_scopes())
            return (f"Keine offene Seite mit doc_id-Präfix `{self.scope}`. "
                    f"{SCOPE_RULE}."
                    + (f"\nVorhanden: {hint}" if hint else ""))
        return "Das Budget ist 0 — nichts zu posten."


def plan(scope: str, budget: int = DEFAULT_BUDGET, *,
         events=None, votes=None, states: Optional[Iterable] = None) -> Plan:
    """The budgeted card list for one scope, in G1 order.

    Documents are ranked by their best pending page, so a document whose worst
    page is dull still gets posted when one of its pages is informative — the
    card carries all of them anyway.
    """
    budget = max(0, min(int(budget or 0), MAX_BUDGET))
    queue = vq.build_queue(10 ** 6, events=events, votes=votes, states=states)
    in_scope = [e for e in queue.entries if matches(e.doc_id, scope)]

    best: dict[str, float] = {}
    roster_by_doc: dict[str, list] = {}
    for entry in in_scope:                      # already sorted by score
        best.setdefault(entry.doc_id, entry.score)
        roster_by_doc.setdefault(entry.doc_id, []).append((entry.doc_id, entry.page))
    ranked = sorted(best, key=lambda d: (-best[d], d))

    cards = ranked[:budget]
    roster = [pair for doc_id in cards for pair in roster_by_doc[doc_id]]
    return Plan(scope=scope, budget=budget, cards=cards, roster=roster,
                matched=len(ranked), skipped=max(0, len(ranked) - len(cards)),
                queue=queue)


# ── the record ───────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", (text or "").strip()).strip("-")
    return (cleaned or "korpus")[:48]


@dataclass
class Campaign:
    """One campaign's ask, plus the coverage it started and ended with."""

    campaign_id: str
    scope: str
    budget: int
    cards: list = field(default_factory=list)
    roster: list = field(default_factory=list)      # [[doc_id, page], …]
    #: Weighted comparisons per bucket when the campaign started. This is what
    #: makes "crossed" sayable rather than only "is above".
    baseline: dict = field(default_factory=dict)    # {"script|century|lang": float}
    #: The same, frozen when the campaign ended. ``None`` while it runs — a
    #: running campaign reads the live log, a finished one must not keep
    #: collecting credit for every comparison since.
    final: Optional[dict] = None
    started_at: str = field(default_factory=_now)
    ended_at: str = ""
    #: Pseudonymous, like every voter id in this project (#332).
    started_by: str = "anon"
    thread_id: Optional[int] = None
    channel_id: Optional[int] = None
    schema: str = CAMPAIGN_SCHEMA

    @property
    def running(self) -> bool:
        return not self.ended_at

    @property
    def path(self) -> Path:
        return campaigns_dir() / f"{self.campaign_id}.json"

    def to_dict(self) -> dict:
        return {"schema": self.schema, "campaign_id": self.campaign_id,
                "scope": self.scope, "budget": self.budget,
                "cards": list(self.cards),
                "roster": [list(p) for p in self.roster],
                "baseline": dict(self.baseline), "final": self.final,
                "started_at": self.started_at, "ended_at": self.ended_at,
                "started_by": self.started_by,
                "thread_id": self.thread_id, "channel_id": self.channel_id}

    @classmethod
    def from_dict(cls, d: dict) -> "Campaign":
        return cls(campaign_id=d.get("campaign_id", ""), scope=d.get("scope", ""),
                   budget=int(d.get("budget", 0) or 0),
                   cards=list(d.get("cards") or []),
                   roster=[tuple(p) for p in (d.get("roster") or [])],
                   baseline=dict(d.get("baseline") or {}),
                   final=d.get("final"),
                   started_at=d.get("started_at", ""),
                   ended_at=d.get("ended_at", "") or "",
                   started_by=d.get("started_by", "anon") or "anon",
                   thread_id=d.get("thread_id"), channel_id=d.get("channel_id"),
                   schema=d.get("schema", CAMPAIGN_SCHEMA))

    def save(self) -> Path:
        p = self.path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)                      # atomic on POSIX
        return p

    @classmethod
    def load(cls, campaign_id: str) -> Optional["Campaign"]:
        p = campaigns_dir() / f"{campaign_id}.json"
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError) as e:
            logger.warning(f"[campaign] {campaign_id} unreadable: {e}")
            return None

    @classmethod
    def load_all(cls) -> list["Campaign"]:
        """Every readable campaign, newest first. An unreadable file costs itself
        and not the listing."""
        out = []
        try:
            files = sorted(campaigns_dir().glob("*.json"))
        except OSError:                          # pragma: no cover — defensive
            return []
        for p in files:
            c = cls.load(p.stem)
            if c is not None:
                out.append(c)
        return sorted(out, key=lambda c: c.started_at, reverse=True)


def _bucket_key(bucket: tuple) -> str:
    """A bucket tuple as a JSON object key. ``None`` survives as the empty
    string, which is why :func:`_bucket_tuple` maps it back rather than keeping
    ``""`` — ``("", 16, "de")`` and ``(None, 16, "de")`` are different keys in
    the preference log."""
    return "|".join("" if v is None else str(v) for v in bucket)


def _bucket_tuple(key: str) -> tuple:
    parts = (key.split("|") + ["", "", ""])[:3]
    script, century, lang = (p or None for p in parts)
    try:
        century = int(century) if century is not None else None
    except ValueError:                           # pragma: no cover — defensive
        century = None
    return (script, century, lang)


def _snapshot(events=None) -> dict:
    return {_bucket_key(b): v for b, v in vq.bucket_coverage(events).items()}


def latest(scope: Optional[str] = None) -> Optional[Campaign]:
    """The newest campaign, optionally for one scope. Running ones win over
    finished ones at the same scope, because that is the one being asked about."""
    found = [c for c in Campaign.load_all()
             if scope is None or c.scope == scope]
    if not found:
        return None
    running = [c for c in found if c.running]
    return (running or found)[0]


# ── posting ──────────────────────────────────────────────────────────────────

def _card(doc_id: str, *, states: Optional[dict] = None):
    """``(state, paths, text, view_factory)`` for one document's Gate-2 card.

    Returns ``None`` when the document has nothing to show — a run that was
    decided or re-run between planning and posting. Skipping is right: posting a
    card for a document whose candidates are gone would ask for a vote on
    nothing.
    """
    from runstate import RunState
    import path_compare

    state = (states or {}).get(doc_id) or RunState.load_or_new(doc_id)
    paths = (state.artifacts or {}).get("paths") or {}
    usable = {k: v for k, v in paths.items() if (v or "").strip()}
    if len(usable) < 2:
        return None
    return state, paths, path_compare.render_vote_card(state, paths)


async def post_cards(the_plan: Plan, *, post: Callable, open_thread=None,
                     sleep=None, delay: float = POST_DELAY,
                     started_by: str = "", channel_id: Optional[int] = None,
                     states: Optional[dict] = None) -> Campaign:
    """Post the planned cards and record the campaign.

    Every Discord touch is a seam — ``open_thread`` and ``post`` are passed in —
    so the budget, the spacing and the bookkeeping are testable without a
    client, which is the only way the #313 lesson (never break the historian's
    click) can be checked offline.

    The record is saved **before** the first card goes out and again at the end.
    A bot that dies mid-campaign then still has a campaign to report on; the
    other order would lose the ask and with it the baseline, which cannot be
    reconstructed afterwards.
    """
    sleep = sleep or asyncio.sleep
    import preferences

    campaign = Campaign(
        campaign_id=f"{_slug(the_plan.scope)}-"
                    f"{datetime.now(timezone.utc):%Y%m%d-%H%M%S}",
        scope=the_plan.scope, budget=the_plan.budget,
        cards=list(the_plan.cards), roster=list(the_plan.roster),
        baseline=_snapshot(), started_by=preferences.pseudonym(started_by),
        channel_id=channel_id)

    thread = None
    if open_thread is not None:
        try:
            thread = await open_thread(campaign.campaign_id)
            campaign.thread_id = int(getattr(thread, "id", 0)) or None
        except Exception as e:                   # noqa: BLE001
            # A thread is where the campaign *should* live, not a precondition
            # for collecting votes. Posting into the channel is worse and still
            # works; refusing to run would lose the campaign over cosmetics.
            logger.warning(f"[campaign] thread creation failed: {e}")
    campaign.save()

    posted = 0
    for i, doc_id in enumerate(the_plan.cards):
        card = _card(doc_id, states=states)
        if card is None:
            logger.info(f"[campaign] {doc_id}: nothing to post any more — skipped")
            continue
        state, paths, text = card
        if posted and delay:
            await sleep(delay)
        try:
            await post(thread, state, paths, text)
            posted += 1
        except Exception as e:                   # noqa: BLE001
            # One card that would not post must not cost the rest of the
            # campaign, and the roster still names the page as asked.
            logger.warning(f"[campaign] {doc_id}: posting failed: {e}")
    campaign.save()
    logger.info(f"[campaign] {campaign.campaign_id}: posted {posted} of "
                f"{len(the_plan.cards)} card(s), {the_plan.pages} page(s) asked")
    return campaign


# ── progress and report ──────────────────────────────────────────────────────

@dataclass
class Progress:
    campaign: Campaign
    decided: int = 0
    accepted: int = 0
    rejected: int = 0

    @property
    def asked(self) -> int:
        return len(self.campaign.roster)

    @property
    def pending(self) -> int:
        return max(0, self.asked - self.decided)

    @property
    def done(self) -> bool:
        return self.asked > 0 and self.pending == 0

    @property
    def rate(self) -> Optional[float]:
        # None, not 0.0: "nothing was asked" and "nothing was answered" are
        # different claims.
        return (self.decided / self.asked) if self.asked else None


def campaign_events(campaign: Campaign, events=None) -> list:
    """The preference events this campaign can claim.

    On the roster **and** timestamped after the start. The second half matters
    because a page could be decided outside the thread between planning and
    posting, and crediting the campaign with a decision it did not prompt would
    inflate exactly the number the epic is measured on. An event with no
    timestamp is not attributed — ``"" >= started_at`` is false, which is the
    right answer rather than an accident.
    """
    if events is None:
        from preferences import load_preferences
        events = load_preferences()
    roster = {tuple(p) for p in campaign.roster}
    return [ev for ev in events
            if (ev.doc_id, ev.page or "") in roster
            and (ev.ts or "") >= campaign.started_at]


def progress(campaign: Campaign, *, events=None, votes=None,
             states: Optional[Iterable] = None) -> Progress:
    """How far the campaign has got — derived, never recorded.

    Uses G1's ``decided_pages`` for completion so the campaign and the queue can
    never disagree about whether a page is answered, and the preference events
    for the accept/reject split, which only the log knows.
    """
    out = Progress(campaign=campaign)
    roster = {tuple(p) for p in campaign.roster}
    try:
        done = vq.decided_pages(events=events, votes=votes, states=states)
    except Exception as e:                       # noqa: BLE001
        logger.warning(f"[campaign] decision lookup failed: {e}")
        done = {}
    out.decided = sum(1 for pair in roster if pair in done)
    for ev in campaign_events(campaign, events):
        if ev.rejected:
            out.rejected += 1
        elif ev.chosen:
            out.accepted += 1
    return out


@dataclass
class BucketLine:
    """One bucket's coverage across the campaign."""

    bucket: tuple
    before: float
    now: float

    @property
    def gained(self) -> float:
        return self.now - self.before

    @property
    def status(self) -> str:
        """``crossed`` | ``already`` | ``still_short``.

        ``already`` exists so the campaign is not credited with a bucket that
        was sufficient before it started — which is the whole reason the
        baseline is stored.
        """
        if self.before >= vq.MIN_COMPARISONS:
            return "already"
        return "crossed" if self.now >= vq.MIN_COMPARISONS else "still_short"


def bucket_report(campaign: Campaign, *, events=None) -> list[BucketLine]:
    """Per-bucket coverage, biggest gain first.

    A finished campaign reports its frozen final counts; a running one reads the
    live log. Covers every bucket either snapshot knows, so a bucket that the
    campaign created from nothing is listed rather than silently absent.
    """
    now = campaign.final if campaign.final is not None else _snapshot(events)
    keys = set(campaign.baseline) | set(now)
    lines = [BucketLine(_bucket_tuple(k),
                        float(campaign.baseline.get(k, 0.0)),
                        float(now.get(k, 0.0)))
             for k in keys]
    return sorted(lines, key=lambda b: (-b.gained, vq.bucket_label(b.bucket)))


def end(campaign: Campaign, *, events=None) -> Campaign:
    """Close the campaign and freeze its numbers. Idempotent."""
    if campaign.running:
        campaign.final = _snapshot(events)
        campaign.ended_at = _now()
        campaign.save()
    return campaign


# ── rendering ────────────────────────────────────────────────────────────────

def format_plan(the_plan: Plan) -> list[str]:
    """What the campaign is about to do, before it does it."""
    if not the_plan.cards:
        return [f"🎯 **Kampagne** `{the_plan.scope or '(ganzes Korpus)'}` — "
                f"nichts zu posten.\n{the_plan.why_empty()}"]
    lines = [
        f"🎯 **Kampagne** `{the_plan.scope or '(ganzes Korpus)'}` — "
        f"{len(the_plan.cards)} Karte(n), **{the_plan.pages} Seite(n)** zu "
        f"entscheiden.",
        f"_{SCOPE_RULE}._",
    ]
    if the_plan.pages > len(the_plan.cards):
        lines.append(f"Eine Karte trägt alle Seiten ihres Dokuments — Budget "
                     f"{the_plan.budget} Karten heisst hier {the_plan.pages} "
                     f"Entscheidungen.")
    if the_plan.skipped:
        lines.append(f"{the_plan.skipped} weitere Dokument(e) im Bestand warten "
                     f"ausserhalb des Budgets.")
    return ["\n".join(lines)]


def format_report(campaign: Campaign, prog: Progress,
                  lines: Optional[list[BucketLine]] = None, *,
                  events=None) -> list[str]:
    """The coverage report — what the votes bought, per bucket.

    One message per section, following ``atr_status.format_gpu_views``: Discord
    caps a message, and a formatter that returns messages keeps the splitting
    testable without a client.

    ``events`` is read **once** and used for both the bucket lines and the
    agreement figure. An earlier version took ``lines`` but re-read the log for
    the agreement, so a report spanning a vote that landed between the two reads
    described two different states of the log and called it one campaign.
    """
    if events is None:
        from preferences import load_preferences
        events = load_preferences()
    lines = bucket_report(campaign, events=events) if lines is None else lines
    state = "läuft" if campaign.running else f"beendet {campaign.ended_at[:16]}"
    head = [
        f"🎯 **Kampagne** `{campaign.campaign_id}` — {state}",
        f"Bestand `{campaign.scope or '(ganzes Korpus)'}` · "
        f"{len(campaign.cards)} Karte(n) · {prog.asked} Seite(n) gefragt",
        f"Entschieden: **{prog.decided}/{prog.asked}**"
        + (f" ({prog.rate:.0%})" if prog.rate is not None else "")
        + f" — {prog.accepted} × eine Lesart gewählt, {prog.rejected} × als "
          f"Abdeckungslücke erfasst",
    ]
    messages = ["\n".join(head)]

    crossed = [b for b in lines if b.status == "crossed"]
    moved = [b for b in lines if b.gained > 0]
    if moved:
        block = [f"**Vergleiche pro Bucket** (Schwelle {vq.MIN_COMPARISONS:.0f}, "
                 f"#335)"]
        for b in moved:
            mark = {"crossed": "✅", "already": "▫️", "still_short": "⏳"}[b.status]
            block.append(f"{mark} `{vq.bucket_label(b.bucket)}` "
                         f"{b.before:.1f} → {b.now:.1f} (+{b.gained:.1f})")
        if crossed:
            many = len(crossed) != 1
            block.append(f"\n**{len(crossed)} Bucket "
                         f"{'haben' if many else 'hat'} n≥"
                         f"{vq.MIN_COMPARISONS:.0f} in dieser Kampagne "
                         f"überschritten** — damit ist `ENABLE_ROUTING_PRIOR` "
                         f"dort auf echten Präferenzdaten auswertbar (#385).")
        else:
            block.append("\nKein Bucket hat die Schwelle in dieser Kampagne "
                         "überschritten.")
        messages.append("\n".join(block))
    else:
        messages.append("**Vergleiche pro Bucket** — noch keine Stimme "
                        "eingegangen, die Abdeckung ist unverändert.")

    # Selection agreement over the campaign's own events only: the project-wide
    # number is already in /routing_stats, and #399 asks for it at campaign
    # level. Delegated to #334's computation rather than re-derived.
    try:
        from routing_report import compute_selection_agreement
        agree = compute_selection_agreement(
            campaign_events(campaign, events))["overall"]
        if agree["decided"]:
            messages.append(
                f"**Auswahl-Übereinstimmung in dieser Kampagne**: "
                f"{agree['agreed']}/{agree['decided']} ({agree['rate']:.0%}) — "
                f"so oft lag die automatische Wahl auf der Lesart, welche die "
                f"Historikerin behalten hat (#334).")
        else:
            messages.append("**Auswahl-Übereinstimmung** — noch keine "
                            "entschiedene Seite mit aufgezeichneter "
                            "Auto-Auswahl.")
    except Exception as e:                       # noqa: BLE001
        logger.warning(f"[campaign] agreement unavailable: {e}")
    return messages
