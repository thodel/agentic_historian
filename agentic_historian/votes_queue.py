"""
votes_queue.py — G1: ask for the votes that carry information (#398).

Gate 2 produces a card per document and nothing orders them. A historian with
thirty minutes is shown the next document in queue order, which is the arrival
order of the scanner — unrelated to what the system would learn from the click.
The measurement layer is honest about needing votes (#335 reports *insufficient
data* below ten comparisons in a bucket, and today everything is n≈1), so at
thousands of pages votes that arrive by chance will never reach density. This
module ranks the pending pages so the asking is deliberate.

Two things carry information, and they are not the same thing
─────────────────────────────────────────────────────────────
**Disagreement.** At max pairwise CER 0.02 the candidates are near-identical and
the click barely discriminates; at 0.62 it separates genuinely different readings.
The measure is the one the ensemble recorded at decision time (#313's
``gate2_context``), never a re-derivation — see `Pending.disagreement`.

**Bucket starvation.** A comparison in a (script × century × lang) bucket that
already has 40 is worth much less than one in a bucket with 2, because #335's
estimate only exists at all above ``MIN_COMPARISONS``. Starvation therefore
weighs more than disagreement can: ``BUCKET_WEIGHT`` exceeds the CER range, so a
starved bucket outranks a saturated one however loudly the saturated page's
engines disagree, while *within* a bucket disagreement decides.

What this module refuses to claim
─────────────────────────────────
Both inputs are three-valued, and the project has been bitten eight times by a
value that claimed more than was measured:

* ``disagreement is None`` — never measured (a run state written before #332
  recorded the context). It is **not** 0.0, which would mean "measured, and the
  engines agreed". Such a page stays in the queue, ranks low on disagreement and
  says `CER nicht gemessen` rather than `CER 0 %`.
* ``covered is None`` — the page's criteria are empty, so the vote would be filed
  under ``(None, None, None)``. That bucket exists in the log but teaches #335
  one global average instead of "which engine wins for Kurrent 16th c.", so an
  unbucketed page gets **no** starvation bonus. Reading it as "0 comparisons,
  maximally starved" would float every criteria-less page to the top of the
  queue — exactly backwards.

Recomputing the CER from the stored candidate texts was considered and rejected.
``artifacts["paths"]`` accumulates across ensemble passes (``existing.update``),
so a label's text can be a later pass's, and a recomputed number would contradict
the ``max_pairwise_cer`` the preference log stores for the same page. One source,
or the two numbers disagree in public.

The no-anchoring rule
─────────────────────
The Gate-2 card deliberately does not mark the selector's pick: at high
disagreement the selection is by model-MATCH score — a prior on fit-to-source,
not output quality — and on BAT_664 the 0.20-scored engine read better than the
0.80 one. A queue entry that leaked "the system thinks it's TrOCR" would anchor
the judgement the vote exists to collect. So the score is explained in full and
``gate2_auto`` is never read here.

Read-only: no new storage, no writes. ``/votes_queue`` is a view over the run
states, the preference log (#332) and the vote log (#290).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from loguru import logger

from agent_a.preference_strength import MIN_COMPARISONS, comparisons_from
from preferences import page_of

__all__ = [
    "BUCKET_WEIGHT",
    "MIN_COMPARISONS",
    "Pending",
    "Queue",
    "TOP_N",
    "bucket_coverage",
    "bucket_label",
    "crossing",
    "build_queue",
    "decided_pages",
    "format_queue",
]

#: Default queue length. Ten entries is about what a historian scans before
#: picking one; `/votes_queue n` overrides it.
TOP_N = 10

#: How much a starved bucket may add to a page's score.
#:
#: Larger than the CER range (max pairwise CER is in [0, 1]) **on purpose**: a
#: bucket with no comparisons must outrank a sufficient one even when the
#: sufficient one's engines disagree completely, because below
#: ``MIN_COMPARISONS`` the bucket has no estimate at all and the most
#: informative comparison in the world cannot be filed anywhere useful. Within
#: one bucket the term is identical, so disagreement decides — which is the
#: ordering #398 asks for.
BUCKET_WEIGHT = 2.0


# ── buckets ──────────────────────────────────────────────────────────────────

def bucket_of(ctx: dict) -> tuple:
    """The ``(script, century, lang)`` a page's vote would be filed under."""
    crit = ctx.get("criteria") or {}
    return (crit.get("script"), crit.get("century"), crit.get("lang"))


def is_bucketed(bucket: tuple) -> bool:
    """Whether a vote on this page would land in an attributable bucket.

    ``any(...)`` rather than "all three known", deliberately matching
    ``preferences._criteria_for``'s own test: a partly-known bucket like
    ``("kurrent", None, None)`` is a real, if coarse, key in the preference log
    and #335 estimates strengths for it. The queue and the recorder have to agree
    on what counts as unbucketed, or the queue would promise coverage the log
    does not deliver.
    """
    return any(v is not None and v != "" for v in bucket)


def bucket_label(bucket: tuple) -> str:
    """``kurrent×15.Jh×de`` — compact enough for a Discord line."""
    if not is_bucketed(bucket):
        return "unbekannt"
    script, century, lang = bucket
    parts = [str(script) if script else "?",
             f"{century}.Jh" if century else "?",
             str(lang) if lang else "?"]
    return "×".join(parts)


def crossing(before: float, now: float) -> str:
    """``crossed`` | ``already`` | ``still_short`` for one bucket's coverage.

    Defined once, here beside ``MIN_COMPARISONS``, because two consumers ask it
    — a campaign's report (#399) and the message after a single vote (#369) —
    and the interesting case is the one a second implementation gets wrong.
    ``already`` means the bucket was sufficient *before*, so whatever just
    happened gets no credit for it; without that distinction a report can only
    say a bucket **is** above the line, never that this vote or this campaign
    put it there.
    """
    if before >= MIN_COMPARISONS:
        return "already"
    return "crossed" if now >= MIN_COMPARISONS else "still_short"


def bucket_coverage(events=None) -> dict[tuple, float]:
    """``bucket → weighted comparisons`` — the denominator #335 reports against.

    Delegates the counting to ``preference_strength.comparisons_from`` rather
    than re-deriving it, so the queue's "3.0/10" is the same 3.0 that decides
    whether the bucket has an estimate. Two consequences come for free and are
    the right ones: a rejection contributes nothing (the historian said *none*
    of these is usable, which is a statement about the pool, not a preference
    within it), and a combine contributes 0.5 (#335's ``COMBINE_WEIGHT``).
    """
    if events is None:
        from preferences import load_preferences
        events = load_preferences()
    return {bucket: sum(w for _w, _l, w in comps)
            for bucket, comps in comparisons_from(events).items()}


# ── one pending page ─────────────────────────────────────────────────────────

@dataclass
class Pending:
    """One page with ≥2 usable candidate readings, and why it is worth a vote."""

    doc_id: str
    page: str
    candidates: int
    #: Max pairwise CER as the ensemble measured it. ``None`` means *not
    #: measured*, which is a different fact from 0.0 (measured; they agreed).
    disagreement: Optional[float] = None
    bucket: tuple = (None, None, None)
    #: Weighted comparisons already in ``bucket``. ``None`` when the page is
    #: unbucketed — no bonus can honestly be computed, see the module docstring.
    covered: Optional[float] = None
    #: Empty while pending; otherwise how the page was already decided.
    decided_by: str = ""

    @property
    def decided(self) -> bool:
        return bool(self.decided_by)

    @property
    def bucketed(self) -> bool:
        return self.covered is not None

    @property
    def starvation(self) -> float:
        """How far the bucket is below sufficiency, in [0, 1]. 0 when saturated
        or unbucketed."""
        if self.covered is None:
            return 0.0
        return max(0.0, (MIN_COMPARISONS - self.covered) / MIN_COMPARISONS)

    @property
    def score(self) -> float:
        """The sampling score. Zero for a page that was already decided.

        A score is one number, so it cannot carry "disagreement unknown" — an
        unmeasured page necessarily contributes 0 here. That is why `why` exists
        and says `CER nicht gemessen`: the ranking loses the distinction, the
        explanation must not.
        """
        if self.decided:
            return 0.0
        return (self.disagreement or 0.0) + BUCKET_WEIGHT * self.starvation

    def why(self) -> str:
        """The score's components, in words. #398 requires these to be shown:
        the historian should see why they are being asked."""
        if self.decided:
            return f"entschieden ({self.decided_by}) — kein Stimmbedarf"
        if self.disagreement is None:
            cer = "CER nicht gemessen"
        else:
            cer = f"CER {self.disagreement:.0%}"
        if self.covered is None:
            cover = "Bucket unbekannt — kein Abdeckungsbonus"
        elif self.starvation <= 0:
            cover = (f"Bucket {bucket_label(self.bucket)}: "
                     f"{self.covered:.1f} Vergleiche — ausreichend")
        else:
            cover = (f"Bucket {bucket_label(self.bucket)}: {self.covered:.1f}/"
                     f"{MIN_COMPARISONS:.0f} Vergleiche")
        return f"{cer} · {cover}"

    @property
    def where(self) -> str:
        return f"`{self.doc_id}`" + (f" · `{self.page}`" if self.page else "")


# ── what counts as already decided ───────────────────────────────────────────

def decided_pages(events=None, votes=None,
                  states: Optional[Iterable] = None) -> dict[tuple, str]:
    """``(doc_id, page) → how it was decided``, over every source of a decision.

    Three sources, because each misses something the others catch:

    * the **preference log** (#332) — the authoritative record, one event per
      page, written for a confirm *and* for "keine brauchbar";
    * the **vote log** (#290) — ``voting.apply_winner``'s path, which records a
      vote without a preference event;
    * ``gate_decisions["path"]`` on the run state — an applied Gate-2 choice.
      Needed because ``record_selection`` is best-effort by design (it must never
      break the historian's click, the #313 lesson): a confirm whose preference
      write failed would otherwise re-offer the page for ever.

    Two things on the run state are deliberately *not* read.
    ``gate_decisions["gate2_rejected"]`` is a document-level flag, so on a
    multi-page card it would retire pages the historian never saw; the per-page
    rejection is in the preference log anyway. ``span_overrides`` is keyed by
    span index and cannot be attributed to a page at all.
    """
    out: dict[tuple, str] = {}
    for state in (states if states is not None else _iter_states()):
        path = ((state.gate_decisions or {}).get("path") or "")
        if path:
            out[(state.doc_id, page_of(path))] = "angewendet"
    for doc_id, page in (votes if votes is not None else _voted_pages()):
        out[(doc_id, page)] = "Stimme"
    if events is None:
        from preferences import load_preferences
        events = load_preferences()
    for ev in events:
        if not ev.doc_id:
            continue
        out[(ev.doc_id, ev.page or "")] = "Ablehnung" if ev.rejected else "Auswahl"
    return out


def _iter_states() -> Iterable:
    """The run states the bot considers active — the same iterator that
    re-registers the persistent gate views, so the queue sees exactly the
    documents whose cards are still clickable."""
    from persistent_views import iter_run_states
    return iter_run_states()


def _voted_pages() -> set:
    from voting import voted_pages
    return voted_pages()


# ── the queue ────────────────────────────────────────────────────────────────

@dataclass
class Queue:
    """A ranked queue, with enough about itself to explain an empty answer."""

    entries: list = field(default_factory=list)      # pending only, ranked
    n: int = TOP_N
    #: Run states inspected.
    docs: int = 0
    #: Pages with ≥2 usable candidates, decided ones included.
    seen: int = 0
    #: Of those, how many are already decided.
    decided: int = 0

    @property
    def top(self) -> list:
        return self.entries[:max(1, self.n)]

    def why_empty(self) -> str:
        """Why there is nothing to vote on, as far as it can be established.

        Three situations that look identical as silence and want three different
        next actions (the #397 lesson): nothing has run, nothing disagreed, or
        everything that disagreed has been decided.
        """
        if not self.docs:
            return ("Keine Läufe gefunden — `data/runs/` ist leer, es ist noch "
                    "kein Dokument durch die Pipeline gelaufen.")
        if not self.seen:
            return (f"{self.docs} Lauf/Läufe, aber keine Seite mit ≥2 brauchbaren "
                    f"Lesarten — keine No-Merge-Entscheidung ist gefallen (#313).")
        return (f"Alle {self.seen} Seite(n) mit uneinigen Lesarten sind "
                f"entschieden — es wartet keine Stimme.")


def _usable_pages(state) -> dict[str, int]:
    """``page → usable candidate count`` for one run state.

    A blank text is not a candidate: the orchestrator already filters those out
    when it records the paths, but a state may carry an emptied label from an
    earlier pass and counting it would promise a comparison that cannot be made.
    """
    counts: dict[str, int] = {}
    for label, text in ((state.artifacts or {}).get("paths") or {}).items():
        if not (text or "").strip():
            continue
        page = page_of(label)
        counts[page] = counts.get(page, 0) + 1
    return {page: n for page, n in counts.items() if n >= 2}


def _context(state, page: str) -> dict:
    """``gate2_context`` for one page.

    The key is ``page or "_"``: a single-page run records its candidates under a
    bare engine label with no page prefix, and the context under ``"_"``. This is
    the same mapping ``preferences._record`` uses — getting it wrong loses the
    criteria and the CER silently, and the page then looks unbucketed.
    """
    ctx_all = (state.gate_decisions or {}).get("gate2_context") or {}
    return ctx_all.get(page or "_") or {}


def build_queue(n: int = TOP_N, *, events=None, votes=None,
                states: Optional[Iterable] = None) -> Queue:
    """Rank every pending Gate-2 page. Read-only; never raises.

    A surface that answers a historian must not hand them a traceback, and an
    unreadable run state must cost that document, not the queue.
    """
    queue = Queue(n=max(1, int(n or TOP_N)))
    try:
        state_list = list(states if states is not None else _iter_states())
    except Exception as e:                      # noqa: BLE001 — see docstring
        logger.warning(f"[votes_queue] could not list run states: {e}")
        return queue

    if events is None:
        try:
            from preferences import load_preferences
            events = load_preferences()
        except Exception as e:                  # noqa: BLE001
            logger.warning(f"[votes_queue] preference log unreadable: {e}")
            events = []
    try:
        coverage = bucket_coverage(events)
        done = decided_pages(events=events, votes=votes, states=state_list)
    except Exception as e:                      # noqa: BLE001
        logger.warning(f"[votes_queue] decision/coverage lookup failed: {e}")
        coverage, done = {}, {}

    queue.docs = len(state_list)
    for state in state_list:
        try:
            pages = _usable_pages(state)
        except Exception as e:                  # noqa: BLE001
            logger.warning(f"[votes_queue] {getattr(state, 'doc_id', '?')}: "
                           f"unreadable paths: {e}")
            continue
        for page, count in pages.items():
            queue.seen += 1
            decided_by = done.get((state.doc_id, page), "")
            if decided_by:
                queue.decided += 1
                continue
            ctx = _context(state, page)
            bucket = bucket_of(ctx)
            entry = Pending(
                doc_id=state.doc_id, page=page, candidates=count,
                disagreement=ctx.get("max_pairwise_cer"),
                bucket=bucket,
                covered=coverage.get(bucket, 0.0) if is_bucketed(bucket) else None,
            )
            queue.entries.append(entry)

    # Deterministic beyond the score, so the same corpus always yields the same
    # queue: a historian who re-runs the command must not get a reshuffled list.
    queue.entries.sort(key=lambda e: (-e.score, e.doc_id, e.page))
    return queue


# ── rendering ────────────────────────────────────────────────────────────────

def format_queue(queue: Queue) -> list[str]:
    """The queue as Discord messages, one per call to send.

    A list rather than one string, following ``atr_status.format_gpu_views`` and
    ``passage_find.format_found``: Discord caps a message, and a formatter that
    returns messages keeps the splitting testable without a Discord client.

    ``gate2_auto`` is never read, here or anywhere in this module — see the
    no-anchoring rule in the module docstring.
    """
    if not queue.entries:
        return [f"🗳️ **Abstimmungs-Warteschlange** — leer.\n{queue.why_empty()}"]

    shown = queue.top
    header = (f"🗳️ **Abstimmungs-Warteschlange** — {len(shown)} von "
              f"{len(queue.entries)} offenen Seite(n), "
              f"nach Informationsgehalt sortiert "
              f"({queue.decided} von {queue.seen} schon entschieden).")
    messages = [header]
    for i, entry in enumerate(shown, 1):
        messages.append(
            f"**{i}.** {entry.where} — Score {entry.score:.2f}\n"
            f"{entry.why()}\n"
            f"{entry.candidates} Lesarten · `/votes {entry.doc_id}`")
    messages.append(
        f"_Score = max. paarweise CER + {BUCKET_WEIGHT:g} × Bucket-Unterdeckung; "
        f"ein Bucket gilt ab {MIN_COMPARISONS:.0f} Vergleichen als ausreichend "
        f"(#335). Welche Lesart die Auswahl bevorzugt, steht hier absichtlich "
        f"nicht._")
    return messages
