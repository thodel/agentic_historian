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

# ── Selection agreement + regret (Q-3, #334) ─────────────────────────────────
#
# "Did we pick the best one on offer?" — measured against the historian's own
# choice, which is the only judgement available. This measures the SELECTOR
# (#300's match-score ranking), not the transcription, and needs no reference
# text: both sides are candidates we produced.
#
# On `regret_cer` — the one number here that could be misread. It is the CER
# BETWEEN two candidate texts: what the selector picked vs what the historian
# ended up with. That is a *distance*, not an accuracy score. It is bounded by
# the candidate pool and says nothing about how close either text is to the true
# reading (#326/#336). Never aggregate it as "our CER".

def _selection_events(events=None):
    if events is not None:
        return events
    from preferences import load_preferences
    return load_preferences()


def _bucket(ev) -> tuple:
    c = ev.criteria or {}
    return (c.get("script"), c.get("century"), c.get("lang"))


def compute_selection_agreement(events=None) -> dict:
    """How often the automatic pick matched the historian's.

    A **combine** counts as agreement when the auto pick is among the chosen —
    the historian kept it, they just kept others too.

    Excluded from the denominator: events with nothing chosen (a rejection, Q-2 —
    there was no human pick to agree with) and events with no recorded auto pick
    (nothing to compare). An undecided page is *unknown*, not a disagreement.
    """
    events = _selection_events(events)
    overall = {"decided": 0, "agreed": 0}
    by_bucket: dict[tuple, dict] = defaultdict(lambda: {"decided": 0, "agreed": 0})
    by_auto_engine: dict[str, dict] = defaultdict(lambda: {"decided": 0, "agreed": 0})

    for ev in events:
        if not ev.chosen or not ev.auto_pick:
            continue
        agreed = ev.auto_pick in ev.chosen
        engine = (ev.auto_pick.split("/", 1)[0] or "?")
        for target, key in ((by_bucket, _bucket(ev)), (by_auto_engine, engine)):
            target[key]["decided"] += 1
            target[key]["agreed"] += int(agreed)
        overall["decided"] += 1
        overall["agreed"] += int(agreed)

    def _rate(d):
        return {**d, "rate": (d["agreed"] / d["decided"]) if d["decided"] else None}

    return {
        "overall": _rate(overall),
        "by_bucket": {k: _rate(v) for k, v in by_bucket.items()},
        "by_auto_engine": {k: _rate(v) for k, v in by_auto_engine.items()},
    }


def _candidate_texts(ev) -> tuple[str, str]:
    """``(auto_text, human_text)`` for one event, or ``("", "")`` if unavailable.

    The preference log holds comparisons, never text (#332) — so the texts are
    read back from the RunState, where they belong. When they are gone (an older
    run, a re-run that replaced the candidates), regret is simply not computed:
    agreement still is, because it needs only labels. Degrading on the harder
    metric is better than inventing one.
    """
    try:
        from runstate import RunState
        state = RunState.load_or_new(ev.doc_id)
        paths = state.artifacts.get("paths") or {}
        prefix = f"{ev.page}:" if ev.page else ""
        auto_text = paths.get(prefix + ev.auto_pick, "") or ""

        # What the historian ended up with: the combined/confirmed text when we
        # have it (a combine has no single candidate), else their one pick.
        closest = (state.closest_reading or {}).get("text") if state.closest_reading else None
        if closest:
            human_text = str(closest)
        elif len(ev.chosen) == 1:
            human_text = paths.get(prefix + ev.chosen[0], "") or ""
        else:
            human_text = ""
        return auto_text, human_text
    except Exception:                                  # reporting is best-effort
        return "", ""


def compute_regret(events=None) -> dict:
    """Distance between what the selector picked and what the historian kept.

    Only over DISAGREEMENTS (an agreement is zero by definition). Reported as a
    distribution — a rare large regret matters more than many tiny ones, so a mean
    would hide exactly the cases worth looking at.
    """
    import statistics
    from eval.metrics import cer

    events = _selection_events(events)
    values: list[float] = []
    unmeasurable = 0
    for ev in events:
        if not ev.chosen or not ev.auto_pick or ev.auto_pick in ev.chosen:
            continue
        auto_text, human_text = _candidate_texts(ev)
        if not auto_text.strip() or not human_text.strip():
            unmeasurable += 1
            continue
        values.append(cer(auto_text, human_text, ignore_case=False,
                          ignore_whitespace=False, ignore_punctuation=False))

    values.sort()
    def _pct(p):
        if not values:
            return None
        return values[min(len(values) - 1, int(round(p * (len(values) - 1))))]

    return {
        "disagreements_measured": len(values),
        "disagreements_unmeasurable": unmeasurable,   # texts no longer available
        "median_regret_cer": statistics.median(values) if values else None,
        "p90_regret_cer": _pct(0.9),
        "max_regret_cer": values[-1] if values else None,
    }


def format_selection_stats(events=None) -> str:
    """Human-readable selection report: agreement, where it fails, and regret."""
    agreement = compute_selection_agreement(events)
    regret = compute_regret(events)
    o = agreement["overall"]
    if not o["decided"]:
        return "📐 **Auswahl-Report** — noch keine Entscheidungen aufgezeichnet."

    lines = [
        "📐 **Auswahl-Report** (Auto-Auswahl vs. Historiker:in)",
        f"Übereinstimmung: **{o['agreed']}/{o['decided']}** ({o['rate']:.0%})",
        "",
    ]
    worst = sorted(
        (b for b in agreement["by_bucket"].items() if b[1]["decided"]),
        key=lambda kv: kv[1]["rate"],
    )[:3]
    if worst:
        lines.append("Schwächste Buckets (Schrift/Jh./Sprache):")
        for (script, century, lang), st in worst:
            lines.append(f"  `{script or '?'}/{century or '?'}/{lang or '?'}` "
                         f"{st['agreed']}/{st['decided']} ({st['rate']:.0%})")
        lines.append("")
    if regret["disagreements_measured"]:
        lines.append(
            f"Abweichung bei Uneinigkeit (regret_cer, Distanz zwischen zwei "
            f"Kandidaten — **kein** Genauigkeitsmass): "
            f"Median {regret['median_regret_cer']:.0%}, "
            f"p90 {regret['p90_regret_cer']:.0%}"
        )
    if regret["disagreements_unmeasurable"]:
        lines.append(f"_{regret['disagreements_unmeasurable']} Abweichung(en) ohne "
                     f"verfügbare Texte — nicht gemessen._")
    return "\n".join(lines)


# ── Coverage (Q-2, #333) ─────────────────────────────────────────────────────
#
# "Did the ensemble offer an acceptable reading at all?" — the one quality
# question a selection can answer without any reference text. The historian
# accepting something means the pool contained a usable option; rejecting means
# it did not. Trending that answers "is the ensemble producing better material?"
#
# It cannot be gamed by reproducing our own errors, it is not capped by the
# candidate pool, and a genuinely better model raises it. It says nothing about
# absolute accuracy — and is not supposed to (#326).

def compute_coverage(events=None) -> dict:
    """Share of DECIDED pages where the historian found an acceptable reading.

    Denominator = pages the historian actually ruled on (accepted or rejected).
    A page never decided is **unknown**, not a failure, and is excluded — counting
    silence as failure would make coverage drop simply because nobody looked yet.
    """
    events = _selection_events(events)
    overall = {"decided": 0, "accepted": 0}
    by_bucket: dict[tuple, dict] = defaultdict(lambda: {"decided": 0, "accepted": 0})
    by_month: dict[str, dict] = defaultdict(lambda: {"decided": 0, "accepted": 0})

    for ev in events:
        accepted = bool(ev.chosen) and not ev.rejected
        if not accepted and not ev.rejected:
            continue                       # neither accepted nor rejected → undecided
        month = (ev.ts or "")[:7]          # YYYY-MM
        for target, key in ((by_bucket, _bucket(ev)), (by_month, month)):
            target[key]["decided"] += 1
            target[key]["accepted"] += int(accepted)
        overall["decided"] += 1
        overall["accepted"] += int(accepted)

    def _rate(d):
        # None, not 0.0 — "no data" and "nothing was usable" are different claims.
        return {**d, "rate": (d["accepted"] / d["decided"]) if d["decided"] else None}

    return {
        "overall": _rate(overall),
        "by_bucket": {k: _rate(v) for k, v in by_bucket.items()},
        "by_month": {k: _rate(v) for k, v in sorted(by_month.items())},
    }


def format_coverage_stats(events=None) -> str:
    """Human-readable coverage report: acceptance overall, worst buckets, trend."""
    cov = compute_coverage(events)
    o = cov["overall"]
    if not o["decided"]:
        return "🧺 **Abdeckungs-Report** — noch keine Entscheidungen aufgezeichnet."

    lines = [
        "🧺 **Abdeckungs-Report** (hat das Ensemble überhaupt eine brauchbare "
        "Lesart geliefert?)",
        f"Brauchbar: **{o['accepted']}/{o['decided']}** ({o['rate']:.0%}) "
        f"der entschiedenen Seiten",
        "",
    ]
    worst = sorted(
        (b for b in cov["by_bucket"].items() if b[1]["decided"]),
        key=lambda kv: kv[1]["rate"],
    )[:3]
    if worst:
        lines.append("Schwächste Buckets (Schrift/Jh./Sprache):")
        for (script, century, lang), st in worst:
            lines.append(f"  `{script or '?'}/{century or '?'}/{lang or '?'}` "
                         f"{st['accepted']}/{st['decided']} ({st['rate']:.0%})")
        lines.append("")
    if len(cov["by_month"]) > 1:
        trend = "  ".join(f"{m} {st['rate']:.0%}" for m, st in cov["by_month"].items())
        lines.append(f"Verlauf: {trend}")
    return "\n".join(lines)
