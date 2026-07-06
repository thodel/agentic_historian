"""
ingest.py — SwitchDrive → pipeline ingestion orchestration (#33).

UI-agnostic core: the Discord bot (and the future Ad-Fontes web front end) call
these functions, so ingestion logic lives outside bot.py and any UI is a thin
shell over the same core.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

import config
from orchestrator import run_full_pipeline_group


def run_switchdrive_orders(parent: Optional[str] = None,
                           reprocess: bool = False) -> dict:
    """Process each SwitchDrive subfolder under ``parent`` as ONE multi-page order.

    Each immediate subfolder is an order; if there are none, ``parent`` itself is
    treated as a single order (loose images directly in it). Already-processed
    orders are skipped unless ``reprocess`` is set. Each order is staged, run
    through the grouped pipeline, marked processed, and its staging dir cleaned up.

    Returns ``{"done": [...], "skipped": [...], "empty": [...], "errors": [...]}``.
    A failing order is recorded and never stops the batch.
    """
    from utils import switchdrive

    parent = parent or config.SWITCHDRIVE_REMOTE_DIR
    orders = switchdrive.list_subdirs(parent) or [parent]
    already = set() if reprocess else switchdrive.load_processed()
    res: dict[str, list] = {"done": [], "skipped": [], "empty": [], "errors": []}

    for order in orders:
        order_id = order.strip("/").replace("/", "__")
        if order_id in already:
            res["skipped"].append(order_id)
            continue
        staging = config.HOT_FOLDER / "_orders" / order_id
        try:
            files = switchdrive.pull_folder(order, staging, recursive=True)
            if not files:
                res["empty"].append(order_id)
                continue
            doc_id = Path(order.rstrip("/")).name or order_id
            run_full_pipeline_group(doc_id, files)
            switchdrive.mark_processed(order_id)
            res["done"].append(f"{doc_id} ({len(files)}p)")
        except Exception as e:
            logger.exception(f"[ingest] order {order_id} failed")
            res["errors"].append(f"{order_id}: {e}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    return res
