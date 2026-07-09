"""
persistent_views.py — HITL gate clicks survive bot restarts (HITL-2c, #150).

Gate views use ``timeout=None`` + stable ``custom_id``s (``ah:<doc>:<gate>:<field>``).
Discord keeps showing the message after a restart; for its buttons to still route
to a callback, the bot must re-register a matching persistent view on startup.

On ``on_ready`` we iterate the persisted run states (``data/runs/*.json``),
rebuild each active document's gate view, and bind it to the stored message id
via ``bot.add_view(view, message_id=...)``. The document id lives in the
``custom_id`` and the run state on disk, so callbacks work with no in-memory
state carried across the restart.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

import config
from runstate import RunState

# ah:<doc>:<gate>:<field>
_CUSTOM_ID_RE = re.compile(r"^ah:(?P<doc>[^:]+):(?P<gate>[^:]+):(?P<field>[^:]+)$")


def parse_custom_id(custom_id: str) -> Optional[tuple[str, str, str]]:
    """(doc_id, gate, field) from a component custom_id, or None."""
    m = _CUSTOM_ID_RE.match(custom_id or "")
    return (m["doc"], m["gate"], m["field"]) if m else None


def _runs_dir() -> Path:
    return config.DATA_DIR / "runs"


def store_message_id(state: RunState, gate: str, message_id: int) -> None:
    """Record which Discord message carries a document's gate card, and persist."""
    state.message_ids[gate] = int(message_id)
    state.save()


def iter_run_states() -> Iterator[RunState]:
    d = _runs_dir()
    if not d.exists():
        return
    for p in sorted(d.glob("*.json")):
        try:
            yield RunState.load(p.stem)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[persist] skipping unreadable run {p.name}: {e}")


def _build_view_for_gate(state: RunState, gate: str):
    """Rebuild the view for a gate from the (loaded) run state, or None.

    Passes runners so gate callbacks can auto-resume when AUTO_RESUME_AFTER_GATE
    is enabled (#227).
    """
    runners = None
    if config.AUTO_RESUME_AFTER_GATE:
        import ingest as _ingest
        runners = _ingest.build_stage_runners(state)
    if gate == "gate1":
        import routing_card
        return routing_card.build_view(state, runners=runners)
    if gate == "gate2":
        import path_compare
        paths = state.artifacts.get("paths") or {}
        return path_compare.build_view(state, paths, runners=runners) if paths else None
    return None


def register_persistent_views(bot) -> int:
    """Re-register every active document's gate views. Returns count registered.

    Called from on_ready; safe to call repeatedly.
    """
    n = 0
    for state in iter_run_states():
        for gate, message_id in list(state.message_ids.items()):
            if not message_id:
                continue
            try:
                view = _build_view_for_gate(state, gate)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[persist] {state.doc_id}/{gate}: build failed: {e}")
                continue
            if view is None:
                continue
            bot.add_view(view, message_id=int(message_id))
            n += 1
    logger.info(f"[persist] registered {n} persistent gate view(s)")
    return n
