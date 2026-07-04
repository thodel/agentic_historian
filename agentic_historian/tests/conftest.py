"""conftest.py — pytest fixtures for RunState tests."""
from __future__ import annotations
import pytest
from pathlib import Path
import sys
sys.path.insert(0, "/home/dh/.openclaw/workspace/agentic_historian")


@pytest.fixture(autouse=True)
def runs_dir(tmp_path: Path, monkeypatch):
    """Auto-applied: redirect _runs_dir() to a temp dir for all save/load tests."""
    import run_state
    fake_dir = tmp_path / "runs"
    fake_dir.mkdir(parents=True, exist_ok=True)

    def _fake_runs_dir() -> Path:
        return fake_dir

    monkeypatch.setattr(run_state, "_runs_dir", _fake_runs_dir)
    return fake_dir
