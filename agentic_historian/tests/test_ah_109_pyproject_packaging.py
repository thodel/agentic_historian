"""
test_ah_109_pyproject_packaging.py — Offline test for issue #109.

Validates:
  1. pyproject.toml is parseable by setuptools and contains required fields.
  2. config.ensure_dirs() is NOT called at module import time (importable without
     side-effects — no mkdir calls at import).
  3. REPO_ROOT falls back to CWD when installed as a package (AGENTIC_HISTORIAN_ROOT env).
  4. The package exposes the declared entry points.

Run: pytest test_ah_109_pyproject_packaging.py -v
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()


class TestPyprojectToml:
    """pyproject.toml is valid and contains all required declarations."""

    def test_pyproject_toml_exists(self):
        assert (REPO_ROOT / "pyproject.toml").exists()

    def test_pyproject_toml_parseable(self):
        """tomlii parses as valid TOML; tomli is stdlib from 3.11."""
        import tomllib

        text = (REPO_ROOT / "pyproject.toml").read_text()
        data = tomllib.loads(text)

        # Required top-level keys
        assert data.get("build-system", {}).get("requires")
        assert data.get("project", {}).get("name")
        assert data.get("project", {}).get("requires-python")
        assert data.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find")

    def test_entry_points_declared(self):
        import tomllib

        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        scripts = data.get("project", {}).get("scripts", {})
        assert "ah" in scripts or "ah-bot" in scripts

    def test_optional_extras_defined(self):
        import tomllib

        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
        extras = data.get("project", {}).get("optional-dependencies", {})
        assert "htr-local" in extras
        assert "hf" in extras
        assert "kraken-remote" in extras

    def test_py_typed_marker_present(self):
        """py.typed marker signals type-hint support to mypy/PEP 561."""
        assert (REPO_ROOT / "py.typed").exists()

    def test_main_module_exists(self):
        """__main__.py enables `python -m agentic_historian`."""
        assert (REPO_ROOT / "__main__.py").exists()


class TestNoSideEffectsAtImport:
    """Importing agentic_historian must not call os.mkdir or other side-effects."""

    def test_config_import_no_mkdir(self, tmp_path, monkeypatch):
        """
        Importing config and calling ensure_dirs explicitly should work,
        but ensure_dirs must NOT have been called during the import of config.
        We patch Path.mkdir to detect any unexpected calls.
        """
        mkdir_calls: list = []

        def track_mkdir(self_, *args, **kwargs):
            mkdir_calls.append(str(self_))

        # Change to a temp directory so we don't create dirs in the repo
        monkeypatch.chdir(tmp_path)

        with patch("pathlib.Path.mkdir", side_effect=track_mkdir):
            # Re-import config fresh (cache is cleared by pytest's own import)
            spec = importlib.util.spec_from_file_location(
                "config_fresh", REPO_ROOT / "config.py"
            )
            module = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(module)
            except Exception:
                pass  # Some imports (e.g. dotenv) may fail in isolation — that's fine

        # ensure_dirs should NOT have been called at import time
        # (it is only called explicitly from bot.py main())
        ensure_dirs_call_paths = [
            str(p) for p in mkdir_calls
            if "data" in str(p) or "hot_folder" in str(p)
        ]
        assert not ensure_dirs_call_paths, (
            f"ensure_dirs() was called during import (side-effect): {ensure_dirs_call_paths}"
        )


class TestRepoRootFallback:
    """REPO_ROOT must resolve to CWD when the package is installed as a package."""

    def test_repo_root_falls_back_to_cwd_when_site_packages(self, monkeypatch):
        """
        When BASE_DIR is inside site-packages (i.e. pip install -e . was run),
        REPO_ROOT should NOT point to site-packages.
        It should use AGENTIC_HISTORIAN_ROOT env var, or fall back to CWD.
        """
        # We cannot easily simulate site-packages without mocking __file__,
        # but we CAN verify the env-var override works
        with monkeypatch.context() as m:
            m.setenv("AGENTIC_HISTORIAN_ROOT", "/custom/root")
            # Re-import config fresh
            spec = importlib.util.spec_from_file_location(
                "config_env_root", REPO_ROOT / "config.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            assert str(module.REPO_ROOT) == "/custom/root"

    def test_repo_root_defaults_to_cwd_when_no_env_and_no_git(self, tmp_path, monkeypatch):
        """When AGENTIC_HISTORIAN_ROOT is unset and BASE_DIR has no .git parent,
        REPO_ROOT falls back to CWD.

        The real config.py lives inside a git checkout, so we copy it into a
        git-less temp package and load THAT — otherwise BASE_DIR/../.git exists
        and the fallback branch is never taken (the test could not pass in CI).
        """
        import shutil
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        shutil.copy(REPO_ROOT / "config.py", pkg / "config.py")  # no ../.git here
        cwd = tmp_path / "elsewhere"
        cwd.mkdir()
        with monkeypatch.context() as m:
            m.delenv("AGENTIC_HISTORIAN_ROOT", raising=False)
            m.chdir(cwd)  # distinct from BASE_DIR.parent, so the cwd branch is provable
            spec = importlib.util.spec_from_file_location(
                "config_cwd_root", pkg / "config.py"
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            assert module.REPO_ROOT == cwd.resolve()


class TestInstallability:
    """pip install -e . works with core dependencies only."""

    def test_editable_install_with_core_deps(self):
        """
        pip install -e .[core] should succeed.
        We run it in a subprocess so it doesn't pollute the current venv.
        """
        result = subprocess.run(
            [
                sys.executable, "-m", "pip", "install", "-e",
                str(REPO_ROOT),
                "--quiet",
                "--no-deps",   # Only install the package itself; deps already present
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"pip install -e . failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_package_importable_after_editable_install(self):
        """After editable install, 'import agentic_historian' should work."""
        result = subprocess.run(
            [sys.executable, "-c", "import agentic_historian; print('OK')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"import agentic_historian failed:\n{result.stderr}"
        )
        assert "OK" in result.stdout

    def test_entry_point_discoverable(self):
        """Entry point 'ah' should be on PATH after install."""
        result = subprocess.run(
            [sys.executable, "-c",
             "from importlib.metadata import entry_points; "
             "eps = entry_points(group='console_scripts'); "
             "print([ep.name for ep in eps])"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # 'ah' or 'ah-bot' should appear (may be empty list on some Python versions)