"""
progress.py — phase-event rendering + type-aware output snippets (V-1, #287).

Pure functions, NO discord/requests/network imports. Fully offline-testable.
"""

from __future__ import annotations

import re
from typing import Any


# ── public API ───────────────────────────────────────────────────────────────

def snippet(value: Any, n: int = 3, max_chars: int = 300) -> str:
    """A short, safe preview of ANY step output, type-aware.

    - **str** → first ``n`` non-blank lines, joined with `` / ``.
    - **list** → first ``n`` entries; dict entries render a meaningful key
      (``text`` / ``name`` / ``normalised`` / ``engine`` / ``model_id``), not ``{...}``.
    - **dict** → first ``n`` ``key: value`` pairs; unwraps Ad-Fontes ``{"wert": …}``.
    - Collapses whitespace, truncates to ``max_chars`` with ``…``, appends
      ``(+N more)`` when items were dropped.
    - ``None`` / empty → ``"—"``. Never raises.
    """
    # ── nil / empty ──────────────────────────────────────────────────────────
    if value is None:
        return "—"
    if value == "":
        return "—"

    # ── str (transcription) — first n non-blank lines ────────────────────────
    if isinstance(value, str):
        lines = [ln.strip() for ln in value.splitlines() if ln.strip()]
        if not lines:
            return "—"
        parts = lines[:n]
        dropped = len(lines) - len(parts)
        s = " / ".join(parts)
        s = _collapse(s, max_chars)
        if dropped > 0 and len(s) < max_chars - 10:
            s += f" (+{dropped} more)"
        elif dropped > 0:
            s = s.rstrip("…") + "(+{0} more)".format(dropped)
        return s

    # ── list (entities, recognitions, models…) ───────────────────────────────
    if isinstance(value, list):
        if not value:
            return "—"
        items: list[str] = []
        for item in value[:n]:
            if isinstance(item, dict):
                rendered = _render_dict_entry(item)
                items.append(rendered)
            elif isinstance(item, str):
                items.append(item)
            else:
                try:
                    items.append(str(item))
                except Exception:
                    items.append("?")
        dropped = len(value) - len(items)
        s = " / ".join(items)
        s = _collapse(s, max_chars)
        if dropped > 0:
            s += f" (+{dropped} more)"
        return s

    # ── dict (source_json, pipeline output…) ─────────────────────────────────
    if isinstance(value, dict):
        if not value:
            return "—"
        pairs: list[str] = []
        for i, (k, v) in enumerate(value.items()):
            if i >= n:
                break
            raw = _unwrap_wert(v)
            pairs.append(f"{k}: {raw}")
        dropped = max(0, len(value) - len(pairs))
        s = _collapse(" | ".join(pairs), max_chars)
        if dropped > 0:
            s += f" (+{dropped} more)"
        return s

    # ── fallback for unexpected types ────────────────────────────────────────
    try:
        s = str(value)
        # Repr-like strings (contains '<', object address) are useless — drop
        if "<" in s and ">" in s:
            return "—"
        s = _collapse(s, max_chars)
        return s
    except Exception:
        return "—"


def format_phase_event(ev: Any) -> str:
    """One Discord line from a ``runstate.PhaseEvent``.

    - ``✅`` / ``❌`` from ``ev.status`` (``done`` / ``error``).
    - Phase + agent; then ``ev.excerpt`` (or ``ev.error`` on failure).
    - ``ev.decision`` appended when set.
    - Hard-capped to fit comfortably in a Discord message.

    Raises AttributeError if ``ev`` lacks the expected fields.
    """
    if ev.status == "error":
        icon = "❌"
        detail = _collapse(ev.error, 300)
    else:
        icon = "✅"
        detail = _collapse(ev.excerpt, 300)

    agent_badge = f"**{ev.agent}**"
    decision_str = f" · `{ev.decision}`" if ev.decision else ""

    line = f"{icon} {agent_badge} ({ev.phase}) — {detail}{decision_str}"
    return _hard_cap(line, 1900)


def format_board(events: list[Any], doc_id: str) -> str:
    """Render a list of ``PhaseEvent`` objects as ONE status board message.

    Cap the whole output **under 2000 chars** (Discord limit).
    When it would overflow, keep the most recent lines and prefix
    ``… N earlier steps``.

    Args:
        events: list of PhaseEvent (or any duck-typed object with the same
            fields: doc_id, phase, agent, status, excerpt, decision, error).
        doc_id: document identifier shown in the header.

    Returns:
        A single string, always ≤ 2000 characters.
    """
    header = f"**📋 Run-State — `{doc_id}`**\n"
    header_len = len(header)

    lines: list[str] = []
    for ev in reversed(events):          # newest first internally
        line = format_phase_event(ev)
        lines.append(line)

    # Try to fit all lines
    body = "\n".join(lines)
    total = header_len + len(body) + 2   # +2 for "\n\n"
    if total <= 2000:
        return header + body

    # Overflow: keep as many newest lines as fit, prepend skip notice
    kept: list[str] = []
    skip_count = 0

    for line in lines:
        if header_len + len("\n".join(kept)) + len(line) + 50 > 2000:
            skip_count += 1
            continue
        kept.append(line)

    skip_msg = f"… *{skip_count} earlier step{'s' if skip_count != 1 else ''}*\n"
    body = skip_msg + "\n".join(kept)
    return _hard_cap(header + body, 1995)


# ── helpers ──────────────────────────────────────────────────────────────────

_WERT_RE = re.compile(r"\s+")


def _collapse(s: str, limit: int = 300) -> str:
    """Collapse internal whitespace to single spaces; truncate with ellipsis."""
    s = _WERT_RE.sub(" ", s).strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1] + "…"


def _unwrap_wert(v: Any) -> str:
    """Ad-Fontes unwrap: {"wert": …} or {"value": …} → the inner value as string."""
    if isinstance(v, dict):
        inner = v.get("wert") or v.get("value")
        if inner is not None:
            return _collapse(str(inner), 120)
    return _collapse(str(v) if v is not None else "", 120)


def _render_dict_entry(d: dict) -> str:
    """Render a single dict entry for the snippet list view."""
    for key in ("normalised", "name", "text", "engine", "model_id",
                "id", "label", "value", "wert"):
        if key in d and d[key] is not None:
            val = d[key]
            if isinstance(val, dict):
                val = _unwrap_wert(val)
            return _collapse(str(val), 100)
    for v in d.values():
        if v is not None:
            return _collapse(str(v), 100)
    return "?"


def _hard_cap(s: str, limit: int = 2000) -> str:
    """Absolute hard-cap: truncate at limit with no ellipsis mid-sequence."""
    if len(s) <= limit:
        return s
    return s[: limit]
