"""What lies in a run's model directory, and which of it is a page.

``atr_batch`` writes one ``<key>.json`` (and ``<key>.txt``) per page into
``<run>/<model>/``. Two more JSON files sit beside the pages and are not pages:
the model's energy (serving-atr-inference#240) and the lease the run held on
the card (#474). Everything that counts or reads pages goes through
:func:`page_files`, because ``glob("*.json")`` alone counted the energy file as
a page in ``job_status``, as an unreadable result in a rebuilt report and as an
empty reading in a run export.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

__all__ = ["ENERGY_FILE", "LEASE_FILE", "RECORD_FILES", "page_files", "read_lease"]

#: The model's energy for a rebuilt report.
ENERGY_FILE = "energy.json"
#: The headroom lease the run held for this model: what it freed, from when to when,
#: and whether the gateway confirmed the engines are back (#474).
LEASE_FILE = "lease.json"
#: The run's records beside the pages; never a page.
RECORD_FILES = frozenset({ENERGY_FILE, LEASE_FILE})


def page_files(model_dir: Path) -> list[Path]:
    """The page results of one model directory, sorted; the run's records left out."""
    return sorted(p for p in Path(model_dir).glob("*.json") if p.name not in RECORD_FILES)


def read_lease(model_dir: Path) -> Optional[dict]:
    """The lease record of one model directory (#474), or None without one."""
    try:
        data = json.loads((Path(model_dir) / LEASE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
