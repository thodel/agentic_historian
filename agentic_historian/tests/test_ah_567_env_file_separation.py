"""Tests for S2 (#567): the bot reads its OWN secrets, not the shared .env.gpustack.

The bot and the OpenClaw agent `dh-bot` share `.env.gpustack`, which also holds
OpenClaw's mail / calendar / SSH credentials. With `AH_ENV_FILE` set, config.py
reads ONLY that file for its file-sourced secrets and never falls back to
`.env.gpustack`, so a dedicated `/etc/dh-bot.env` can carry just the bot's keys.

Offline: `_select_env_files` reads os.environ at call time, so the behaviour is
checked without re-importing config.
"""

import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config  # noqa: E402

EXAMPLE = REPO_ROOT / "deploy/systemd/dh-bot.env.example"
DROPIN = REPO_ROOT / "deploy/systemd/agentic-historian.service.d/secrets.conf"

BOT_SECRETS = [
    "DISCORD_BOT_TOKEN", "GPUSTACK_API_KEY", "ATR_API_KEY", "GITHUB_TOKEN",
    "SWITCHDRIVE_PASS", "NEXTCLOUD_SHARE_PASS", "HF_TOKEN",
]


def test_default_reads_the_shared_gpustack_file(monkeypatch):
    monkeypatch.delenv("AH_ENV_FILE", raising=False)
    files = config._select_env_files()
    assert any(p.name == ".env.gpustack" for p in files)


def test_a_dedicated_env_file_excludes_the_shared_one(monkeypatch):
    monkeypatch.setenv("AH_ENV_FILE", "/etc/dh-bot.env")
    files = config._select_env_files()
    assert files == (Path("/etc/dh-bot.env"),)
    assert not any(p.name == ".env.gpustack" for p in files), \
        "with AH_ENV_FILE the bot must not fall back to the shared .env.gpustack"


def test_blank_ah_env_file_is_ignored(monkeypatch):
    monkeypatch.setenv("AH_ENV_FILE", "   ")
    files = config._select_env_files()
    assert any(p.name == ".env.gpustack" for p in files)


def test_the_example_lists_the_bot_secrets_and_warns_off_openclaw():
    text = EXAMPLE.read_text(encoding="utf-8")
    for key in BOT_SECRETS:
        assert key in text, f"{key} missing from dh-bot.env.example"
    assert "do NOT copy" in text or "do not copy" in text.lower()


def test_the_dropin_isolates_the_env(monkeypatch):
    text = DROPIN.read_text(encoding="utf-8")
    assert "Environment=AH_ENV_FILE=/etc/dh-bot.env" in text
    assert "EnvironmentFile=/etc/dh-bot.env" in text
    # the empty reset that drops any inherited (shared) EnvironmentFile
    assert "\nEnvironmentFile=\n" in text
