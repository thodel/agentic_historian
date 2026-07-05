"""
routing_report.py — HITL-4b (#154): routing stats for Agent E.

Reads ``data/feedback/routing.jsonl`` and computes:
    1. Per-field override rate  — how often did the historian change the value?
    2. Model win-rate per (script, century, lang) bucket
    3. Path-preference distribution (VLM / kraken / reconciled)
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

import config


# ── loading ─────────────────────────────────────────────────────────────────

def _iter_entries():
    path = config.ROUTING_LOG_PATH
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ── computations ────────────────────────────────────────────────────────────

def compute_override_rates() -> dict[str, dict]:
    """Per field: total, overrides (chosen != inferred), override_rate."""
    by_field: dict[str, dict] = defaultdict(lambda: {"total": 0, "overrides": 0})
    for e in _iter_entries():
        f = e.get("field")
        if not f or f == "path_preference":
            continue  # override rate only makes sense for criterion fields
        by_field[f]["total"] += 1
        if e.get("chosen_value") != e.get("inferred_value"):
            by_field[f]["overrides"] += 1

    result = {}
    for f, v in sorted(by_field.items()):
        total = v["total"]
        overrides = v["overrides"]
        result[f] = {
            "total": total,
            "overrides": overrides,
            "override_rate": overrides / total if total > 0 else 0.0,
        }
    return result


def compute_model_winrates() -> dict[tuple, dict]:
    """Per (script, century, lang) bucket: model_id wins/total and win_rate."""
    buckets: dict[tuple, dict[str, dict]] = defaultdict(lambda: defaultdict(
        lambda: {"wins": 0, "total": 0}))
    for e in _iter_entries():
        if e.get("field") != "model_select":
            continue
        scr = (e.get("script") or "").lower() or None
        cent = e.get("century")
        lang = (e.get("lang") or "").lower() or None
        if not (scr and cent and lang):
            continue
        key = (scr, int(cent), lang)
        mid = e.get("model_id") or e.get("chosen_value")
        if not mid:
            continue
        buckets[key][mid]["total"] += 1
        # chosen_value = the final selection after override
        if e.get("decided_by") == "human":
            buckets[key][mid]["wins"] += 1
        elif e.get("chosen_value") == e.get("model_id"):
            # auto case — model_id matched chosen means it won
            buckets[key][mid]["wins"] += 1

    result = {}
    for key, models in sorted(buckets.items()):
        result[key] = {}
        for mid, stats in models.items():
            t = stats["total"]
            result[key][mid] = {
                "wins": stats["wins"],
                "total": t,
                "win_rate": stats["wins"] / t if t > 0 else 0.0,
            }
    return result


def compute_path_preferences() -> dict[str, int]:
    """Count path_preference entries per path (vlm / kraken / reconciled)."""
    counts: dict[str, int] = defaultdict(int)
    for e in _iter_entries():
        if e.get("field") == "path_preference":
            path = e.get("chosen_value") or e.get("path")
            if path:
                counts[path] += 1
    return dict(counts)


# ── formatting ──────────────────────────────────────────────────────────────

def format_routing_stats() -> str:
    """Build a plain-text routing stats block (no Discord dependency)."""
    override = compute_override_rates()
    winrates = compute_model_winrates()
    paths = compute_path_preferences()

    if not override and not winrates and not paths:
        return "— Routing-Log leer (noch keine Daten)."

    lines = []

    # Override rates
    overridden = any(v["overrides"] > 0 for v in override.values())
    if override:
        lines.append("**Override-Rate** (gewählte ≠ inferierte Werte):")
        for f, v in override.items():
            total = v["total"]
            ov = v["overrides"]
            rate = v["override_rate"]
            bar = "█" * round(rate * 10) + "░" * (10 - round(rate * 10))
            lines.append(f"  {f:<16} {bar} {ov}/{total} ({rate:.0%})")
        if not overridden:
            lines.append("  _Keine Overrides — Pipeline arbeitet zuverlässig._")
    else:
        lines.append("**Override-Rate**: keine Daten")

    # Path preferences
    if paths:
        total_paths = sum(paths.values())
        lines.append("")
        lines.append("**Pfad-Präferenz** (VLM / kraken / reconciled):")
        for p, cnt in sorted(paths.items()):
            bar = "█" * round(cnt / total_paths * 10)
            lines.append(f"  {p:<12} {bar} {cnt}")
    else:
        lines.append("")
        lines.append("**Pfad-Präferenz**: keine Daten")

    # Model win-rates (top 5 buckets)
    if winrates:
        lines.append("")
        lines.append("**Modell-Präferenz** (Top-Buckets, \u2265\u00a010 Entscheidungen):")
        for (scr, cent, lang), models in sorted(winrates.items())[:5]:
            total = sum(m["total"] for m in models.values())
            if total < 10:
                continue
            winner = max(models.items(), key=lambda x: x[1]["win_rate"])
            lines.append(f"  {scr} | {cent}. Jh. | {lang}"
                         f"  \u2192 {winner[0]} ({winner[1]['win_rate']:.0%}, n={total})")

    return "\n".join(lines)


# ── Discord embed (imported lazily by bot.py) ───────────────────────────────

def routing_stats_embed():
    """Return a compact Discord embed with routing stats, or None if no data."""
    from utils import gpustack_client as gs  # noqa: F401

    override = compute_override_rates()
    winrates = compute_model_winrates()
    paths = compute_path_preferences()

    if not override and not winrates and not paths:
        return None

    # build description
    desc_parts = []

    if override:
        ov_lines = []
        for f, v in override.items():
            total = v["total"]
            ov = v["overrides"]
            rate = v["override_rate"]
            ov_lines.append(f"`{f}` {ov}/{total} ({rate:.0%})")
        desc_parts.append("**Override-Rate**\n" + "\n".join(ov_lines))

    if paths:
        total_paths = sum(paths.values())
        path_lines = [f"`{p}` {cnt}" for p, cnt in sorted(paths.items())]
        desc_parts.append("**Pfad-Präferenz**\n" + "\n".join(path_lines))

    if winrates:
        top_buckets = []
        for (scr, cent, lang), models in sorted(winrates.items())[:4]:
            total = sum(m["total"] for m in models.values())
            if total < 10:
                continue
            winner = max(models.items(), key=lambda x: x[1]["win_rate"])
            top_buckets.append(
                f"`{scr} | {cent}. Jh.` \u2192 `{winner[0]}` ({winner[1]['win_rate']:.0%})")
        if top_buckets:
            desc_parts.append("**Modell-Präferenz (Top-Buckets)**\n" + "\n".join(top_buckets))

    if not desc_parts:
        return None

    from discord import Embed  # type: ignore[import]
    return Embed(
        title="\U0001f4cb Routing-Statistik (HITL-4b)",
        description="\n\n".join(desc_parts),
        color=0x5C9B3E,
    )