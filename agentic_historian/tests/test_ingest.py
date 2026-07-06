"""#33: SwitchDrive order ingestion lives in ingest.py (UI-agnostic), tested offline.

Mocks utils.switchdrive + run_full_pipeline_group — no network/pipeline.
Run from the repo root:
    pytest agentic_historian/tests/test_ingest.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402
import ingest  # noqa: E402


def _setup(monkeypatch, tmp_path, subdirs, files_map, processed=None):
    monkeypatch.setattr(config, "HOT_FOLDER", tmp_path)
    from utils import switchdrive
    monkeypatch.setattr(switchdrive, "list_subdirs", lambda parent: subdirs)
    monkeypatch.setattr(switchdrive, "load_processed", lambda: set(processed or []))
    marked: list = []
    monkeypatch.setattr(switchdrive, "mark_processed", lambda oid: marked.append(oid))
    monkeypatch.setattr(switchdrive, "pull_folder",
                        lambda order, staging, recursive=False:
                        [Path(f) for f in files_map.get(order, [])])
    ran: list = []
    monkeypatch.setattr(ingest, "run_full_pipeline_group",
                        lambda doc_id, files: ran.append((doc_id, len(files))))
    return marked, ran


def test_done_skipped_empty_buckets(monkeypatch, tmp_path):
    marked, ran = _setup(
        monkeypatch, tmp_path,
        subdirs=["root/A", "root/B", "root/C"],
        files_map={"root/A": ["a/1.jpg", "a/2.jpg"], "root/C": ["c/1.jpg"]},  # B empty
        processed={"root__C"})                                                # C done
    res = ingest.run_switchdrive_orders("root")
    assert res["done"] == ["A (2p)"]
    assert res["skipped"] == ["root__C"]
    assert res["empty"] == ["root__B"]
    assert res["errors"] == []
    assert ("A", 2) in ran and marked == ["root__A"]


def test_error_is_isolated_batch_continues(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path,
           subdirs=["root/A", "root/B"],
           files_map={"root/A": ["a/1.jpg"], "root/B": ["b/1.jpg"]})

    def boom(doc_id, files):
        if doc_id == "A":
            raise RuntimeError("kaboom")
    monkeypatch.setattr(ingest, "run_full_pipeline_group", boom)

    res = ingest.run_switchdrive_orders("root")
    assert any("A:" in e for e in res["errors"])   # A errored, recorded
    assert res["done"] == ["B (1p)"]               # B still processed


def test_reprocess_ignores_processed_set(monkeypatch, tmp_path):
    marked, _ = _setup(monkeypatch, tmp_path,
                       subdirs=["root/A"], files_map={"root/A": ["a/1.jpg"]},
                       processed={"root__A"})
    res = ingest.run_switchdrive_orders("root", reprocess=True)
    assert res["done"] == ["A (1p)"] and res["skipped"] == []
    assert marked == ["root__A"]
