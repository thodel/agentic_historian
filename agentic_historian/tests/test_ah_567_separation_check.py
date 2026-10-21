"""#567: the separation is a statement about a host, so something has to ask it.

The Definition of Done reads: "the bot process cannot read the OpenClaw
credentials, runs hardened under systemd, and writes only into its data
directory". That is true or false of a running machine, and nothing measured it
— #567 and #571 were both closed with the repo side marked ✅ while **none** of
it was in force on tei: no hardening drop-in installed, no `EnvironmentFile`,
the service still running as `dh` (measured 13./14.10.2026).

So the check reads systemd's **effective** properties, not the drop-in file in
this checkout — a file that can sit in `/etc/systemd/system/<unit>.d/` without a
`daemon-reload`, and in a git checkout without being installed at all, is
exactly the evidence that failed here.

Two things are tested harder than the happy path, because both are how a check
like this goes quietly wrong:

* **`None` is not a pass.** Three values, as in `bot.security_posture`: holds,
  does not hold, and *nobody looked*. A host where nothing could be measured
  exits non-zero, because an empty check that passes is how this issue came to
  be closed the first time.
* **`ProtectHome` must not be demanded where it cannot hold.** The checkout is
  under `/home/dh`, so `ProtectHome=yes` would blind the service to its own
  code. A check that reports that as a failure teaches its reader to ignore it.

Offline: the systemd answer and the file modes are both injected, so every case
— including "systemctl is not there" — is producible.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "systemd"


def _load():
    spec = importlib.util.spec_from_file_location(
        "check_separation", DEPLOY / "check_separation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_separation"] = module
    spec.loader.exec_module(module)
    return module


check = _load()

HARDENED = {
    "User": "dh-bot", "Group": "dh-bot",
    "WorkingDirectory": "/home/dh/agentic_historian/agentic_historian",
    "ProtectSystem": "strict", "NoNewPrivileges": "yes", "PrivateTmp": "yes",
    "ProtectHome": "", "ReadWritePaths": "/home/dh/agentic_historian/data",
    "EnvironmentFiles": "/etc/dh-bot.env (ignore_errors=no)",
}

#: What tei actually reported on 13.10.2026: the unit exists, and none of it is on.
AS_MEASURED_ON_TEI = {
    "User": "dh", "Group": "dh",
    "WorkingDirectory": "/home/dh/agentic_historian/agentic_historian",
    "ProtectSystem": "", "NoNewPrivileges": "no", "PrivateTmp": "no",
    "ProtectHome": "", "ReadWritePaths": "", "EnvironmentFiles": "",
}


def _rows(properties, **kw):
    return {label: (ok, why) for label, ok, why in check.posture(properties, **kw)}


def _stat_for(modes: dict[str, tuple[int, int]]):
    """A stat that answers for the named paths and raises for everything else."""
    class Info:
        def __init__(self, mode, uid):
            self.st_mode, self.st_uid, self.st_gid = mode, uid, uid

    def stat(path):
        key = str(path)
        if key not in modes:
            raise OSError(2, "No such file or directory", key)
        return Info(*modes[key])

    return stat


# ── the state the issue was closed in ───────────────────────────────────────

def test_the_host_as_it_was_measured_fails_on_every_item(tmp_path):
    rows = _rows(AS_MEASURED_ON_TEI)
    held, failed, unmeasured = check.verdict(check.posture(AS_MEASURED_ON_TEI))

    assert held == 0, "nothing was in force, and the check must say so"
    assert failed >= 4, "User, ProtectSystem, NoNewPrivileges, PrivateTmp"
    assert any("dh" in label and ok is False for label, (ok, _why) in rows.items())


def test_a_hardened_host_holds_what_it_can_hold():
    rows = _rows(HARDENED)

    assert rows["ProtectSystem=strict"][0] is True
    assert rows["NoNewPrivileges=yes"][0] is True
    assert rows["PrivateTmp=yes"][0] is True
    assert rows["ReadWritePaths"][0] is True
    assert rows["User= (dh-bot)"][0] is True


# ── None is not a pass ──────────────────────────────────────────────────────

def test_no_systemd_is_one_unmeasured_row_and_no_verdict():
    rows = check.posture(None)

    assert [ok for _l, ok, _w in rows] == [None]
    assert check.verdict(rows) == (0, 0, 1)


def test_nothing_measured_exits_non_zero(monkeypatch, capsys):
    """An empty check that passes is how #567 was closed the first time."""
    monkeypatch.setattr(check, "unit_properties", lambda *a, **kw: None)

    code = check.main(["--unit", "agentic-historian"])

    assert code == 2
    assert "kein Bestehen" in capsys.readouterr().err


def test_the_openclaw_question_is_unmeasured_until_the_path_is_named():
    """Where those credentials live is host knowledge. Guessing a path and
    reporting "unreachable" would answer the Definition of Done with a guess."""
    rows = _rows(HARDENED)

    label, (ok, why) = next((name, v) for name, v in rows.items() if "OpenClaw" in name)
    assert ok is None
    assert "--openclaw-secrets" in why


def test_an_unreadable_secret_file_is_unmeasured_not_safe():
    """Run without root, the mode cannot be read — which is not the same as a
    file nobody else can read."""
    rows = _rows(HARDENED, secret_files=[Path("/etc/dh-bot.env")],
                 stat=_stat_for({}))

    assert rows["/etc/dh-bot.env"][0] is None
    assert "als root" in rows["/etc/dh-bot.env"][1]


# ── the Definition of Done, once the path is named ──────────────────────────

def test_credentials_the_bot_user_cannot_reach_are_the_done_condition():
    rows = _rows(HARDENED, openclaw_secrets=Path("/home/dh/.openclaw/creds.json"),
                 stat=_stat_for({"/home/dh/.openclaw/creds.json": (0o600, 1000)}))

    label, (ok, why) = next((name, v) for name, v in rows.items() if "OpenClaw" in name)
    assert ok is True
    assert "Definition of Done" in why


def test_credentials_owned_by_the_service_user_are_reachable(monkeypatch):
    """The case item 3 exists for: same user, so `0600` protects nothing.

    The uid is resolved through an injected name lookup rather than this box's
    passwd — the first version of this test asserted one thing if uid 1000
    happened to be called `dh` here and the opposite otherwise, which is a test
    that cannot fail.
    """
    monkeypatch.setattr(check, "_user_name", lambda uid: "dh" if uid == 1000 else str(uid))
    same_user = dict(HARDENED, User="dh")

    rows = _rows(same_user, openclaw_secrets=Path("/home/dh/.openclaw/creds.json"),
                 stat=_stat_for({"/home/dh/.openclaw/creds.json": (0o600, 1000)}))

    label, (ok, why) = next((name, v) for name, v in rows.items() if "OpenClaw" in name)
    assert ok is False, "0600 does not help when the reader owns the file"
    assert "er kann sie lesen" in why


def test_a_different_owner_with_0600_is_out_of_reach(monkeypatch):
    """The counter-test to the one above: the same mode, another owner."""
    monkeypatch.setattr(check, "_user_name", lambda uid: {1000: "dh", 1001: "dh-bot"}.get(uid, str(uid)))

    rows = _rows(HARDENED, openclaw_secrets=Path("/home/dh/.openclaw/creds.json"),
                 stat=_stat_for({"/home/dh/.openclaw/creds.json": (0o600, 1000)}))

    assert next(ok for name, (ok, _w) in rows.items() if "OpenClaw" in name) is True


def test_world_readable_credentials_are_reachable_whoever_owns_them():
    rows = _rows(HARDENED, openclaw_secrets=Path("/srv/creds.json"),
                 stat=_stat_for({"/srv/creds.json": (0o644, 0)}))

    label, (ok, why) = next((name, v) for name, v in rows.items() if "OpenClaw" in name)
    assert ok is False
    assert "0o644" in label


def test_a_group_readable_bot_secret_is_a_finding():
    rows = _rows(HARDENED, secret_files=[Path("/etc/dh-bot.env")],
                 stat=_stat_for({"/etc/dh-bot.env": (0o644, 0)}))

    label, (ok, why) = next((name, v) for name, v in rows.items() if "dh-bot.env" in name)
    assert ok is False
    assert "jeder Prozess" in why


# ── ProtectHome: not demanded where it cannot hold ──────────────────────────

def test_protect_home_is_not_a_failure_while_the_checkout_lives_under_home():
    """`/home/dh/agentic_historian` is the checkout. ProtectHome=yes would blind
    the service to its own code, so reporting its absence as a failure trains the
    reader to ignore the report."""
    rows = _rows(HARDENED)

    label, (ok, why) = next((name, v) for name, v in rows.items() if "ProtectHome" in name)
    assert ok is None
    assert "nicht anwendbar" in why


def test_protect_home_is_demanded_once_the_checkout_moves_off_home():
    moved = dict(HARDENED, WorkingDirectory="/srv/agentic_historian")
    rows = _rows(moved)

    label, (ok, why) = next((name, v) for name, v in rows.items() if "ProtectHome" in name)
    assert ok is False
    assert "hier wäre es zu haben" in why

    kept = dict(moved, ProtectHome="read-only")
    rows = _rows(kept)
    assert next(ok for name, (ok, _w) in rows.items() if "ProtectHome" in name) is True


# ── the properties come from systemd, not from this checkout ────────────────

def test_the_check_asks_systemd_and_not_the_drop_in_file(monkeypatch):
    """The whole point. A drop-in in the repo — or in /etc without a reload — is
    not evidence about the running service."""
    asked: list[list[str]] = []

    def fake_run(cmd):
        asked.append(cmd)
        return "ProtectSystem=strict\nUser=dh-bot\n"

    monkeypatch.setattr(check.shutil, "which", lambda _name: "/bin/systemctl")
    props = check.unit_properties("agentic-historian", run=fake_run)

    assert props == {"ProtectSystem": "strict", "User": "dh-bot"}
    assert asked and asked[0][:3] == ["systemctl", "show", "agentic-historian"]
    assert any(arg == "-pProtectSystem" for arg in asked[0])


def test_a_systemctl_that_fails_is_no_properties_rather_than_empty_ones(monkeypatch):
    """`{}` would run every row against missing values and report four failures
    about a host nobody asked."""
    monkeypatch.setattr(check.shutil, "which", lambda _name: "/bin/systemctl")

    assert check.unit_properties("x", run=lambda _cmd: None) is None
    assert check.unit_properties("x", run=lambda _cmd: "") is None


def test_without_systemctl_nothing_is_claimed(monkeypatch):
    monkeypatch.setattr(check.shutil, "which", lambda _name: None)

    assert check.unit_properties("agentic-historian") is None


# ── the report a person reads ───────────────────────────────────────────────

def test_the_three_values_are_marked_apart():
    rows = [("a", True, "hält"), ("b", False, "fehlt"), ("c", None, "ungemessen")]

    lines = check.format_rows(rows)

    assert "ok" in lines[0] and "FEHLT" in lines[1] and "?" in lines[2]
    assert len({line.split("]")[0] for line in lines}) == 3, (
        "two of the three states render alike")


def test_the_summary_names_the_unmeasured_count(monkeypatch, capsys):
    monkeypatch.setattr(check, "unit_properties", lambda *a, **kw: HARDENED)

    code = check.main([])
    out = capsys.readouterr().out

    assert "nicht gemessen" in out
    assert "Nicht gemessen heisst nicht erfüllt" in out
    assert code in (0, 1)
