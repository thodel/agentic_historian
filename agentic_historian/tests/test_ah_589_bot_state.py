"""#589: the bot can show its own state — commands, credentials, reload.

Three findings from the SwitchDrive 401 of 2026-10-09, one theme: a change that
looks applied and is not observable.

**A** — a new slash command was invisible for up to an hour, because the
commands are global and Discord propagates those slowly. There was no way to
tell "the code is missing" from "Discord is still rolling it out".

**B** — `config.py` reads the `.env` files at import, so a corrected password
never reached the running bot. And the obvious fix silently does nothing:
`load_dotenv(override=False)` writes the file's value into `os.environ` on the
first load, so every later load finds the key present and leaves it alone. The
test for that is the point of this file.

**C** — nothing watched the endpoint, so a dead passcode presented itself as a
mystery about a folder.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_589_bot_state.py
"""

import asyncio
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace


PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import config               # noqa: E402
import credential_watch as cw   # noqa: E402
import webdav_probe as wp   # noqa: E402

OK, FAILED, UNKNOWN = wp.OK, wp.FAILED, wp.UNKNOWN


def _probe(blocked=None, *, action="App-Passwort erneuern", detail="HTTP 401"):
    """A Probe blocked at one layer, or healthy."""
    checks = []
    for layer in wp.LAYERS:
        if blocked is None:
            checks.append(wp.Check(layer=layer, status=OK, detail="fine"))
        elif layer == blocked:
            checks.append(wp.Check(layer=layer, status=FAILED, detail=detail,
                                   action=action))
        elif wp.LAYERS.index(layer) < wp.LAYERS.index(blocked):
            checks.append(wp.Check(layer=layer, status=OK, detail="fine"))
        else:
            checks.append(wp.Check(layer=layer, status=UNKNOWN))
    return wp.Probe(label="SwitchDrive", endpoint="https://x/webdav",
                    checks=checks)


# ── A: a new command must be visible at once ─────────────────────────────────

def test_commands_are_guild_scoped_when_a_guild_is_named(monkeypatch, tmp_path):
    """Global commands take up to an hour to propagate; a named guild is
    immediate. `/pull_preflight` was merged, deployed and still absent."""
    script = textwrap.dedent(f"""
        import sys, os
        sys.path.insert(0, {str(PKG)!r})
        os.environ["DISCORD_GUILD_ID"] = "4242"
        import bot
        print("debug_guilds:", bot.bot.debug_guilds)
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, timeout=180)

    assert "debug_guilds: [4242]" in out.stdout, out.stderr[-2000:]


def test_commands_stay_global_when_no_guild_is_named():
    """An empty knob must not break a multi-server install."""
    import bot

    assert config.DISCORD_GUILD_ID is None
    assert bot.bot.debug_guilds is None


def test_the_knob_reads_zero_as_unset():
    assert config.DISCORD_GUILD_ID is None or isinstance(
        config.DISCORD_GUILD_ID, int)


# ── B: the reload, and the trap that would make it a no-op ───────────────────

def _isolated_config(tmp_path, env_text, preset_env=None):
    """Run config in a fresh interpreter with its own REPO_ROOT and env."""
    (tmp_path / ".env.gpustack").write_text(env_text, encoding="utf-8")
    script = textwrap.dedent(f"""
        import sys, os, json
        sys.path.insert(0, {str(PKG)!r})
        os.environ["AGENTIC_HISTORIAN_ROOT"] = {str(tmp_path)!r}
        for k, v in {dict(preset_env or {})!r}.items():
            os.environ[k] = v
        import config
        first = {{"PASS": config.SWITCHDRIVE_PASS,
                  "USER": config.SWITCHDRIVE_USER,
                  "pass_from_file": "SWITCHDRIVE_PASS" in config.ENV_SOURCE,
                  "user_from_file": "SWITCHDRIVE_USER" in config.ENV_SOURCE}}
        from pathlib import Path
        Path({str(tmp_path)!r}, ".env.gpustack").write_text(
            "SWITCHDRIVE_PASS=neu\\nSWITCHDRIVE_USER=aus-der-datei\\n")
        result = config.reload_env()
        print(json.dumps({{"first": first, "result": result,
                           "after": {{"PASS": config.SWITCHDRIVE_PASS,
                                     "USER": config.SWITCHDRIVE_USER}}}}))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-3000:]
    import json
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_changed_file_value_actually_arrives(tmp_path):
    """The whole point. `importlib.reload` alone reports success and changes
    nothing, because `load_dotenv(override=False)` already put the first value
    into os.environ and will not replace it."""
    got = _isolated_config(tmp_path, "SWITCHDRIVE_PASS=alt\n")

    assert got["first"]["PASS"] == "alt"
    assert got["after"]["PASS"] == "neu", "the reload was a no-op"
    assert got["result"]["changed"] == ["SWITCHDRIVE_PASS"]


def test_a_value_from_the_process_environment_is_not_touched(tmp_path):
    """#106: the real environment must win over every `.env` file. A reload that
    did not distinguish them would overwrite a systemd secret with a committed
    template."""
    got = _isolated_config(tmp_path, "SWITCHDRIVE_PASS=alt\n",
                           preset_env={"SWITCHDRIVE_USER": "aus-der-umgebung"})

    assert got["first"]["user_from_file"] is False
    assert got["after"]["USER"] == "aus-der-umgebung", \
        "the file overwrote an environment value"
    assert "SWITCHDRIVE_USER" not in got["result"]["changed"]


def test_an_environment_key_is_reported_as_unchangeable_from_here(tmp_path):
    """"I reloaded and nothing changed" and "that key cannot be reloaded from
    here" are different answers, and only the second says where to go."""
    got = _isolated_config(tmp_path, "SWITCHDRIVE_PASS=alt\n",
                           preset_env={"SWITCHDRIVE_USER": "aus-der-umgebung"})

    assert "SWITCHDRIVE_USER" in got["result"]["from_environment"]


def test_the_result_names_keys_and_never_values(tmp_path):
    """This is posted into a channel."""
    got = _isolated_config(tmp_path, "SWITCHDRIVE_PASS=alt-geheim\n")
    flat = repr(got["result"])

    assert "alt-geheim" not in flat and "neu" not in flat
    assert set(got["result"]) == {"changed", "unchanged", "from_environment"}


def test_a_key_that_vanished_from_the_file_keeps_its_running_value(monkeypatch,
                                                                   tmp_path):
    """A reload may not be a way to lose configuration mid-run."""
    monkeypatch.setattr(config, "ENV_FILES", (tmp_path / ".env.gpustack",))
    monkeypatch.setattr(config, "ENV_SOURCE",
                        {"AH_TEST_KEY": tmp_path / ".env.gpustack"})
    monkeypatch.setitem(os.environ, "AH_TEST_KEY", "lebt")
    (tmp_path / ".env.gpustack").write_text("OTHER=1\n", encoding="utf-8")

    result = config.reload_env()

    assert os.environ["AH_TEST_KEY"] == "lebt"
    assert "AH_TEST_KEY" not in result["changed"]


def test_reload_keeps_env_source_usable_for_the_next_reload(monkeypatch,
                                                             tmp_path):
    """Re-executing the module would rebuild ENV_SOURCE from an os.environ that
    now holds the file values, so every key would look environment-sourced — the
    reload would destroy what makes the next one possible."""
    monkeypatch.setattr(config, "ENV_FILES", (tmp_path / ".env.gpustack",))
    monkeypatch.setattr(config, "ENV_SOURCE",
                        {"AH_TEST_KEY2": tmp_path / ".env.gpustack"})
    (tmp_path / ".env.gpustack").write_text("AH_TEST_KEY2=a\n", encoding="utf-8")

    config.reload_env()

    assert "AH_TEST_KEY2" in config.ENV_SOURCE


# ── C: the watcher announces a state, not a reading ──────────────────────────

def test_a_healthy_probe_says_nothing():
    notice, state = cw.decide(cw.WatchState(), _probe(), now=0.0)

    assert notice is None and state.blocked == "" and not state.told


def test_a_fresh_failure_waits_out_the_grace():
    """One failed request is not a dead passcode. A message per blip is how a
    channel gets muted."""
    notice, state = cw.decide(cw.WatchState(), _probe("auth"), now=100.0)

    assert notice is None
    assert state.blocked == "auth" and state.since == 100.0 and not state.told


def test_a_held_failure_is_announced_once():
    _n, state = cw.decide(cw.WatchState(), _probe("auth"), now=0.0)
    first, state = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)
    second, state = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 999)

    assert first is not None and first.kind == "blocked" and first.urgent
    assert second is None, "the report that fires every poll is the one nobody reads"


def test_a_restart_into_a_broken_passcode_still_speaks_up():
    """Deliberately unlike `atr_watch`, which stays silent on first sight
    because a cold start would paste forty jobs into the channel. Here there is
    one binary state, so there is no flood to prevent — and seeding silently
    would hide a live outage, which is the thing this file exists to stop."""
    notice, state = cw.decide(cw.load_state(Path("/nonexistent/x.json")),
                              _probe("auth"), now=0.0)
    assert notice is None                                  # grace, not silence

    notice, _ = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)
    assert notice is not None, "a bot that restarts into a 401 never says so"


def test_recovery_is_announced_once_and_only_if_the_break_was():
    _n, state = cw.decide(cw.WatchState(), _probe("auth"), now=0.0)
    told, state = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)
    back, state = cw.decide(state, _probe(), now=cw.GRACE_S + 2)
    again, _ = cw.decide(state, _probe(), now=cw.GRACE_S + 3)

    assert told is not None
    assert back is not None and back.kind == "recovered" and not back.urgent
    assert again is None


def test_a_recovery_nobody_was_told_about_is_not_announced():
    """A blip inside the grace must not produce a cheerful all-clear for a
    problem the channel never heard about."""
    _n, state = cw.decide(cw.WatchState(), _probe("network"), now=0.0)
    back, _ = cw.decide(state, _probe(), now=10.0)

    assert back is None


def test_a_different_layer_gets_its_own_grace():
    """A network outage that turns into a rejected passcode is a new condition,
    not the old one continuing — inheriting the elapsed time would announce it
    instantly."""
    _n, state = cw.decide(cw.WatchState(), _probe("network"), now=0.0)
    notice, state = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)

    assert notice is None
    assert state.blocked == "auth" and state.since == cw.GRACE_S + 1


def test_the_message_carries_the_action_not_the_status_code():
    _n, state = cw.decide(cw.WatchState(), _probe("auth"), now=0.0)
    notice, _ = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)

    assert "App-Passwort erneuern" in notice.text
    assert "/pull_preflight" in notice.text
    assert "auth" in notice.text


def test_the_message_never_carries_the_secret(monkeypatch):
    monkeypatch.setattr(config, "SWITCHDRIVE_PASS", "geheimnis-23-zeichen")
    _n, state = cw.decide(cw.WatchState(), _probe("auth"), now=0.0)
    notice, _ = cw.decide(state, _probe("auth"), now=cw.GRACE_S + 1)

    assert "geheimnis-23-zeichen" not in notice.text


def test_the_state_survives_a_round_trip(tmp_path):
    state = cw.WatchState(blocked="auth", since=123.5, told=True)
    cw.save_state(state, tmp_path / "s.json")

    assert cw.load_state(tmp_path / "s.json") == state


def test_an_unreadable_state_costs_the_memory_and_not_the_poll(tmp_path):
    (tmp_path / "s.json").write_text("{ not json", encoding="utf-8")

    assert cw.load_state(tmp_path / "s.json") == cw.WatchState()


def test_the_state_is_saved_after_the_message_not_before():
    """A crash between deciding and posting must repeat a message, not lose one:
    `decide` returns a new state and mutates nothing."""
    before = cw.WatchState(blocked="auth", since=0.0)
    _n, after = cw.decide(before, _probe("auth"), now=cw.GRACE_S + 1)

    assert before.told is False, "decide mutated the state it was given"
    assert after.told is True and after is not before


def test_the_watcher_is_off_unless_a_channel_is_chosen():
    """Right for any host that is not the one doing the ingesting."""
    assert config.ENABLE_CREDENTIAL_WATCH == (
        config.CREDENTIAL_WATCH_CHANNEL_ID is not None)


# ── the commands ─────────────────────────────────────────────────────────────

class _Ctx:
    def __init__(self):
        self.sent = []
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=7, roles=[])

        async def _defer(ephemeral=False):
            pass

        async def _send(content=None, ephemeral=False, **kw):
            self.sent.append(content)
            return SimpleNamespace(id=1, content=content)

        self.defer = _defer
        self.followup = SimpleNamespace(send=_send)


def _cmd(name):
    import bot
    return next(c for c in bot.bot.pending_application_commands
                if c.name == name).callback


def test_env_reload_is_registered_and_admin_gated():
    import bot
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    start = src.find('name="env_reload",')

    assert any(c.name == "env_reload"
               for c in bot.bot.pending_application_commands)
    assert start != -1
    assert "@admin_only" in src[start:src.find("async def", start)]


def test_env_reload_shows_the_preflight_so_success_is_shown_not_claimed(
        monkeypatch):
    """A reload that reports success and changed nothing is the defect this
    command exists to avoid, so the proof is attached."""
    from utils import switchdrive
    monkeypatch.setattr(config, "reload_env",
                        lambda: {"changed": ["SWITCHDRIVE_PASS"],
                                 "unchanged": [],
                                 "from_environment": ["ATR_API_KEY"]})
    monkeypatch.setattr(switchdrive, "preflight",
                        lambda folder=None, **kw: _probe())

    ctx = _Ctx()
    asyncio.run(_cmd("env_reload")(ctx))
    body = "\n".join(c for c in ctx.sent if c)

    assert "SWITCHDRIVE_PASS" in body
    assert "✅ **auth**" in body, "the preflight was not attached"
    assert "ATR_API_KEY" in body and "#106" in body


def test_env_reload_says_plainly_when_nothing_changed(monkeypatch):
    from utils import switchdrive
    monkeypatch.setattr(config, "reload_env",
                        lambda: {"changed": [], "unchanged": ["A", "B"],
                                 "from_environment": []})
    monkeypatch.setattr(switchdrive, "preflight",
                        lambda folder=None, **kw: _probe())

    ctx = _Ctx()
    asyncio.run(_cmd("env_reload")(ctx))

    assert "Geändert: nichts" in "\n".join(c for c in ctx.sent if c)


def test_the_watch_loop_exists_and_is_started_on_ready():
    src = (PKG / "bot.py").read_text(encoding="utf-8")

    assert "async def _credential_watch_loop" in src
    assert "ENABLE_CREDENTIAL_WATCH" in src
    assert "_credential_watch_loop())" in src


def test_the_watch_loop_probes_the_credentials_and_not_a_folder():
    """A folder that was renamed is not a credential problem; `/pull` finds that
    out when somebody actually pulls."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")
    start = src.find("async def _credential_watch_loop")
    body = src[start:src.find("# ── Commands", start)]

    # Since #592 through ``ingest_mailbox``, so the watcher watches the way
    # actually in use — watching the account path while the ingest reads a share
    # would announce a credential nothing uses and stay silent about the one
    # that broke. The ``None`` is the point this test has always made: no target
    # folder, because a renamed folder is not a credential problem.
    assert "ingest_mailbox.preflight, None" in body


def test_nothing_takes_the_password_through_discord():
    """A slash-command parameter and a modal field are both transmitted to
    Discord and kept in its interaction logs; `ephemeral` is not secrecy. And it
    would give the bot write access to its own credential store."""
    src = (PKG / "bot.py").read_text(encoding="utf-8")

    for forbidden in ("SWITCHDRIVE_PASS=", "discord.ui.Modal", "InputText"):
        assert forbidden not in src, f"{forbidden} suggests a secret input path"
    start = src.find('name="env_reload",')
    signature = src[src.find("async def", start):src.find('"""', start)]
    assert "Option(" not in signature, "/env_reload must take no parameters"
