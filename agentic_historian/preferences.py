"""
preferences.py — historian selections recorded as PREFERENCES, not as truth (#332).

A Gate-2 selection says *"of these seven readings, I preferred this one"*. It does
**not** say *"this text is correct"*: the historian picks the closest of the options
we produced, bounded by whatever the ensemble happened to generate. Measuring CER
against it would certify our own errors as ground truth, cap quality at "reproduces
the existing pool", and penalise any better model that disagrees with it (#326).

So this log stores **comparisons**, never the chosen text:

    chosen ≻ each offered-but-not-chosen   (within one page's candidate set)

Two consequences that shape the format:

- **``offered`` matters as much as ``chosen``.** A preference is uninterpretable
  without the alternatives that were actually on the table — an engine that wins
  often because it is offered often is not a strong engine.
- **One event per page.** Comparing a candidate from page 1 against one from page 2
  is meaningless, so a confirm spanning several pages emits several events.

Voter ids are pseudonymised with a per-deployment salt: the routing prior and any
published analysis need a *stable* voter, never an identifiable one.

Downstream: #333 (coverage), #334 (selection agreement/regret), #335 (Bradley–Terry
engine strength → ``agent_a/routing_prior.py``).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

import config


# ── pseudonymisation ─────────────────────────────────────────────────────────

def _salt() -> str:
    """Per-deployment salt, created on first use.

    A bare hash of a Discord id is reversible by anyone with the user list (ids are
    small and enumerable), so the salt is what actually makes this pseudonymous. It
    stays local and is never published with the data.
    """
    path = config.PSEUDONYM_SALT_PATH
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(16)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:                                  # pragma: no cover — platform
            pass
        return value
    except OSError as e:
        logger.warning(f"[prefs] could not read/create salt: {e}")
        return ""


def pseudonym(voter: str) -> str:
    """Stable, non-identifying id for a voter. Empty voter → ``"anon"``."""
    voter = (voter or "").strip()
    if not voter:
        return "anon"
    return hashlib.sha256((_salt() + voter).encode("utf-8")).hexdigest()[:16]


# ── the event ────────────────────────────────────────────────────────────────

@dataclass
class PreferenceEvent:
    """One historian decision over one page's candidate readings."""
    doc_id: str
    page: str = ""
    offered: list = field(default_factory=list)   # [{engine, model_id, auto_rank}]
    chosen: list = field(default_factory=list)    # ["engine/model_id", …]
    combined: bool = False                        # several chosen → weaker signal
    auto_pick: str = ""                           # what the selector picked (#300)
    max_pairwise_cer: Optional[float] = None
    criteria: dict = field(default_factory=dict)  # {script, century, lang}
    rejected: bool = False                        # "none of these is usable" (#333)
    voter: str = "anon"                           # pseudonymous
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "page": self.page, "ts": self.ts,
            "offered": self.offered, "chosen": self.chosen,
            "combined": self.combined, "auto_pick": self.auto_pick,
            "max_pairwise_cer": self.max_pairwise_cer,
            "criteria": self.criteria, "rejected": self.rejected,
            "voter": self.voter,
        }


def pairs(event: PreferenceEvent) -> list[tuple[str, str]]:
    """``(winner, loser)`` for every chosen ≻ offered-but-not-chosen comparison.

    This is the whole point of the log: a single click over N candidates yields
    N-1 comparisons, and *those* are what an engine-strength model consumes (#335).
    A combine (several chosen) yields a comparison per chosen × non-chosen; the
    chosen ones are not compared against each other — the historian expressed no
    preference between them.
    """
    chosen = list(event.chosen or [])
    losers = [_key(o) for o in (event.offered or []) if _key(o) not in chosen]
    return [(w, l) for w in chosen for l in losers]


def _key(offered_entry: Any) -> str:
    if isinstance(offered_entry, dict):
        eng = offered_entry.get("engine", "") or ""
        mid = offered_entry.get("model_id", "") or ""
        return f"{eng}/{mid}" if mid else eng
    return str(offered_entry)


# ── recording ────────────────────────────────────────────────────────────────

def page_of(label: str) -> str:
    """The page a Gate-2 path label belongs to (``"<page>:<engine>/<model>"``)."""
    return label.split(":", 1)[0] if ":" in label else ""


def engine_model_of(label: str) -> str:
    """The ``engine/model`` part of a Gate-2 path label."""
    return label.split(":", 1)[1] if ":" in label else label


def record_rejection(state, paths: dict, *, voter: str = "") -> list:
    """Record "none of these readings is usable" — a coverage FAILURE (#333).

    Without this the log cannot tell an abandoned page from an unseen one, and the
    most valuable negative signal we could collect is lost: a historian declining
    everything is the clearest possible statement that the ensemble produced
    nothing acceptable.

    One event per page with candidates, matching ``record_selection`` — on a
    multi-page card "none usable" applies to each page that was on offer.
    """
    return _record(state, paths, chosen=[], voter=voter, rejected=True)


def record_selection(state, paths: dict, chosen: list, *, voter: str = "") -> list:
    """Record one Gate-2 confirm as preference event(s) — one per page touched.

    Best-effort by construction: this is observation, and a failure here must never
    break the historian's click (the #313 lesson). Returns the events written.

    Never writes the chosen TEXT. ``paths`` is read only for its keys (which
    candidates existed) — the text lives in the RunState and the exports.
    """
    return _record(state, paths, chosen=chosen, voter=voter, rejected=False)


def _record(state, paths: dict, *, chosen: list, voter: str, rejected: bool) -> list:
    """Shared writer for accept and reject, so the two cannot drift apart."""
    try:
        chosen = [c for c in (chosen or []) if c in paths]
        doc_id = getattr(state, "doc_id", "") or ""
        if (not chosen and not rejected) or not doc_id:
            # No doc_id → the preference cannot be attributed to anything, and a
            # junk row would pollute every downstream aggregate (#333-#335).
            return []
        gate = (getattr(state, "gate_decisions", {}) or {})
        ctx_all = gate.get("gate2_context") or {}
        auto_all = gate.get("gate2_auto") or {}

        by_page: dict[str, list] = {}
        for label in paths:
            by_page.setdefault(page_of(label), []).append(label)

        events = []
        for page, labels in by_page.items():
            page_chosen = [c for c in chosen if page_of(c) == page]
            if not page_chosen and not rejected:
                continue                                # nothing decided for this page
            ctx = ctx_all.get(page or "_", {}) or {}
            ranks = ctx.get("ranks") or {}
            offered = []
            for label in labels:
                em = engine_model_of(label)
                eng, _, mid = em.partition("/")
                offered.append({"engine": eng, "model_id": mid,
                                "auto_rank": ranks.get(label)})
            ev = PreferenceEvent(
                doc_id=doc_id,
                page=page,
                offered=offered,
                chosen=[engine_model_of(c) for c in page_chosen],
                combined=len(page_chosen) > 1,
                rejected=rejected,
                auto_pick=engine_model_of(auto_all.get(page or "_", "") or ""),
                max_pairwise_cer=ctx.get("max_pairwise_cer"),
                criteria=ctx.get("criteria") or {},
                voter=pseudonym(voter),
            )
            _append(ev)
            events.append(ev)
        return events
    except Exception as e:                               # never break the click
        logger.warning(f"[prefs] recording failed: {e}")
        return []


def _append(ev: PreferenceEvent) -> None:
    config.FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    with config.PREFERENCES_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
    logger.info(f"[prefs] {ev.doc_id}/{ev.page}: {len(ev.chosen)} chosen of "
                f"{len(ev.offered)} offered"
                f"{' (combined)' if ev.combined else ''}")


def load_preferences(doc_id: Optional[str] = None) -> list[PreferenceEvent]:
    """Every recorded preference (optionally for one doc). Corrupt lines are
    skipped — a malformed line must not cost us the rest of the history."""
    path = config.PREFERENCES_LOG_PATH
    if not path.exists():
        return []
    out: list[PreferenceEvent] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if doc_id is not None and d.get("doc_id") != doc_id:
                continue
            out.append(PreferenceEvent(
                doc_id=d.get("doc_id", ""), page=d.get("page", ""),
                offered=d.get("offered", []) or [], chosen=d.get("chosen", []) or [],
                combined=bool(d.get("combined", False)),
                auto_pick=d.get("auto_pick", "") or "",
                max_pairwise_cer=d.get("max_pairwise_cer"),
                criteria=d.get("criteria", {}) or {},
                rejected=bool(d.get("rejected", False)),
                voter=d.get("voter", "anon"),
                ts=d.get("ts", ""),
            ))
    except OSError as e:
        logger.warning(f"[prefs] could not read {path}: {e}")
        return []
    return out
