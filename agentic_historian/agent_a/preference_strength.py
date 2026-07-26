"""
agent_a/preference_strength.py — engine strength from historian preferences (#335).

Turns the preference log (#332) into a per-bucket ranking of engines, using a
**Bradley–Terry** model over the pairwise comparisons `chosen ≻ offered-but-not-
chosen`. This is the learning signal the project has never had: which engine a
historian actually prefers for Kurrent 16th c., derived without any reference text.

Why Bradley–Terry rather than a win rate
────────────────────────────────────────
``routing_prior`` today computes ``wins / total_entries_in_bucket``. That divides by
the whole bucket, so a model **offered rarely but chosen almost every time it was
offered** scores low and gets no prior — even though it is the strongest engine we
have. Bradley–Terry estimates a strength from *who each model was compared against
and how often*, so being offered rarely costs nothing.

That is not academic: the ensemble plans 3 engines per pass and only widens on
disagreement, so a specialist model is offered far less often than a generalist.

Weighting
─────────
A **combine** (several readings kept, #313) is a weaker statement than picking one:
the historian said "both are usable", not "this beats that". Its comparisons carry
``COMBINE_WEIGHT`` (0.5) instead of 1.0.

Sufficiency
───────────
Below ``MIN_COMPARISONS`` in a bucket the estimate is noise, so the bucket reports
**insufficient data** rather than a number. A confident-looking strength from three
clicks would be worse than none — it would steer model selection on nothing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from loguru import logger

# A combine says "both usable", not "this beats that" — a weaker preference.
COMBINE_WEIGHT = 0.5
# Below this many weighted comparisons a bucket's estimate is noise. Matches the
# existing routing-prior threshold (>=10) so the two agree on what "enough" means.
MIN_COMPARISONS = 10.0
# Bradley–Terry MM iteration limits.
_MAX_ITERS = 200
_TOLERANCE = 1e-9
_EPS = 1e-12


@dataclass
class Strength:
    """One model's estimated strength within a bucket."""
    model_id: str                 # LOCAL id (registry space) when known
    strength: float               # Bradley–Terry strength, normalised to mean 1.0
    wins: float                   # weighted
    comparisons: float            # weighted
    win_prob_vs_field: float      # implied P(beats an average opponent)


def _model_key(entry: dict) -> str:
    """The id to aggregate on: the LOCAL (registry) id when recorded.

    The preference log carries the gateway id (`kraken-early_modern_german`) while
    the model selector works in local ids (`10.5281/zenodo.15030337`). For kraken
    the two share no substring, so aggregating on the gateway id would produce a
    prior that never matches a registry model — silently inert. #332 records
    ``local_model_id`` for exactly this reason; fall back to the display key only
    when it is absent (older events).
    """
    if not isinstance(entry, dict):
        return str(entry)
    local = (entry.get("local_model_id") or "").strip()
    if local:
        return local
    eng = entry.get("engine", "") or ""
    mid = entry.get("model_id", "") or ""
    return f"{eng}/{mid}" if mid else eng


def _display_key(entry) -> str:
    if isinstance(entry, dict):
        eng = entry.get("engine", "") or ""
        mid = entry.get("model_id", "") or ""
        return f"{eng}/{mid}" if mid else eng
    return str(entry)


def comparisons_from(events) -> dict[tuple, list[tuple[str, str, float]]]:
    """``bucket → [(winner, loser, weight), …]`` in LOCAL-id space.

    A rejection (#333) contributes nothing: the historian said *none* of these is
    usable, which is a statement about the pool, not a preference between its
    members.
    """
    out: dict[tuple, list] = defaultdict(list)
    for ev in events:
        if getattr(ev, "rejected", False) or not ev.chosen:
            continue
        offered = ev.offered or []
        by_display = {_display_key(o): _model_key(o) for o in offered}
        winners = [by_display.get(c, c) for c in ev.chosen]
        losers = [_model_key(o) for o in offered
                  if _display_key(o) not in set(ev.chosen)]
        weight = COMBINE_WEIGHT if getattr(ev, "combined", False) else 1.0
        c = ev.criteria or {}
        bucket = (c.get("script"), c.get("century"), c.get("lang"))
        for w in winners:
            for l in losers:
                if w and l and w != l:
                    out[bucket].append((w, l, weight))
    return out


def bradley_terry(comparisons: list[tuple[str, str, float]]) -> dict[str, float]:
    """Bradley–Terry strengths via the standard MM iteration, normalised to mean 1.

    ``p_i ← W_i / Σ_j≠i n_ij / (p_i + p_j)`` where ``W_i`` is i's weighted wins and
    ``n_ij`` the weighted number of comparisons between i and j. Converges for a
    connected comparison graph; a model that never wins tends to 0 and is floored
    at a small epsilon so the iteration stays finite.
    """
    wins: dict[str, float] = defaultdict(float)
    pair_n: dict[tuple, float] = defaultdict(float)
    models: set[str] = set()
    for w, l, weight in comparisons:
        wins[w] += weight
        key = (w, l) if w < l else (l, w)
        pair_n[key] += weight
        models.update((w, l))

    if not models:
        return {}
    p = {m: 1.0 for m in models}
    for _ in range(_MAX_ITERS):
        new: dict[str, float] = {}
        for i in models:
            denom = 0.0
            for j in models:
                if i == j:
                    continue
                key = (i, j) if i < j else (j, i)
                n_ij = pair_n.get(key, 0.0)
                if n_ij:
                    denom += n_ij / max(p[i] + p[j], _EPS)
            new[i] = (wins[i] / denom) if denom > 0 else _EPS
        mean = sum(new.values()) / len(new)
        if mean > 0:
            new = {m: max(v / mean, _EPS) for m, v in new.items()}
        delta = max(abs(new[m] - p[m]) for m in models)
        p = new
        if delta < _TOLERANCE:
            break
    return p


def compute_strengths(events=None, *, min_comparisons: float = MIN_COMPARISONS
                      ) -> dict[tuple, dict]:
    """``bucket → {"sufficient": bool, "comparisons": float, "models": {id: Strength}}``.

    Buckets below ``min_comparisons`` are returned with ``sufficient=False`` and no
    strengths — visible as "insufficient data" rather than silently absent, so a
    thin bucket is distinguishable from an unseen one.
    """
    if events is None:
        from preferences import load_preferences
        events = load_preferences()

    out: dict[tuple, dict] = {}
    for bucket, comps in comparisons_from(events).items():
        total = sum(w for _a, _b, w in comps)
        if total < min_comparisons:
            out[bucket] = {"sufficient": False, "comparisons": total, "models": {}}
            continue
        p = bradley_terry(comps)
        wins: dict[str, float] = defaultdict(float)
        seen: dict[str, float] = defaultdict(float)
        for w, l, weight in comps:
            wins[w] += weight
            seen[w] += weight
            seen[l] += weight
        models = {}
        for m, strength in p.items():
            others = [v for k, v in p.items() if k != m]
            field = (sum(others) / len(others)) if others else 1.0
            models[m] = Strength(
                model_id=m, strength=strength, wins=wins.get(m, 0.0),
                comparisons=seen.get(m, 0.0),
                win_prob_vs_field=strength / max(strength + field, _EPS),
            )
        out[bucket] = {"sufficient": True, "comparisons": total, "models": models}
    return out


def prior_scores(events=None, *, cap: float = 0.15,
                 min_comparisons: float = MIN_COMPARISONS) -> dict[tuple, dict[str, float]]:
    """``bucket → {local_model_id: additive nudge in [0, cap]}``.

    Same shape and cap as the existing win-rate prior, so ``routing_prior`` keeps
    its guarantee: a script match contributes 0.4 and the nudge is at most 0.15, so
    the prior can break a near-tie but can never override a real criteria match.

    The nudge is ``P(beats an average opponent) - 0.5``, floored at 0: only an
    above-average engine is promoted, and nothing is ever demoted on this evidence.
    """
    out: dict[tuple, dict[str, float]] = {}
    for bucket, info in compute_strengths(events, min_comparisons=min_comparisons).items():
        if not info["sufficient"]:
            continue
        scores = {}
        for model_id, st in info["models"].items():
            nudge = min(max(st.win_prob_vs_field - 0.5, 0.0), cap)
            if nudge > 0:
                scores[model_id] = round(nudge, 4)
        if scores:
            out[bucket] = scores
    return out
