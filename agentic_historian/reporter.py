"""Progress reporter for Agentic Historian.

This module tracks implementation progress and can generate status reports.
It reads from a PROGRESS.md file in the package directory (next to this module).
"""

import json
import logging
from pathlib import Path
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
# PROGRESS.md lives in the package dir (agentic_historian/PROGRESS.md), i.e.
# next to this module. The previous `.parent.parent` overshot to the repo root.
PROGRESS_FILE = Path(__file__).parent / "PROGRESS.md"

DEFAULT_PROGRESS = {
    "phase_0": {"status": "pending", "notes": "GitHub push and exec approvals"},
    "phase_1": {"status": "in_progress", "notes": "Scaffold and bot skeleton"},
    "phase_2": {"status": "pending", "notes": "Knowledge hub"},
    "phase_3": {"status": "pending", "notes": "OCR (HTR) pipeline"},
    "phase_4": {"status": "pending", "notes": "Source description"},
    "phase_5": {"status": "pending", "notes": "Entity extraction (NER)"},
    "phase_6": {"status": "pending", "notes": "Corpus analysis"},
    "phase_7": {"status": "pending", "notes": "Meta agent"},
    "phase_8": {"status": "pending", "notes": "Hot folder integration"},
    "phase_9": {"status": "pending", "notes": "Testing and tuning"},
    "last_commit": None,
    "last_activity": None,
}


def load_progress() -> dict:
    """Load progress state from PROGRESS.md JSON, or return defaults."""
    if not PROGRESS_FILE.exists():
        return DEFAULT_PROGRESS.copy()
    try:
        text = PROGRESS_FILE.read_text()
        # Extract JSON block if present
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
    except Exception as e:
        logger.warning(f"Could not load progress file: {e}")
    return DEFAULT_PROGRESS.copy()


def save_progress(data: dict) -> None:
    """Save progress state to PROGRESS.md."""
    PROGRESS_FILE.write_text(
        f"# Agentic Historian — Progress\n\n"
        f"_Last updated: {datetime.now(timezone.utc).isoformat()}_\n\n"
        f"```json\n{json.dumps(data, indent=2)}\n```\n"
    )


def update_phase(phase: str, status: str, note: str = "") -> dict:
    """Update a phase status. Returns the updated progress."""
    data = load_progress()
    key = f"phase_{phase}"
    if key in data:
        data[key]["status"] = status
        if note:
            data[key]["notes"] = note
    data["last_activity"] = datetime.now(timezone.utc).isoformat()
    save_progress(data)
    return data


def generate_report() -> str:
    """Generate a human-readable progress report."""
    data = load_progress()
    lines = ["**Agentic Historian — Progress Report**\n"]
    phase_map = {
        "phase_0": "Phase 0 — GitHub Setup",
        "phase_1": "Phase 1 — Scaffold & Bot",
        "phase_2": "Phase 2 — Knowledge Hub",
        "phase_3": "Phase 3 — OCR (HTR)",
        "phase_4": "Phase 4 — Source Description",
        "phase_5": "Phase 5 — Entity Extraction",
        "phase_6": "Phase 6 — Corpus Analysis",
        "phase_7": "Phase 7 — Meta Agent",
        "phase_8": "Phase 8 — Hot Folder",
        "phase_9": "Phase 9 — Testing & Tuning",
    }
    status_icons = {
        "completed": "✅",
        "in_progress": "🔄",
        "pending": "⬜",
        "blocked": "🚫",
    }
    for key, label in phase_map.items():
        if key in data:
            info = data[key]
            icon = status_icons.get(info.get("status", "pending"), "⬜")
            notes = info.get("notes", "")
            lines.append(f"{icon} **{label}** — {info.get('status', 'pending')}")
            if notes:
                lines.append(f"   └ {notes}")
    last = data.get("last_activity", "unknown")
    lines.append(f"\n_Last activity: {last}_")
    return "\n".join(lines)

