"""#802 step 6 and 7 — ``deploy/hermes/decommission_openclaw.sh``.

The script runs once on tei, after the observation week, as root over two
homes (``dh``'s OpenClaw files, ``hermes``' imported skill). It cannot be tried
there first, so these tests run it against two fake homes with every host
command stubbed on ``PATH`` (the ``snsf_check.sh`` pattern in test_ah_803):

* the dry run changes nothing, measures what the runbook needs, and prints the
  irreversible steps as commands instead of running them;
* ``--apply`` stops before the first change while OpenClaw runs, is enabled,
  or the week is not over — and otherwise archives with a date, renames, moves
  the old skill aside, and leaves the workspace itself for the human;
* no mode ever prints a secret value — the inventory names fields, and
  ``--verify-gone`` counts lines.
"""

from __future__ import annotations

import getpass
import json
import os
import stat
import subprocess
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "hermes" / "decommission_openclaw.sh"
TODAY = "2026-11-13"

#: Values the fake homes hold. None of them may ever reach stdout or stderr.
SECRETS = {
    "gpustack": "gpu-OLDVALUE-7f3a9c1e5b",
    "github": "gh-OLDVALUE-91c2e7aa04",
    "discord": "dc-OLDVALUE-55aa77bb99",
    "mail": "mail-OLDVALUE-pass-1234",
    "pairing": "pair-OLDVALUE-0099887766",
    "hermes": "hm-OLDVALUE-gpu-abcdef12",
    "cron": "cron-OLDVALUE-tok-424242",
}

STUBS = {
    # systemctl --user is-active/is-enabled/show; state from the test
    "systemctl": """#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
case "$*" in
  *is-active*) echo "${FAKE_ACTIVE:-inactive}"; [ "${FAKE_ACTIVE:-inactive}" = active ] ;;
  *is-enabled*) echo "${FAKE_ENABLED:-disabled}"; [ "${FAKE_ENABLED:-disabled}" = enabled ] ;;
  *FragmentPath*) echo "$FAKE_UNIT_FILE" ;;
esac
""",
    # a node process of another user carries a token in its arguments
    "ps": """#!/usr/bin/env bash
case "$*" in
  *pid=*) printf '%s\\n' "${FAKE_PS_PIDS:-}" ;;
  *) printf '%s\\n' "${FAKE_PS_USERS:-}" ;;
esac
""",
    "dpkg-query": """#!/usr/bin/env bash
case "$*" in *Status*) echo "install ok installed" ;; *) echo "22.11.0-1nodesource1" ;; esac
""",
    "apt-cache": """#!/usr/bin/env bash
printf 'nodejs\\nReverse Depends:\\n%s' "${FAKE_RDEPENDS:-}"
""",
    "npm": """#!/usr/bin/env bash
echo "npm $*" >> "$STUB_LOG"
case "$*" in
  "prefix -g") echo "$FAKE_NPM_PREFIX" ;;
  ls*) printf '%s\\n' "$FAKE_NPM_PREFIX/lib" "$FAKE_NPM_PREFIX/lib/node_modules/openclaw" \\
        "$FAKE_NPM_PREFIX/lib/node_modules/corepack" "$FAKE_NPM_PREFIX/lib/node_modules/@scope/tool" ;;
  uninstall*) echo "STUB: npm uninstall must never run" >&2; exit 99 ;;
esac
""",
    "openclaw": "#!/usr/bin/env bash\necho 'STUB: openclaw must never run' >&2; exit 99\n",
    "hermes": """#!/usr/bin/env bash
echo "hermes $*" >> "$STUB_LOG"
case "$*" in
  "cron list") printf '%s\\n' \\
    "0466f6a110f5  dh-bot-queue  0 13,17,21 * * *  ok" \\
    "8f6c1ac36165  dh-digest     0 16 * * 0        ok" \\
    "ad898cb7126f  repo-status   0 15 * * 2        ok" \\
    "b82a9a2fe5ca  snsf-grants   0 9 25 3,9 *      ${FAKE_SNSF_STATUS:-ok}" ;;
  "skills list") ls "$HOME_H/.hermes/skills/"* 2>/dev/null ;;
  "claw cleanup") echo "cleanup ran" ;;
esac
""",
    "crontab": """#!/usr/bin/env bash
printf '%s\\n' "${FAKE_CRONTAB:-}"
""",
    "apt": "#!/usr/bin/env bash\necho 'STUB: apt must never run' >&2; exit 99\n",
    "sudo": "#!/usr/bin/env bash\necho 'STUB: sudo must never run here' >&2; exit 99\n",
}


@pytest.fixture
def host(tmp_path):
    """Two fake homes as tei has them on 13.11.2026, and a runner."""
    me = getpass.getuser()
    dh, hm = tmp_path / "dh", tmp_path / "hermes"
    oc = dh / ".openclaw"
    (oc / "credentials").mkdir(parents=True)
    (oc / "tmp").mkdir()
    (oc / "openclaw.json").write_text(json.dumps({
        "channels": {"discord": {"token": SECRETS["discord"], "groupPolicy": "open"}},
        "models": {"providers": {"gpustack": {"baseUrl": "https://gpustack.unibe.ch/v1",
                                              "apiKey": SECRETS["gpustack"]}}},
        "skills": {"mail": {"password": SECRETS["mail"], "host": "mail.example"}}}))
    (oc / "credentials" / "discord-pairing.json").write_text(json.dumps({"pairingToken": SECRETS["pairing"]}))
    (oc / "tmp" / ".gh_token").write_text(SECRETS["github"] + "\n")
    ws = dh / "openclaw-workspace"
    (ws / "proj").mkdir(parents=True)
    (ws / "proj" / "notes.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(ws / "proj")], check=True)
    (dh / "agentic_historian").mkdir()
    (dh / "agentic_historian" / ".env.gpustack").write_text(
        f"GPUSTACK_API_KEY={SECRETS['gpustack']}\nGITHUB_TOKEN=\"{SECRETS['github']}\"\n"
        "GPUSTACK_BASE_URL=https://gpustack.unibe.ch/v1\nENABLE_HLS_LOOKUP=true\n")
    skill = hm / ".hermes" / "skills" / "openclaw-imports" / "batch-issue-processor"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: batch-issue-processor\n---\n")
    (hm / ".hermes" / "skills" / "dh-bot-queue").mkdir()
    (hm / ".hermes" / ".env").write_text(f"GPUSTACK_API_KEY={SECRETS['hermes']}\n")
    out = hm / ".hermes" / "cron" / "output" / "dh-digest"
    out.mkdir(parents=True)
    (out / "2026-11-08_1600.md").write_text("digest\n")
    system_env = tmp_path / "agentic_historian.env"
    system_env.write_text(f"GPUSTACK_API_KEY={SECRETS['gpustack']}\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        f = bin_dir / name
        f.write_text(body)
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "stub.log"
    log.touch()
    unit = dh / ".config" / "systemd" / "user" / "openclaw-gateway.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("[Service]\nExecStart=/usr/bin/node openclaw gateway\n")

    def run(*args, **env):
        e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "STUB_LOG": str(log),
             "HOME_H": str(hm),
             "DECOM_OC_USER": me, "DECOM_HERMES_USER": me,
             "DECOM_OC_HOME": str(dh), "DECOM_HERMES_HOME": str(hm),
             "DECOM_HERMES_BIN": str(bin_dir / "hermes"), "DECOM_TODAY": TODAY,
             "DECOM_SYSTEM_ENV": str(system_env),
             "FAKE_UNIT_FILE": str(unit), "FAKE_NPM_PREFIX": str(tmp_path / "npm"),
             # another user's node process; its arguments carry a secret
             "FAKE_PS_USERS": f"www-data node node /srv/app.js --token {SECRETS['cron']}",
             "FAKE_PS_PIDS": f"4242 www-data node node /srv/app.js --token {SECRETS['cron']}",
             "FAKE_CRONTAB": f"0 3 * * * cd {ws}/proj && TOKEN={SECRETS['cron']} ./run.sh",
             **env}
        r = subprocess.run(["bash", str(SCRIPT), *args], env=e, capture_output=True, text=True, check=False)
        r.combined = r.stdout + r.stderr
        for name, value in SECRETS.items():
            assert value not in r.combined, f"secret {name} printed"
        return r

    run.dh, run.hermes, run.ws, run.oc, run.skill, run.log = dh, hm, ws, oc, skill, log
    run.system_env, run.tmp = system_env, tmp_path
    return run


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted((str(p.relative_to(root)), p.stat().st_mode) for p in root.rglob("*")
                  if "stub.log" not in p.name)


def test_the_script_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], check=False).returncode == 0
    assert os.access(SCRIPT, os.X_OK) or SCRIPT.read_text().startswith("#!/usr/bin/env bash")


def test_the_dry_run_changes_nothing_and_measures_what_the_runbook_needs(host):
    before = _tree(host.tmp)
    r = host()
    assert r.returncode == 0, r.combined
    assert _tree(host.tmp) == before
    out = r.stdout
    assert "Trockenlauf" in out and "Nichts geändert" in out
    assert "openclaw-gateway: inactive" in out and "openclaw-gateway: disabled" in out
    assert "lib/node_modules" not in out.split("globale Pakete:")[1].splitlines()[0]
    assert "openclaw" in out.split("globale Pakete:")[1].splitlines()[0]
    assert "@scope/tool" in out                                   # goes with Node, said so
    assert "andere Node-Prozesse laufen: www-data (1)" in out
    assert "Node bleibt" in out                                    # a node process of another user
    assert "Git proj: 1 geänderte Dateien" in out
    assert "crontab von" in out and "nennt openclaw in 1 Zeilen" in out
    assert "batch-issue-processor" in out and "[!!] der importierte Skill liegt noch da" in out
    for job in ("dh-bot-queue", "dh-digest", "repo-status", "snsf-grants"):
        assert f"Job {job}" in out and "fehlt" not in out.split(f"Job {job}")[1].splitlines()[0]
    assert "letzte Ausgabe dh-digest: 2026-11-08" in out or "letzte Ausgabe dh-digest: 20" in out
    # the inventory names fields, never values
    assert "channels.discord.token" in out and "models.providers.gpustack.apiKey" in out
    assert "GPUSTACK_API_KEY" in out and "GITHUB_TOKEN" in out
    assert "GPUSTACK_BASE_URL" not in out and "groupPolicy" not in out
    # the irreversible steps are printed for the human, never run
    assert "npm uninstall -g openclaw" in out and "rm -rf " + str(host.ws) in out
    assert "1519706253314756758" in out
    log = host.log.read_text()
    assert "uninstall" not in log and "claw cleanup" not in log


def test_node_may_go_only_when_nothing_else_needs_it(host):
    r = host(FAKE_PS_USERS="", FAKE_PS_PIDS="", FAKE_CRONTAB="")
    assert "Node wird nach OpenClaw von nichts gebraucht" in r.stdout
    assert "sudo apt purge nodejs" in r.stdout
    r = host(FAKE_PS_USERS="", FAKE_PS_PIDS="", FAKE_RDEPENDS="  some-tool\n")
    assert "installierte Pakete hängen an nodejs: some-tool" in r.stdout
    assert "apt purge" not in r.stdout


def test_a_failed_cron_job_is_flagged(host):
    r = host(FAKE_SNSF_STATUS="error: exit 23")
    assert "[!!] 1 Zeilen mit error/failed" in r.stdout


@pytest.mark.parametrize("env, reason", [
    ({"FAKE_ACTIVE": "active"}, "openclaw-gateway läuft"),
    ({"FAKE_ENABLED": "enabled"}, "startet beim nächsten Login wieder"),
    ({"DECOM_TODAY": "2026-11-12"}, "Beobachtungswoche läuft bis 2026-11-13"),
    ({"FAKE_PS_PIDS": "777 dh node node /opt/openclaw/dist/index.js gateway"}, "ein OpenClaw-Prozess läuft"),
])
def test_apply_stops_before_the_first_change(host, env, reason):
    before = _tree(host.tmp)
    r = host("--apply", **env)
    assert r.returncode == 3, r.combined
    assert reason in r.stdout and "Nichts geändert" in r.stdout
    assert _tree(host.tmp) == before


def test_apply_archives_renames_and_moves_the_old_skill_aside(host):
    r = host("--apply")
    assert r.returncode == 0, r.combined
    arch = host.dh / "archiv"
    ws_tar, oc_tar = arch / f"openclaw-workspace-{TODAY}.tar.gz", arch / f"dot-openclaw-{TODAY}.tar.gz"
    assert ws_tar.is_file() and oc_tar.is_file()
    assert stat.S_IMODE(oc_tar.stat().st_mode) == 0o600, "the .openclaw archive holds the old secrets"
    with tarfile.open(oc_tar) as t:
        assert ".openclaw/openclaw.json" in t.getnames()
    with tarfile.open(ws_tar) as t:
        assert "openclaw-workspace/proj/notes.md" in t.getnames()
    assert not host.oc.exists() and (host.dh / ".openclaw.pre-migration" / "openclaw.json").is_file()
    assert host.ws.is_dir(), "deleting the workspace is the human's step"
    assert not host.skill.exists()
    assert (host.hermes / "archiv" / f"openclaw-imports-{TODAY}" / "batch-issue-processor" / "SKILL.md").is_file()
    assert (host.hermes / ".hermes" / "skills" / "dh-bot-queue").is_dir(), "only the imported skill moves"
    assert "claw cleanup" not in host.log.read_text(), "nothing for it under /home/hermes"
    assert "Rückweg" in r.stdout and "2026-12-13" in r.stdout          # archive deletion date
    again = host("--apply")
    assert again.returncode == 0 and "gibt es schon — nicht überschrieben" in again.stdout


def test_apply_runs_hermes_claw_cleanup_only_for_leftovers_in_hermes_home(host):
    (host.hermes / ".openclaw").mkdir()
    r = host("--apply")
    assert r.returncode == 0, r.combined
    assert "hermes claw cleanup" in host.log.read_text()


def test_old_values_are_collected_and_counted_never_printed(host):
    values = host.tmp / "shm" / "alte-werte"
    values.parent.mkdir()
    r = host("--collect-old-values", str(values))
    assert r.returncode == 0, r.combined
    assert stat.S_IMODE(values.stat().st_mode) == 0o600
    held = set(values.read_text().split())
    assert {SECRETS["gpustack"], SECRETS["github"], SECRETS["discord"], SECRETS["mail"],
            SECRETS["pairing"]} <= held
    assert not {"https://gpustack.unibe.ch/v1", "open", "true"} & held, "only secret-named fields"
    assert host("--collect-old-values", str(values)).returncode == 2, "never overwrites"

    r = host("--verify-gone", str(values))
    assert r.returncode == 1 and "die Rotation ist nicht fertig" in r.stdout
    # rotated: new values in every live file
    env = host.dh / "agentic_historian" / ".env.gpustack"
    env.write_text("GPUSTACK_API_KEY=gpu-NEWVALUE-0000000001\nGITHUB_TOKEN=gh-NEWVALUE-000000002\n")
    host.system_env.write_text("GPUSTACK_API_KEY=gpu-NEWVALUE-0000000001\n")
    r = host("--verify-gone", str(values))
    assert r.returncode == 0, r.combined
    assert "kein alter Wert mehr in den Live-Dateien" in r.stdout
    assert ".openclaw/" in r.stdout                                    # the archive side is counted too


def test_verify_gone_ignores_short_lines_that_would_match_everything(host):
    values = host.tmp / "v"
    values.write_text("\n\nab\n")
    values.chmod(0o600)
    assert host("--verify-gone", str(values)).returncode == 2


def test_the_runbook_and_the_checklist_point_at_the_script_and_hold_no_values():
    readme = (REPO / "deploy" / "hermes" / "README.md").read_text(encoding="utf-8")
    deployment = (REPO / "docs" / "DEPLOYMENT_TEI.md").read_text(encoding="utf-8")
    rotation = (REPO / "deploy" / "hermes" / "rotation.md").read_text(encoding="utf-8")
    for text in (readme, deployment):
        assert "decommission_openclaw.sh" in text and "rotation.md" in text
    for name in ("GPUSTACK_API_KEY", "GITHUB_TOKEN", "DISCORD_BOT_TOKEN", "HF_TOKEN",
                 "--collect-old-values", "--verify-gone"):
        assert name in rotation, name
