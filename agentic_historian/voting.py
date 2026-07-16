"""
voting.py — one-or-many human votes decide the winning transcription (#290).

The automatic fusion vote fails when the engines genuinely disagree: on BAT_664
(71% pairwise CER) three mediocre candidates out-voted TrOCR's good diplomatic
reading, so the fused text was worse than the best single engine. At that
disagreement level there is no consensus to find — a human has to say which
reading is right.

Gate 2 (``path_compare``) already renders the candidates and applies a pick, but
it is first-click-decides. This module adds the missing piece: **collect N votes,
aggregate them, and only then apply**. It deliberately delegates the actual apply
to ``path_compare.apply_path_choice`` so the existing RunState invalidation
(B/C re-run) and ``routing.jsonl`` feedback happen unchanged.

Why this also fixes the fusion problem: every vote flows into the feedback log →
the routing prior (#155) nudges the winning engine/model up for that
script/century → next run the *right* model is picked first. Human judgement
becomes the quality signal the automatic vote never had.

Votes live in ``data/feedback/votes.jsonl`` (one JSON line each, append-only;
the latest line for a voter wins, so a re-vote replaces the earlier one).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

import config


@dataclass
class Vote:
    doc_id: str
    candidate: str                 # path/engine label, e.g. "trocr-kurrent-xvi-xvii"
    voter: str                     # stable voter id (Discord user id)
    page: str = ""                 # optional: per-page voting for multi-page orders
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {"doc_id": self.doc_id, "candidate": self.candidate,
                "voter": self.voter, "page": self.page, "ts": self.ts}


def _key(doc_id: str, page: str) -> tuple[str, str]:
    return (doc_id, page or "")


# ── recording ─────────────────────────────────────────────────────────────────

def record_vote(doc_id: str, candidate: str, voter: str, *, page: str = "") -> Vote:
    """Record one vote (append-only). A voter's later vote replaces their earlier
    one — see :func:`load_votes` — so clicking twice can't skew the tally."""
    vote = Vote(doc_id=doc_id, candidate=candidate, voter=str(voter), page=page or "")
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.VOTES_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(vote.to_dict(), ensure_ascii=False) + "\n")
    logger.info(f"[vote] {doc_id}{'/' + page if page else ''}: {voter} → {candidate}")
    return vote


def load_votes(doc_id: str, *, page: str = "") -> list[Vote]:
    """Effective votes for one doc (and page): the LAST vote per voter wins, so a
    re-vote replaces the earlier one. A missing or corrupt log yields []."""
    path = config.VOTES_LOG_PATH
    if not path.exists():
        return []
    latest: dict[str, Vote] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue                       # skip a corrupt line, never raise
            if _key(d.get("doc_id", ""), d.get("page", "")) != _key(doc_id, page):
                continue
            v = Vote(doc_id=d.get("doc_id", ""), candidate=d.get("candidate", ""),
                     voter=str(d.get("voter", "")), page=d.get("page", ""),
                     ts=d.get("ts", ""))
            if v.voter and v.candidate:
                latest[v.voter] = v            # later line replaces earlier
    except OSError as e:
        logger.warning(f"[vote] could not read {path}: {e}")
        return []
    return list(latest.values())


# ── aggregation ───────────────────────────────────────────────────────────────

def tally(votes: list[Vote]) -> dict[str, int]:
    """Votes per candidate (effective votes only — one per voter)."""
    out: dict[str, int] = {}
    for v in votes:
        out[v.candidate] = out.get(v.candidate, 0) + 1
    return out


def winner(votes: list[Vote], *, min_votes: Optional[int] = None) -> Optional[tuple[str, int]]:
    """The decided winner as ``(candidate, votes)``, or ``None`` when undecided.

    Undecided means: fewer than ``min_votes`` cast, or a tie for the lead (wait for
    another vote rather than break a tie arbitrarily). ``min_votes`` defaults to
    ``config.VOTING_MIN_VOTES`` (1 → a single vote decides, as Gate 2 does today).
    """
    if min_votes is None:
        min_votes = getattr(config, "VOTING_MIN_VOTES", 1)
    counts = tally(votes)
    if not counts or len(votes) < max(1, min_votes):
        return None
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return None                            # tie → no winner yet
    return ranked[0]


# ── applying ──────────────────────────────────────────────────────────────────

def apply_winner(state, paths: dict, *, page: str = "",
                 min_votes: Optional[int] = None) -> Optional[str]:
    """Apply the voted winner, if the votes have decided one.

    Delegates to Gate 2's ``apply_path_choice`` so the RunState invalidation
    (path_preference → B/C re-run) and the routing feedback log happen exactly as
    for a single-click pick. Returns the winning text, or ``None`` if undecided or
    the winning candidate has no text in ``paths``.
    """
    decided = winner(load_votes(state.doc_id, page=page), min_votes=min_votes)
    if decided is None:
        return None
    candidate, count = decided
    if not (paths.get(candidate) or "").strip():
        logger.warning(f"[vote] {state.doc_id}: winner {candidate!r} has no text — not applying")
        return None
    from path_compare import apply_path_choice
    return apply_path_choice(state, candidate, paths, decided_by=f"vote({count})")
