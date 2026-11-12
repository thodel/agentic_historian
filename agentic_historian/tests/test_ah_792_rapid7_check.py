"""deploy/tei/check_rapid7.sh — is tei ready for the Rapid7 rescan? (#792)

The epic's definition of done is met except for the rescan itself, which the
Informatikdienste run. Before asking them, one command should say whether the
scan would find anything we already removed: Java/Log4j/Spring (#793/#794),
the Ubuntu packages (#795), world-writable files and home modes (#796), failed
units, services that stopped answering, a pending reboot, and what H6 (#802)
still has to take off the host.

The script runs here against stubbed system commands and a fake file tree:
a healthy tei passes, every regression it guards against fails, a command that
does not answer is a WARN (never a silent OK), and it prints paths and
versions, never file contents.

Offline. Run from the repo root:
    pytest agentic_historian/tests/test_ah_792_rapid7_check.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "tei" / "check_rapid7.sh"

#: Real tools the script uses; everything else on PATH is a stub.
REAL = ("bash", "find", "head", "tr", "grep", "sort", "awk", "stat", "ls")

STUBS = {
    "id": 'echo "${STUB_UID:-0}"',
    "systemctl": r'''
case "$1" in
  --failed) [ -n "${STUB_SYSTEMCTL_DOWN:-}" ] && exit 1; printf '%s\n' ${STUB_FAILED_UNITS:-} ;;
  show) echo loaded ;;
  is-active) case " ${STUB_INACTIVE:-} " in *" $2 "*) echo inactive; exit 3 ;; esac; echo active ;;
esac''',
    "docker": r'''[ -n "${STUB_DOCKER_DOWN:-}" ] && exit 1
printf '%s\n' ${STUB_IMAGES:-ch-h-bot-app:mcp ch-h-bot-api:mcp ch-h-bot-bridge:mcp swisslib-mcp:latest adfreiburg/qlever:latest}''',
    "nginx": "exit 0",
    "curl": r'''for a in "$@"; do url="$a"; done
port=${url##*:}; port=${port%%/*}
case " ${STUB_DOWN_PORTS:-} " in *" $port "*) printf 000; exit 7 ;; esac
printf 200''',
    "pro": r'''printf 'SERVICE ENTITLED STATUS DESCRIPTION\n'
printf 'esm-apps   yes  %s  Expanded Security Maintenance for Applications\n' "${STUB_ESM_APPS:-enabled}"
printf 'esm-infra  yes  enabled  Expanded Security Maintenance for Infrastructure\n' ''',
    "dpkg-query": r'''pkg="${!#}"
case " ${STUB_INSTALLED:-gh python3-urllib3 python3-pip-whl} " in *" $pkg "*) printf 'install ok installed' ;; *) exit 1 ;; esac''',
    "apt-cache": r'''pkg="$2"
case "$pkg" in
  gh) printf 'gh:\n  Installed: 2.83.0\n  Candidate: 2.83.0\n  Version table:\n *** 2.83.0 500\n        500 %s stable/main amd64 Packages\n' "${STUB_GH_SOURCE:-https://cli.github.com/packages}" ;;
  python3-urllib3) printf 'python3-urllib3:\n  Installed: %s\n  Candidate: %s\n' "${STUB_URLLIB3:-2.0.7-1ubuntu0.8}" "${STUB_URLLIB3_CAND:-${STUB_URLLIB3:-2.0.7-1ubuntu0.8}}" ;;
  python3-pip-whl) printf 'python3-pip-whl:\n  Installed: 24.0+dfsg-1ubuntu1.3+esm3\n  Candidate: 24.0+dfsg-1ubuntu1.3+esm3\n' ;;
esac''',
    "apt": r'''echo "Listing..."; printf '%s\n' ${STUB_UPGRADABLE:-}''',
    "getent": r'''printf 'root:x:0:0:root:/root:/bin/bash\n'
printf 'dh:x:1000:1000::/home/dh:/bin/bash\n'
printf 'hermes:x:1001:1001::/home/hermes:/bin/bash\n'
printf 'agentic_historian:x:998:998::/home/agentic_historian:/usr/sbin/nologin\n'
printf 'atrsvc:x:1002:1002::/home/atrsvc:/usr/sbin/nologin\n'
printf 'nobody:x:65534:65534::/nonexistent:/usr/sbin/nologin\n' ''',
}


@pytest.fixture
def tei(tmp_path):
    """A fake host: a file tree and a PATH of stubs plus the real tools."""
    root = tmp_path / "root"
    for d, mode in (("root", 0o700), ("home/dh", 0o750), ("home/hermes", 0o750),
                    ("home/atrsvc", 0o750), ("home/agentic_historian", 0o750)):
        (root / d).mkdir(parents=True)
        (root / d).chmod(mode)
    (root / "etc/apt/sources.list.d").mkdir(parents=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for tool in REAL:
        os.symlink(shutil.which(tool), bin_ / tool)
    for name, body in STUBS.items():
        p = bin_ / name
        p.write_text("#!/bin/bash\n" + body + "\n")
        p.chmod(0o755)

    def run(**env):
        e = {"PATH": str(bin_), "CHECK_ROOT": str(root), "LC_ALL": "C.UTF-8",
             **{k: str(v) for k, v in env.items()}}
        return subprocess.run([str(bin_ / "bash"), str(SCRIPT)], env=e, check=False,
                              capture_output=True, text=True, timeout=60)

    run.root = root
    return run


def _lines(out, level):
    return [ln for ln in out.splitlines() if ln.startswith(level)]


def test_the_script_is_valid_bash_and_executable():
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_tei_as_it_stands_passes_with_the_known_warnings(tei):
    r = tei()
    assert r.returncode == 0, r.stdout
    assert _lines(r.stdout, "FAIL") == []
    assert "OK    java: no log4j-core, openjdk or spring jar" in r.stdout
    assert "OK    pro esm-apps: enabled" in r.stdout
    assert "OK    package gh: from cli.github.com" in r.stdout
    assert "OK    package python3-pip-whl: 24.0+dfsg-1ubuntu1.3+esm3 (esm)" in r.stdout
    # urllib3 waits for Canonical: known, accepted, said so
    assert ("WARN  package python3-urllib3: 2.0.7-1ubuntu0.8, no esm fix published "
            "yet") in r.stdout
    assert "OK    home dh: /home/dh is 750" in r.stdout
    assert "OK    home atrsvc: /home/atrsvc is 750" in r.stdout    # nologin, still checked
    assert "home nobody" not in r.stdout
    # a system UID with a home under /home is checked: that was the #796 account
    assert "OK    home agentic_historian: /home/agentic_historian is 750" in r.stdout
    assert r.stdout.rstrip().endswith("ready for the rescan (WARNs are known; see "
                                      "docs/SECURITY_TEI.md).")


@pytest.mark.parametrize("setup,env,expected", [
    (lambda root: (root / "opt/x").mkdir(parents=True)
     or (root / "opt/x/log4j-core-2.19.0.jar").write_text("jar"),
     {}, "FAIL  java: found: "),
    (lambda root: (root / "root/teipublisher_container").mkdir(), {},
     "FAIL  java: /root/teipublisher_container is back"),
    (None, {"STUB_IMAGES": "existdb/teipublisher:latest adfreiburg/qlever:latest"},
     "FAIL  docker: Java image(s) back: existdb/teipublisher:latest"),
    (None, {"STUB_FAILED_UNITS": "mnt-gwdg.mount"},
     "FAIL  systemd: failed units: mnt-gwdg.mount"),
    (None, {"STUB_INACTIVE": "atr-mcp"}, "FAIL  unit atr-mcp: inactive"),
    (None, {"STUB_DOWN_PORTS": "8001"}, "FAIL  port 8001: kf does not answer"),
    (None, {"STUB_ESM_APPS": "disabled"}, "FAIL  pro esm-apps: not enabled"),
    (None, {"STUB_INSTALLED": "gh python3-urllib3 python3-pip-whl python3-pip"},
     "FAIL  package python3-pip: installed again"),
    (None, {"STUB_GH_SOURCE": "http://archive.ubuntu.com/ubuntu noble/universe"},
     "FAIL  package gh: installed from the Ubuntu archive"),
    (None, {"STUB_URLLIB3_CAND": "2.0.7-1ubuntu0.8+esm1"},
     ("FAIL  package python3-urllib3: 2.0.7-1ubuntu0.8 installed, 2.0.7-1ubuntu0.8+esm1 "
      "available — sudo apt upgrade python3-urllib3")),
    (lambda root: (root / "home/hermes").chmod(0o755), {},
     "FAIL  home hermes: /home/hermes is 755"),
    # a service account with nologin is checked too: the #796 finding was a
    # missing home
    (lambda root: (root / "home/atrsvc").rmdir(), {},
     "FAIL  home atrsvc: /home/atrsvc does not exist — create it 0750"),
    (lambda root: (root / "home/agentic_historian").rmdir(), {},
     "FAIL  home agentic_historian: /home/agentic_historian does not exist"),
])
def test_each_regression_the_rescan_would_find_fails(tei, setup, env, expected):
    if setup:
        setup(tei.root)
    r = tei(**env)
    assert r.returncode == 1
    assert any(ln.startswith(expected) for ln in r.stdout.splitlines()), r.stdout
    assert "not ready for the rescan" in r.stdout


def test_a_removed_package_back_does_not_also_say_none_is_back(tei):
    r = tei(STUB_INSTALLED="gh python3-urllib3 python3-pip-whl rclone")
    assert "FAIL  package rclone" in r.stdout
    assert "none of the seven removed packages is back" not in r.stdout


@pytest.mark.parametrize("env,expected", [
    ({"STUB_UPGRADABLE": "libfoo/noble-updates"},
     "WARN  apt: 1 package(s) upgradable"),
    ({"STUB_UID": "1000"}, "WARN  privileges: not root"),
])
def test_what_is_known_or_waiting_is_a_warning(tei, env, expected):
    r = tei(**env)
    assert r.returncode == 0
    assert any(ln.startswith(expected) for ln in r.stdout.splitlines()), r.stdout


def test_without_root_docker_is_said_to_be_unchecked(tei):
    r = tei(STUB_UID=1000)
    assert "WARN  docker: not checked (needs root and docker)" in r.stdout


@pytest.mark.parametrize("env,expected", [
    ({"STUB_DOCKER_DOWN": 1}, "WARN  docker: docker does not answer"),
    ({"STUB_SYSTEMCTL_DOWN": 1}, "WARN  systemd: systemctl does not answer"),
])
def test_a_command_that_does_not_answer_is_never_an_ok(tei, env, expected):
    r = tei(**env)
    assert expected in r.stdout
    key = expected.split()[1]
    assert not any(ln.startswith(f"OK    {key}") for ln in r.stdout.splitlines())


def test_a_pending_reboot_and_node_are_reported(tei):
    (tei.root / "var/run").mkdir(parents=True)
    (tei.root / "var/run/reboot-required").write_text("*** System restart required ***\n")
    (tei.root / "etc/apt/sources.list.d/nodesource.list").write_text("deb x")
    r = tei()
    assert "WARN  reboot: required" in r.stdout
    assert "WARN  openclaw/node: still on the host (nodesource-repo)" in r.stdout


def test_world_writable_files_are_named_but_never_read(tei):
    f = tei.root / "home/dh/.venv/.lock"
    f.parent.mkdir(parents=True)
    f.write_text("TOKEN=do-not-print-me\n")
    f.chmod(0o666)
    r = tei()
    assert "WARN  world-writable: 1 file(s), e.g. " in r.stdout
    assert ".venv/.lock" in r.stdout
    assert "do-not-print-me" not in r.stdout + r.stderr
