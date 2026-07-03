"""Tests for #97: run_hot_folder never reports successes.

The success branch moved the file and set result['moved_to'] but never appended
to `results`; only the except branch appended. So /hotfolder and /pull reported 0.

Offline: run_full_pipeline is mocked. Run from the repo root:
    pytest agentic_historian/tests/test_ah_97_hotfolder_success.py
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


def test_hotfolder_records_successful_runs(tmp_path, monkeypatch):
    import config, orchestrator

    hot = tmp_path / "hot"
    processed = tmp_path / "processed"
    hot.mkdir()
    processed.mkdir()
    (hot / "scan_001.jpg").write_bytes(b"\xff\xd8\xff")
    (hot / "scan_002.png").write_bytes(b"\x89PNG")
    (hot / "notes.txt").write_text("ignore me")  # non-image, must be skipped

    monkeypatch.setattr(config, "HOT_FOLDER", hot)
    monkeypatch.setattr(config, "PROCESSED_FOLDER", processed)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(
        orchestrator, "run_full_pipeline",
        lambda fp, *a, **k: {"doc_id": Path(fp).stem, "transcription": "ok"},
    )

    results = orchestrator.run_hot_folder()

    # Both images must be reported as successes (the bug returned []).
    assert len(results) == 2, f"expected 2 successes, got {len(results)}: {results}"
    doc_ids = {r["doc_id"] for r in results}
    assert doc_ids == {"scan_001", "scan_002"}
    for r in results:
        assert "moved_to" in r and "error" not in r
    # Files were moved out of the hot folder
    assert not (hot / "scan_001.jpg").exists()
    assert (processed / "scan_001.jpg").exists()


def test_hotfolder_still_reports_failures(tmp_path, monkeypatch):
    import config, orchestrator

    hot = tmp_path / "hot"
    processed = tmp_path / "processed"
    hot.mkdir()
    processed.mkdir()
    (hot / "bad.jpg").write_bytes(b"\xff\xd8\xff")

    monkeypatch.setattr(config, "HOT_FOLDER", hot)
    monkeypatch.setattr(config, "PROCESSED_FOLDER", processed)
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)

    def _boom(fp, *a, **k):
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(orchestrator, "run_full_pipeline", _boom)

    results = orchestrator.run_hot_folder()
    assert len(results) == 1
    assert results[0]["doc_id"] == "bad"
    assert "error" in results[0]
