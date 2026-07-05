"""
agent_a/routing_prior.py — HITL-4c (#155): additive routing prior from human feedback.

When routing.jsonl has ≥10 entries for a (script, century, lang) combination,
a small additive prior is added to score_model() so that the model historically
preferred by humans scores slightly higher. The prior nudges but cannot override
a full criteria match.

Prior mechanism
---------------
The prior is computed from routing.jsonl entries that look like:
  {doc_id, script, century, lang, model_id, chosen_value}

For each (script, century, lang) bucket with ≥ 10 entries:
  - win_rate = wins_for_model / total_entries_in_bucket
  - prior_score = min(win_rate - 0.5, 0.15)   # capped at ±0.15; negative discarded
  - Only applies when base score from exact criteria ≥ 0.3 (nudge, don't override)

The cap design
--------------
- script match alone = 0.4 → a positive prior of at most 0.15 can never override it.
- lang only = 0.3 → a 0.15 prior could theoretically push above 0.4, but script
  matches are computed independently and added first; the cap ensures prior ≤ 0.15
  while the script-only floor is 0.3, so script-match models always win unless the
  prior is applied to a script match (0.4 + prior up to 0.15 → 0.55, still fine).

The "base score ≥ 0.3" threshold means the prior only activates in ambiguous cases
where the base score alone is insufficiently decisive.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from agent_a.model_selector import normalise_lang, normalise_script


@dataclass
class PriorEntry:
    """Statistical prior for a single model within a (script, century, lang) bucket."""
    model_id: str
    wins: int
    total: int
    prior_score: float  # additive nudge (0.0–0.15)


# Routing prior store: key = (script, century, lang), value = list[PriorEntry]
_ROUTING_PRIOR: dict[tuple, list[PriorEntry]] = {}
_ROUTING_PRIOR_LOADED: bool = False


# ── data directory resolution ─────────────────────────────────────────────────

def _routing_path() -> Path:
    """
    Locate routing.jsonl.
    Uses the same logic as config.py: BASE_DIR is the package root,
    data/feedback/ is under it.  Also respects AGENTIC_HISTORIAN_ROOT for
    non-standard layouts during testing.
    """
    from config import BASE_DIR, REPO_ROOT

    # Try data/feedback/ relative to REPO_ROOT first (standard layout)
    p = REPO_ROOT / "data" / "feedback" / "routing.jsonl"
    if p.exists():
        return p

    # Fallback: relative to BASE_DIR (installed package layout)
    p = BASE_DIR / "data" / "feedback" / "routing.jsonl"
    return p


# ── loading ───────────────────────────────────────────────────────────────────

def load_routing_prior() -> dict[tuple, list[PriorEntry]]:
    """
    Load routing.jsonl and compute per-(script, century, lang) win rates.

    Returns a dict keyed by (script_norm, century, lang_norm) where each
    value is a sorted list of PriorEntry (one per distinct model_id in that bucket).

    Only buckets with ≥ 10 total entries are included.
    prior_score for each entry = min(wins/total - 0.5, 0.15), floored at 0.0.
    """
    path = _routing_path()

    if not path.exists():
        logger.debug(f"[routing_prior] routing.jsonl not found at {path}")
        return {}

    # Accumulate votes per bucket
    # bucket_key -> {model_id -> wins, "total" -> int}
    buckets: dict[tuple, dict[str, int | int]] = {}

    try:
        with open(path, encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(f"[routing_prior] line {line_num}: decode error: {exc}")
                    continue

                doc_id = entry.get("doc_id", "")
                script = entry.get("script")
                century = entry.get("century")
                lang = entry.get("lang")
                model_id = entry.get("model_id", "")
                chosen_value = entry.get("chosen_value", "")

                if not all([script, century, lang, model_id]):
                    logger.warning(
                        f"[routing_prior] line {line_num}: missing required field "
                        f"(need script, century, lang, model_id); skipping"
                    )
                    continue

                norm_script = normalise_script(str(script))
                norm_lang = normalise_lang(str(lang))
                bucket_key = (norm_script, int(century), norm_lang)

                if bucket_key not in buckets:
                    buckets[bucket_key] = {"total": 0}
                bucket = buckets[bucket_key]

                bucket["total"] = bucket.get("total", 0) + 1
                if model_id not in bucket:
                    bucket[model_id] = 0
                bucket[model_id] = bucket[model_id] + 1

    except OSError as exc:
        logger.warning(f"[routing_prior] could not read {path}: {exc}")
        return {}

    # Build PriorEntry list per bucket (only buckets with ≥ 10 entries)
    result: dict[tuple, list[PriorEntry]] = {}
    for bucket_key, bucket_data in buckets.items():
        total: int = bucket_data.get("total", 0)
        if total < 10:
            continue

        entries: list[PriorEntry] = []
        for model_id, wins in bucket_data.items():
            if model_id == "total":
                continue
            win_rate = wins / total
            prior_score = min(win_rate - 0.5, 0.15)
            if prior_score > 0.0:  # only store positive priors
                entries.append(PriorEntry(
                    model_id=str(model_id),
                    wins=int(wins),
                    total=total,
                    prior_score=round(prior_score, 4),
                ))

        if entries:
            result[bucket_key] = entries

    logger.info(f"[routing_prior] loaded {len(result)} active buckets from {path}")
    return result


def get_routing_prior() -> dict[tuple, list[PriorEntry]]:
    """
    Returns the cached routing prior, loading it on first call.
    Thread-safe after initial load (the dict itself is immutable post-load).
    """
    global _ROUTING_PRIOR, _ROUTING_PRIOR_LOADED
    if not _ROUTING_PRIOR_LOADED:
        _ROUTING_PRIOR = load_routing_prior()
        _ROUTING_PRIOR_LOADED = True
    return _ROUTING_PRIOR


def get_prior(
    script: Optional[str],
    century: Optional[int],
    lang: Optional[str],
    registry_models: list[str],
) -> dict[str, float]:
    """
    Compute additive prior scores for all models given document criteria.

    Args:
        script:  raw script string (normalised internally)
        century: integer century
        lang:    raw lang string (normalised internally)
        registry_models: list of model_id values in the current registry

    Returns:
        dict mapping model_id → prior_score (0.0 if no prior applies)

    The prior is additive and capped so it cannot override a script match
    (which contributes 0.4 base).  Prior only activates when the bucket
    has ≥ 10 entries.
    """
    if not script or not century or not lang:
        return {}

    prior_store = get_routing_prior()
    norm_script = normalise_script(script)
    norm_lang = normalise_lang(lang)
    bucket_key = (norm_script, int(century), norm_lang)

    bucket = prior_store.get(bucket_key, [])
    if not bucket:
        return {}

    # Build result: all models get 0.0 unless they have a positive prior entry
    result: dict[str, float] = {m: 0.0 for m in registry_models}
    for entry in bucket:
        if entry.model_id in result:
            result[entry.model_id] = entry.prior_score

    return result


def clear_cache() -> None:
    """Clear the in-memory prior cache. Useful for testing."""
    global _ROUTING_PRIOR, _ROUTING_PRIOR_LOADED
    _ROUTING_PRIOR = {}
    _ROUTING_PRIOR_LOADED = False