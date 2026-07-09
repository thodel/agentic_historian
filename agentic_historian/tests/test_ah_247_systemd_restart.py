"""
test_ah_247_systemd_restart.py
Test the systemd Restart=always drop-in parses as valid unit syntax.
"""

import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DROPOUT_PATH = REPO_ROOT / "deploy" / "systemd" / "agentic-historian.service.d" / "restart.conf"


class TestSystemdRestartDropin:
    """Smoke tests: file parses, has required directives."""

    def test_file_exists(self):
        assert DROPOUT_PATH.exists(), f"Drop-in not found at {DROPOUT_PATH}"

    def test_parses_as_systemd_unit(self):
        """configparser with strict=False accepts NG-style [Section] headers."""
        cp = configparser.ConfigParser(allow_no_value=True, strict=False)
        cp.read(DROPOUT_PATH, encoding="utf-8")
        assert cp.sections() == ["Service"], f"Expected [Service], got {cp.sections()}"

    def test_restart_always(self):
        cp = configparser.ConfigParser(allow_no_value=True, strict=False)
        cp.read(DROPOUT_PATH, encoding="utf-8")
        assert cp.get("Service", "Restart") == "always"

    def test_restart_sec_is_positive_integer(self):
        cp = configparser.ConfigParser(allow_no_value=True, strict=False)
        cp.read(DROPOUT_PATH, encoding="utf-8")
        raw = cp.get("Service", "RestartSec")
        seconds = int(raw.strip())
        assert seconds >= 1, f"RestartSec should be >= 1 second, got {seconds}"

    def test_no_other_sections(self):
        cp = configparser.ConfigParser(allow_no_value=True, strict=False)
        cp.read(DROPOUT_PATH, encoding="utf-8")
        assert len(cp.sections()) == 1 and cp.sections()[0] == "Service"
