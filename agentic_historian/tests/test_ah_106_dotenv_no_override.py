"""Tests for #106: load_dotenv(override=True) stomps real environment variables.

Offline. Two layers:
  1. source guard — config.py must use override=False, never override=True;
  2. functional — replicate config's loading and prove real process env wins,
     unset vars still get filled, and .env.gpustack beats a generic .env.

Run from the repo root: pytest agentic_historian/tests/test_ah_106_dotenv_no_override.py
"""

import os
from pathlib import Path

from dotenv import load_dotenv

CFG_PATH = "agentic_historian/config.py"


def _read(path):
    with open(path) as f:
        return f.read()


# ── Source guard ─────────────────────────────────────────────────────────────

def test_config_uses_override_false():
    src = _read(CFG_PATH)
    assert "override=False" in src, "config.py must load dotenv with override=False"
    assert "override=True" not in src, (
        "config.py must NOT use override=True — it stomps real env vars (#106)"
    )


def test_gpustack_loaded_before_generic_env():
    """.env.gpustack must be listed before the generic .env files so that, with
    override=False (first-wins), the dedicated secrets keep precedence."""
    src = _read(CFG_PATH)
    g = src.find(".env.gpustack")
    # the generic REPO_ROOT / ".env" entry appears after .env.gpustack
    generic = src.find('REPO_ROOT / ".env",')
    assert g != -1 and generic != -1 and g < generic, (
        ".env.gpustack must load before the generic .env (first-wins precedence)"
    )


# ── Functional: the override=False semantics config now relies on ────────────

def test_real_env_not_stomped_by_dotenv(tmp_path, monkeypatch):
    """A var already in the process env must survive a dotenv file that sets it."""
    monkeypatch.setenv("AH_106_REAL", "from-systemd")
    env_file = tmp_path / ".env"
    env_file.write_text("AH_106_REAL=from-file\n")

    load_dotenv(env_file, override=False)

    assert os.environ["AH_106_REAL"] == "from-systemd", (
        "override=False must let the real process env win"
    )


def test_unset_var_is_filled_from_dotenv(tmp_path, monkeypatch):
    """A var NOT in the process env is still populated from the dotenv file."""
    monkeypatch.delenv("AH_106_UNSET", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("AH_106_UNSET=from-file\n")

    load_dotenv(env_file, override=False)

    assert os.environ.get("AH_106_UNSET") == "from-file"


def test_gpustack_wins_over_generic_when_loaded_first(tmp_path, monkeypatch):
    """With override=False, the file loaded FIRST wins for an unset var — so
    loading .env.gpustack before .env gives the secrets file precedence."""
    monkeypatch.delenv("AH_106_KEY", raising=False)
    gpustack = tmp_path / ".env.gpustack"
    generic = tmp_path / ".env"
    gpustack.write_text("AH_106_KEY=from-gpustack\n")
    generic.write_text("AH_106_KEY=from-generic\n")

    for f in (gpustack, generic):          # same order as config.py
        load_dotenv(f, override=False)

    assert os.environ["AH_106_KEY"] == "from-gpustack"
