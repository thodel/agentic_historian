"""Tests for #199: SwitchDrive consistency fixes + round-trip verification.

Run offline — webdav4 Client is patched, no real network calls.
Run from the repo root:
  pytest agentic_historian/tests/test_ah_199_switchdrive.py
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def patched_sd(tmp_path):
    """
    Provides a switchdrive module with:
    - Config attrs redirected to temp dirs
    - webdav4.Client patched to a MagicMock
    - _PROCESSED_FILE redirected to tmp_path

    Uses bare 'import config' matching the actual switchdrive.py pattern.
    PYTHONPATH must include the repo root so 'import config' resolves.
    """
    # Add repo root to sys.path so 'import config' works
    repo_root = Path(__file__).resolve().parent.parent  # .../agentic_historian
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    import config          # agentic_historian/agentic_historian/config.py
    import utils.switchdrive as sd  # agentic_historian/agentic_historian/utils/switchdrive.py

    # Save original values
    saved = {
        k: getattr(config, k, None)
        for k in (
            "SWITCHDRIVE_URL",
            "SWITCHDRIVE_USER",
            "SWITCHDRIVE_PASS",
            "SWITCHDRIVE_REMOTE_DIR",
            "DATA_DIR",
            "HOT_FOLDER",
        )
    }

    hot = tmp_path / "hot"
    hot.mkdir()

    # Apply patches
    config.SWITCHDRIVE_URL = "https://drive.switch.ch/remote.php/webdav"
    config.SWITCHDRIVE_USER = "testuser"
    config.SWITCHDRIVE_PASS = "testpass"
    config.SWITCHDRIVE_REMOTE_DIR = "test_root"
    config.DATA_DIR = tmp_path
    config.HOT_FOLDER = hot

    processed_file = tmp_path / "processed_orders.json"
    saved_processed = sd._PROCESSED_FILE
    sd._PROCESSED_FILE = processed_file

    with patch("webdav4.client.Client") as MockClientCls:
        mock_client = MagicMock()
        MockClientCls.return_value = mock_client
        yield {"sd": sd, "client": mock_client, "cfg": config, "processed_file": processed_file}

    # Restore
    for k, v in saved.items():
        setattr(config, k, v)
    sd._PROCESSED_FILE = saved_processed


# ─── is_configured ─────────────────────────────────────────────────────────────

def test_is_configured_all_present(patched_sd):
    assert patched_sd["sd"].is_configured() is True


def test_is_configured_missing_url(patched_sd):
    patched_sd["cfg"].SWITCHDRIVE_URL = ""
    assert patched_sd["sd"].is_configured() is False


def test_is_configured_missing_user(patched_sd):
    patched_sd["cfg"].SWITCHDRIVE_USER = ""
    assert patched_sd["sd"].is_configured() is False


def test_is_configured_missing_pass(patched_sd):
    patched_sd["cfg"].SWITCHDRIVE_PASS = ""
    assert patched_sd["sd"].is_configured() is False


# ─── pull_folder ───────────────────────────────────────────────────────────────

def test_pull_folder_skips_non_ingest_exts(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.return_value = [
        {"name": "test_root/doc.docx", "type": "file"},
        {"name": "test_root/001r.jpg", "type": "file"},
    ]
    result = sd.pull_folder("test_root")
    assert len(result) == 1
    assert result[0].suffix in sd.INGEST_EXTS
    assert not any("docx" in str(p) for p in result)


def test_pull_folder_collision_safe_path_encoding(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.return_value = [
        {"name": "test_root/saa-0428/001r.jpg", "type": "file"},
        {"name": "test_root/saa-0429/001r.jpg", "type": "file"},
    ]
    result = sd.pull_folder("test_root")
    names = {p.name for p in result}
    assert len(names) == 2
    assert "saa-0428__001r.jpg" in names
    assert "saa-0429__001r.jpg" in names


def test_pull_folder_respects_recursive_true(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.side_effect = [
        [{"name": "test_root/suborder", "type": "directory"}],
        [{"name": "test_root/suborder/page1.jpg", "type": "file"}],
    ]
    result = sd.pull_folder("test_root", recursive=True)
    assert len(result) == 1
    assert result[0].name == "suborder__page1.jpg"


def test_pull_folder_respects_recursive_false(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.return_value = [{"name": "test_root/top.jpg", "type": "file"}]
    result = sd.pull_folder("test_root", recursive=False)
    assert len(result) == 1
    assert result[0].name == "top.jpg"


# ─── list_subdirs ──────────────────────────────────────────────────────────────

def test_list_subdirs_returns_immediate_children(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.return_value = [
        {"name": "test_root/order-001", "type": "directory"},
        {"name": "test_root/order-002", "type": "directory"},
        {"name": "test_root/file.jpg", "type": "file"},
        {"name": "test_root", "type": "directory"},
    ]
    result = sd.list_subdirs("test_root")
    assert set(result) == {"test_root/order-001", "test_root/order-002"}


def test_list_subdirs_flat_folder_returns_remote_dir(patched_sd):
    sd, mc = patched_sd["sd"], patched_sd["client"]
    mc.ls.return_value = [{"name": "test_root/001r.jpg", "type": "file"}]
    result = sd.list_subdirs("test_root")
    assert result == ["test_root"]


# ─── load_processed / mark_processed round-trip ────────────────────────────────

def test_processed_round_trip(patched_sd):
    sd, pf = patched_sd["sd"], patched_sd["processed_file"]
    assert sd.load_processed() == set()
    sd.mark_processed("order-042")
    sd.mark_processed("order-137")
    assert sd.load_processed() == {"order-042", "order-137"}
    sd.mark_processed("order-042")
    assert sd.load_processed() == {"order-042", "order-137"}


def test_load_processed_missing_file_returns_empty_set(patched_sd):
    sd = patched_sd["sd"]
    nonexistent = patched_sd["processed_file"].parent / "nonexistent.json"
    with patch.object(sd, "_PROCESSED_FILE", nonexistent):
        assert sd.load_processed() == set()


# ─── _resolve_remote ───────────────────────────────────────────────────────────

def test_resolve_remote_full_path_unchanged(patched_sd):
    sd = patched_sd["sd"]
    assert sd._resolve_remote("test_root/some/subfolder") == "test_root/some/subfolder"


def test_resolve_remote_short_name_prepends_base(patched_sd):
    sd = patched_sd["sd"]
    assert sd._resolve_remote("my_folder") == "test_root/my_folder"


def test_resolve_remote_strips_slashes(patched_sd):
    sd = patched_sd["sd"]
    assert sd._resolve_remote("/test_root/subfolder/") == "test_root/subfolder"
