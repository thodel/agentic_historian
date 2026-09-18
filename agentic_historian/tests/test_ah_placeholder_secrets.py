"""A secret that is still a template (`config.unfilled_secrets`).

A *missing* secret announces itself: the variable is empty and the call fails
with 401. A secret whose value is the literal string `<Passwort>` is set, is
truthy, passes every "is it configured" test in this repo, and fails at the far
end of a network call with an error about the server.

It also hides behind the load order. The first file to define a key wins
(`override=False`), so a committed template shadows the real value in a file
loaded later and nothing says so. On tei (2026-09-18) `.env.gpustack` and `.env`
both carried `<Passwort>` while `agentic_historian/.env` had the real one; the
davfs2 mount failed with "rejected Basic challenge" and the password looked
present in every check anyone thought to run.
"""

import importlib
import os
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


@pytest.fixture
def fresh_config(tmp_path, monkeypatch):
    """Import `config` against a throwaway repo root, with a clean environment.

    Reloaded rather than reused: the load order is executed at import, and the
    behaviour under test *is* the load order.

    The **original** module object is put back afterwards, not merely evicted.
    Other suites hold a reference to it from their own import and patch it with
    ``monkeypatch.setattr(config, …)``; leaving a second, differently-configured
    module under the same name means those patches land on an object nobody is
    reading (`test_kh_mcp_registry` fails exactly this way, and only when this
    file runs first).
    """
    original = sys.modules.get("config")

    def _load(files: dict[str, str], environ: dict[str, str] | None = None):
        for name, body in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        for key in ("NEXTCLOUD_SHARE_PASS", "GITHUB_TOKEN", "ATR_API_KEY"):
            monkeypatch.delenv(key, raising=False)
        for key, value in (environ or {}).items():
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("AGENTIC_HISTORIAN_ROOT", str(tmp_path))
        sys.modules.pop("config", None)
        return importlib.import_module("config")

    yield _load

    if original is not None:
        sys.modules["config"] = original
    else:
        sys.modules.pop("config", None)


TEMPLATE = "NEXTCLOUD_SHARE_PASS=<Passwort>\n"
REAL = "NEXTCLOUD_SHARE_PASS=hunter2\n"


def test_a_template_value_is_reported(fresh_config):
    cfg = fresh_config({".env": TEMPLATE})

    found = cfg.unfilled_secrets()

    assert "NEXTCLOUD_SHARE_PASS" in found
    assert "<Passwort>" in found["NEXTCLOUD_SHARE_PASS"]


def test_it_names_the_file_to_edit(fresh_config):
    """Three copies of the variable is the case this exists for; knowing which
    one wins is the whole problem."""
    cfg = fresh_config({
        ".env.gpustack": TEMPLATE,
        ".env": TEMPLATE,
        "agentic_historian/.env": REAL,
    })

    why = cfg.unfilled_secrets()["NEXTCLOUD_SHARE_PASS"]

    assert ".env.gpustack" in why                     # the one that wins
    assert cfg.NEXTCLOUD_SHARE_PASS == "<Passwort>"   # and it really did


def test_a_real_value_in_the_first_file_is_not_reported(fresh_config):
    cfg = fresh_config({".env.gpustack": REAL, ".env": TEMPLATE})

    assert cfg.unfilled_secrets() == {}
    assert cfg.NEXTCLOUD_SHARE_PASS == "hunter2"


def test_the_process_environment_still_wins_over_a_template(fresh_config):
    """A systemd `Environment=` beats every file, template or not (#106)."""
    cfg = fresh_config({".env.gpustack": TEMPLATE},
                       environ={"NEXTCLOUD_SHARE_PASS": "from-systemd"})

    assert cfg.NEXTCLOUD_SHARE_PASS == "from-systemd"
    assert cfg.unfilled_secrets() == {}


@pytest.mark.parametrize("value", [
    "<Passwort>", "<your-token>", "changeme", "CHANGE_ME", "your-api-key",
    "TODO", "xxx", "xxxxxx", "...",
])
def test_the_usual_ways_of_writing_not_yet(fresh_config, value):
    cfg = fresh_config({".env": f"GITHUB_TOKEN={value}\n"})

    assert "GITHUB_TOKEN" in cfg.unfilled_secrets()


@pytest.mark.parametrize("value", [
    "hunter2", "ghp_abc123", "a<b>c", "yourname", "xx",
    "sk-proj-Todo1234", "Password<1>",
])
def test_a_real_secret_is_not_mistaken_for_one(fresh_config, value):
    """False positives cost trust in the check, which is worse than no check."""
    cfg = fresh_config({".env": f"GITHUB_TOKEN={value}\n"})

    assert cfg.unfilled_secrets() == {}


@pytest.mark.parametrize("value", [
    "<@817396581317738546>",       # a user mention — the bot's own, on tei
    "<@!817396581317738546>",      # a nickname mention
    "<@&1234567890>",              # a role
    "<#1234567890>",               # a channel
    "<:kurrent:1234567890>",       # a custom emoji
    "<noreply@example.org>",       # an addr-spec in brackets
    "<1234567890>",
])
def test_angle_brackets_are_not_enough_to_be_a_placeholder(fresh_config, value):
    """The first version of this check reported the bot's own Discord mention as
    an unfilled template, the first time it ran on tei. Brackets are how Discord
    writes every id it has; a placeholder is brackets around a *word*."""
    cfg = fresh_config({".env": f"ATR_WATCH_MENTION={value}\n"})

    assert cfg.unfilled_secrets() == {}


def test_an_empty_value_is_missing_not_a_template(fresh_config):
    """Empty is the case that already reports itself; this is for the other one."""
    cfg = fresh_config({".env": "NEXTCLOUD_SHARE_PASS=\n"})

    assert cfg.unfilled_secrets() == {}


def test_check_config_surfaces_it_alongside_missing_tokens(fresh_config):
    cfg = fresh_config({".env": "DISCORD_BOT_TOKEN=x\nGPUSTACK_API_KEY=y\n"
                                "NEXTCLOUD_SHARE_PASS=<Passwort>\n"})

    problems = cfg.check_config()

    assert any(p.startswith("NEXTCLOUD_SHARE_PASS: still a template")
               for p in problems), problems
