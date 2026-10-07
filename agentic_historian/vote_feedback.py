"""
vote_feedback.py — after a Gate-2 vote, say what it taught the system (#369).

Requested on 2026-08-13, the day the first bucket reached sufficiency: six votes
produced 23 pairwise comparisons and ``kurrent/15/de`` crossed
``MIN_COMPARISONS``, the first bucket #335 could actually rank. Until then
voting was a black hole — the historian picked a reading and nothing came back.
That is a poor bargain for the one contributor whose time every metric in #326
depends on, and vote volume is the binding constraint on all of them.

This is presentation, not measurement: ``routing_report.compute_coverage``
(#333), ``compute_selection_agreement`` (#334) and
``votes_queue.bucket_coverage`` (#335's denominator) already produce every
number here.

The bucket that was voted on, not a global average
──────────────────────────────────────────────────
A global figure hides exactly the per-script/century differences #335 exists to
find. A multi-page confirm writes one preference event per page (#332) and
those pages can sit in different buckets, so the message groups by bucket — but
stays **one message**. N messages per confirm would hit the ~5 msg / 5 s channel
limit and bury the card.

What must not be shown
──────────────────────
**Which engine is winning.** #334 measures how often the human agrees with the
automatic pick and #335 derives engine strength from the human's choices; if the
display steers the choice, both metrics start measuring the display. Same
objection that kept the match score off the card (#313) and the auto-pick
unhighlighted (#352). Coverage, comparison count and sufficiency are safe —
they describe *how much* was learned, not *what*.

The ranking is reachable deliberately, through ``/votes_stats bereich:stärke``:
a historian who chooses to look has broken the anchoring themselves, one who is
shown it has not. Naming the command is part of the bargain — "not here" is only
honest if there is a "there", and before #369 there was none, because all three
report formatters were reachable from no command at all.

**``regret_cer``.** It is a distance between two candidates, both of ours,
bounded by the pool (#334) — not an accuracy. There is no way to put a bare
percentage next to "what your vote taught us" without it being read as one, and
it answers a different question anyway. Omitted here; it stays in
``routing_report.format_selection_stats``, with its disclaimer attached.

Three-valued throughout
───────────────────────
A rate is ``None`` when nothing was decided, never 0 %. Agreement is *not
measurable* when no auto-pick was recorded, rather than 0 %. A vote in an
unbucketed page is reported as recorded-but-unattributable, because a comparison
filed under ``(None, None, None)`` teaches one global average and the historian
should know their click bought less than it looks like.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

import votes_queue as vq

__all__ = [
    "BucketFeedback",
    "after_vote",
    "feedback_for",
    "format_feedback",
]

#: Where the engine ranking lives, named in the message so "not here" is honest.
RANKING_COMMAND = "/votes_stats bereich:stärke"


@dataclass
class BucketFeedback:
    """What one bucket learned from the votes just recorded."""

    bucket: tuple
    #: Weighted comparisons before and after the votes just recorded.
    before: float = 0.0
    now: float = 0.0
    #: Coverage (#333) in this bucket, over the whole log.
    accepted: int = 0
    decided: int = 0
    #: Selection agreement (#334) in this bucket, over the whole log.
    agreed: int = 0
    compared: int = 0
    #: Of the events just recorded, how many were an accept and how many a
    #: "keine brauchbar".
    new_accepts: int = 0
    new_rejections: int = 0

    @property
    def gained(self) -> float:
        return self.now - self.before

    @property
    def status(self) -> str:
        """``crossed`` | ``already`` | ``still_short`` — one definition, in
        ``votes_queue``, shared with the campaign report (#399)."""
        return vq.crossing(self.before, self.now)

    @property
    def bucketed(self) -> bool:
        return vq.is_bucketed(self.bucket)

    @property
    def coverage_rate(self) -> Optional[float]:
        # None, not 0.0: "nothing decided" and "nothing was usable" are
        # different claims (#333's own rule).
        return (self.accepted / self.decided) if self.decided else None

    @property
    def agreement_rate(self) -> Optional[float]:
        # None means *not measurable* — no event in this bucket carried a
        # recorded auto-pick, so there was nothing to agree with. Printing 0 %
        # would read as "the selector is always wrong".
        return (self.agreed / self.compared) if self.compared else None


def feedback_for(events, all_events=None) -> list[BucketFeedback]:
    """One :class:`BucketFeedback` per bucket touched by ``events``.

    ``events`` is what ``preferences.record_selection`` / ``record_rejection``
    returned — the events just written. ``all_events`` is the whole log, which
    already contains them.

    ``before`` is computed as ``now - gained`` rather than by filtering the log:
    identity-matching the new events against reloaded ones is fragile (a
    re-parsed event is a different object), and the subtraction is exact because
    both sides come from the same weighted counter.
    """
    if not events:
        return []
    if all_events is None:
        from preferences import load_preferences
        all_events = load_preferences()

    now = vq.bucket_coverage(all_events)
    gained = vq.bucket_coverage(events)

    from routing_report import compute_coverage, compute_selection_agreement
    coverage = compute_coverage(all_events)["by_bucket"]
    agreement = compute_selection_agreement(all_events)["by_bucket"]

    out: dict[tuple, BucketFeedback] = {}
    for ev in events:
        crit = ev.criteria or {}
        bucket = (crit.get("script"), crit.get("century"), crit.get("lang"))
        fb = out.get(bucket)
        if fb is None:
            cov = coverage.get(bucket, {}) or {}
            agr = agreement.get(bucket, {}) or {}
            fb = BucketFeedback(
                bucket=bucket,
                now=now.get(bucket, 0.0),
                before=now.get(bucket, 0.0) - gained.get(bucket, 0.0),
                accepted=int(cov.get("accepted", 0) or 0),
                decided=int(cov.get("decided", 0) or 0),
                agreed=int(agr.get("agreed", 0) or 0),
                compared=int(agr.get("decided", 0) or 0))
            out[bucket] = fb
        if ev.rejected:
            fb.new_rejections += 1
        elif ev.chosen:
            fb.new_accepts += 1
    return sorted(out.values(), key=lambda f: (-f.gained,
                                               vq.bucket_label(f.bucket)))


def format_feedback(feedback: list[BucketFeedback]) -> Optional[str]:
    """The post-vote message, or None when there is nothing to say.

    One message for the whole confirm, however many pages and buckets it
    touched: a multi-page card writes one event per page (#332) and N messages
    would hit the channel's rate limit and push the card out of view.
    """
    if not feedback:
        return None

    lines = ["📊 **Erfasst** — was diese Stimme dem System beigebracht hat:"]
    for fb in feedback:
        lines.append("")
        label = (f"`{vq.bucket_label(fb.bucket)}`" if fb.bucketed
                 else "**ohne Bucket**")
        head = label
        if fb.new_rejections and not fb.new_accepts:
            head += " · als Abdeckungslücke erfasst"
        lines.append(head)

        if fb.coverage_rate is None:
            lines.append("· Abdeckung: noch nichts entschieden")
        else:
            lines.append(f"· Abdeckung: **{fb.accepted}/{fb.decided}** "
                         f"({fb.coverage_rate:.0%}) — so oft war überhaupt eine "
                         f"brauchbare Lesart dabei (#333)")

        if fb.agreement_rate is None:
            lines.append("· Übereinstimmung: nicht messbar — für diesen Bucket "
                         "ist keine Auto-Auswahl aufgezeichnet")
        else:
            lines.append(f"· Übereinstimmung: **{fb.agreed}/{fb.compared}** "
                         f"({fb.agreement_rate:.0%}) — so oft lag die "
                         f"automatische Wahl auf der behaltenen Lesart (#334)")

        if fb.gained > 0:
            lines.append(f"· Vergleiche: **{fb.now:.0f}** "
                         f"(+{fb.gained:.0f}, ab {vq.MIN_COMPARISONS:.0f} "
                         f"auswertbar)")
        else:
            # A rejection yields no comparison: the historian said *none* of
            # these is usable, which is a statement about the pool rather than a
            # preference within it. Saying so beats a silent, unchanged number.
            lines.append(f"· Vergleiche: **{fb.now:.0f}** (unverändert — eine "
                         f"Ablehnung vergleicht keine Lesarten)")

        if fb.status == "crossed":
            lines.append(f"🎉 **Dieser Bucket ist mit dieser Stimme auswertbar "
                         f"geworden** (n≥{vq.MIN_COMPARISONS:.0f}) — die "
                         f"Engine-Stärke lässt sich hier jetzt schätzen (#335).")
        if not fb.bucketed:
            lines.append("⚠️ Für diese Seite sind keine Kriterien (Schrift / "
                         "Jahrhundert / Sprache) aufgezeichnet, der Vergleich "
                         "zählt nur in den globalen Durchschnitt — nicht auf "
                         "Kurrent 16. Jh. o. ä.")

    lines.append("")
    # The decision of #369, stated rather than merely enacted: a historian who
    # chooses to look has broken the anchoring themselves, one who is shown it
    # has not.
    lines.append(f"_Welche Engine vorne liegt, steht hier absichtlich nicht — "
                 f"das wäre die Antwort auf die Frage, die die nächste Stimme "
                 f"beantworten soll. Auf Abruf: `{RANKING_COMMAND}`._")
    return "\n".join(lines)


def after_vote(events, all_events=None) -> Optional[str]:
    """The message to post after a Gate-2 confirm or rejection, or None.

    Never raises: this is a courtesy after the historian's click, and the #313
    lesson is that nothing in that path may cost them the click.
    """
    try:
        return format_feedback(feedback_for(events, all_events))
    except Exception as e:                      # noqa: BLE001 — see docstring
        logger.warning(f"[gate2] vote feedback failed: {e}")
        return None
