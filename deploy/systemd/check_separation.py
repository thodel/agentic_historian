#!/usr/bin/env python3
"""Is the separation from the OpenClaw agent actually in force? (#567)

    sudo deploy/systemd/check_separation.py
    deploy/systemd/check_separation.py --openclaw-secrets /home/dh/.openclaw/credentials.json
    deploy/systemd/check_separation.py --json

#567's Definition of Done is a statement about a **running host**: "the bot
process cannot read the OpenClaw credentials, runs hardened under systemd, and
writes only into its data directory". Nothing checked it, and that is not a
small gap — #567 and #571 were both closed with the repo side marked ✅ while
**none** of it was in force on tei: no hardening drop-in installed, no
`EnvironmentFile`, the service still running as `dh` (measured 13./14.10.2026,
`docs/DEPLOYMENT_TEI.md`). Closing an issue describes the repository, not the
machine.

So this asks the machine. It reads systemd's **effective** properties — not the
drop-in file in this checkout, which is exactly the thing that can sit there
unused — and the owners and modes of the files the bot's secrets are in.

Three values per row, as in ``bot.security_posture``
---------------------------------------------------
``True`` holds, ``False`` does not, and ``None`` means **not measured here** —
never "probably fine". Two rows are routinely ``None`` and say why: where the
OpenClaw credentials live is host knowledge (name it with
``--openclaw-secrets``), and ``ProtectHome`` cannot apply while the checkout
itself is under ``/home``.

The exit code counts only the rows that were measured: a host where nothing
could be checked exits non-zero with "nothing measured", because an empty check
that passes is how this issue came to be closed the first time.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

#: The unit that runs the bot on tei. Named `agentic-historian`, not `dh-bot` —
#: `docs/DEPLOYMENT_TEI.md` records that mix-up costing a round of guessing.
UNIT = "agentic-historian"

#: The settings `deploy/mcp-atr/atr-mcp.service` already has and item 2 asks the
#: bot unit to match. Each value is what systemd reports when it is in force.
REQUIRED = {
    "ProtectSystem": "strict",
    "NoNewPrivileges": "yes",
    "PrivateTmp": "yes",
}

#: Properties read for the report but judged by their own rows below.
CONTEXT = ("User", "Group", "WorkingDirectory", "ReadWritePaths", "ProtectHome",
           "EnvironmentFiles")

Row = tuple[str, "bool | None", str]


def unit_properties(unit: str = UNIT, *, run=None) -> dict[str, str] | None:
    """systemd's effective properties for *unit*, or None when it cannot be asked.

    ``systemctl show`` rather than reading the drop-in: a file in
    ``/etc/systemd/system/<unit>.d/`` that was never followed by a
    ``daemon-reload`` is reported by neither, and the file in *this checkout* is
    not evidence about the host at all.

    None, not ``{}``: "systemd did not answer" and "it answered with nothing"
    are different findings, and the second one cannot happen.
    """
    run = run or _run
    if shutil.which("systemctl") is None:
        return None
    properties = [*REQUIRED, *CONTEXT]
    out = run(["systemctl", "show", unit, *(f"-p{name}" for name in properties)])
    if out is None:
        return None
    values: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values or None


def _run(cmd: list[str]) -> str | None:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _mode_of(path: Path, *, stat=os.stat):
    """``(mode, uid, gid)`` for *path*, or None when it cannot be read."""
    try:
        info = stat(path)
    except OSError:
        return None
    return info.st_mode & 0o777, info.st_uid, info.st_gid


def _user_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def posture(properties: dict[str, str] | None, *, secret_files=(),
            openclaw_secrets: Path | None = None, stat=os.stat) -> list[Row]:
    """The rows #567's three items come down to, measured where possible."""
    rows: list[Row] = []

    if properties is None:
        return [("systemd", None,
                 "nicht abfragbar (kein systemctl, oder die Unit ist unbekannt) — "
                 "von hier aus ist über den laufenden Dienst nichts zu sagen")]

    service_user = properties.get("User") or "root"

    # ── item 3: a user of its own ───────────────────────────────────────────
    rows.append((
        f"User= ({service_user})", service_user not in ("", "root", "dh"),
        "ein eigener Benutzer — die Secrets des OpenClaw-Agenten liegen damit "
        "ausserhalb seiner Reichweite" if service_user not in ("", "root", "dh")
        else f"läuft als {service_user!r}, demselben Benutzer wie der "
             "OpenClaw-Agent: beide lesen die Secrets des anderen (Punkt 3)"))

    # ── item 2: the hardening, as systemd reports it ────────────────────────
    for name, wanted in REQUIRED.items():
        actual = properties.get(name, "")
        rows.append((f"{name}={actual or '—'}", actual == wanted,
                     f"{wanted} ist in Kraft" if actual == wanted else
                     f"erwartet {wanted}; die Drop-in-Datei im Checkout ist kein "
                     f"Beleg, dass sie installiert und nachgeladen wurde"))

    writable = properties.get("ReadWritePaths", "")
    rows.append(("ReadWritePaths", bool(writable.strip()),
                 f"gesetzt: {writable}" if writable.strip() else
                 "leer — mit ProtectSystem=strict schreibt der Dienst nirgends, "
                 "ohne es schreibt er überall, wo sein Benutzer darf"))

    # ProtectHome cannot hold while the code it runs lives under /home.
    home_bound = (properties.get("WorkingDirectory") or "").startswith("/home")
    protect_home = properties.get("ProtectHome", "")
    rows.append((f"ProtectHome={protect_home or '—'}",
                 None if home_bound else protect_home in ("yes", "read-only"),
                 "nicht anwendbar: das Checkout selbst liegt unter /home, "
                 "ProtectHome=yes würde den Dienst am eigenen Code blind machen"
                 if home_bound else
                 "gesetzt" if protect_home in ("yes", "read-only") else
                 "nicht gesetzt, und das Checkout liegt nicht unter /home — hier "
                 "wäre es zu haben"))

    # ── item 1: the secrets, and who else can read them ─────────────────────
    for path in secret_files:
        path = Path(path)
        measured = _mode_of(path, stat=stat)
        if measured is None:
            rows.append((f"{path}", None, "nicht lesbar von hier — als root "
                                          "ausführen, um die Rechte zu sehen"))
            continue
        mode, uid, _gid = measured
        rows.append((f"{path} ({oct(mode)}, {_user_name(uid)})", not mode & 0o077,
                     "nur der Eigentümer liest sie" if not mode & 0o077 else
                     f"Gruppe/Welt dürfen lesen ({oct(mode)}) — jeder Prozess "
                     f"dieses Hosts kommt an die Bot-Secrets"))

    # ── the Definition of Done itself ───────────────────────────────────────
    if openclaw_secrets is None:
        rows.append(("OpenClaw-Credentials erreichbar?", None,
                     "nicht geprüft: wo sie liegen, ist Host-Wissen — mit "
                     "--openclaw-secrets <pfad> wird daraus eine Messung"))
    else:
        measured = _mode_of(Path(openclaw_secrets), stat=stat)
        if measured is None:
            rows.append((f"OpenClaw-Credentials ({openclaw_secrets})", None,
                         "Pfad nicht lesbar von hier — existiert er?"))
        else:
            mode, uid, _gid = measured
            owner = _user_name(uid)
            reachable = bool(mode & 0o077) or owner == service_user
            rows.append((f"OpenClaw-Credentials ({oct(mode)}, {owner})", not reachable,
                         "für den Bot-Benutzer unerreichbar — das ist die "
                         "Definition of Done" if not reachable else
                         f"der Bot läuft als {service_user!r} und die Datei gehört "
                         f"{owner!r} mit {oct(mode)}: er kann sie lesen"))
    return rows


def format_rows(rows: list[Row]) -> list[str]:
    mark = {True: "ok   ", False: "FEHLT", None: "?    "}
    return [f"[{mark[ok]}] {label}: {why}" for label, ok, why in rows]


def verdict(rows: list[Row]) -> tuple[int, int, int]:
    """``(held, failed, unmeasured)`` — the three counts, never two."""
    held = sum(1 for _l, ok, _w in rows if ok is True)
    failed = sum(1 for _l, ok, _w in rows if ok is False)
    return held, failed, len(rows) - held - failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unit", default=UNIT)
    parser.add_argument("--secrets", type=Path, action="append", default=None,
                        help="an env file the bot reads; repeatable")
    parser.add_argument("--openclaw-secrets", type=Path, default=None,
                        help="where the OpenClaw agent keeps its credentials")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    secrets = args.secrets if args.secrets is not None else _default_secret_files()
    rows = posture(unit_properties(args.unit), secret_files=secrets,
                   openclaw_secrets=args.openclaw_secrets)
    held, failed, unmeasured = verdict(rows)

    if args.json:
        print(json.dumps({"unit": args.unit,
                          "rows": [{"label": label, "ok": ok, "why": why}
                                   for label, ok, why in rows],
                          "held": held, "failed": failed, "unmeasured": unmeasured},
                         indent=2, ensure_ascii=False))
    else:
        print(f"Trennung vom OpenClaw-Agenten, Unit {args.unit} (#567)\n")
        for line in format_rows(rows):
            print("  " + line)
        print(f"\n{held} erfüllt, {failed} nicht erfüllt, {unmeasured} nicht gemessen.")
        if unmeasured:
            print("Nicht gemessen heisst nicht erfüllt — es heisst, dass niemand "
                  "nachgesehen hat.")

    if held == 0:
        print("\nNichts konnte geprüft werden. Das ist kein Bestehen: genau so "
              "wurde dieses Issue das erste Mal geschlossen.", file=sys.stderr)
        return 2
    return 1 if failed else 0


def _default_secret_files() -> list[Path]:
    """The files the bot reads its secrets from, as `config.ENV_SOURCE` resolves.

    Not a guessed list: `docs/DEPLOYMENT_TEI.md` records that the authoritative
    answer to "where does this key come from" is `config.ENV_SOURCE`, and that a
    `grep` over the candidates gave the wrong one twice.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agentic_historian"))
        import config  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — a check must not fail on its own import
        return []
    sources = getattr(config, "ENV_SOURCE", None) or {}
    return sorted({Path(path) for path in sources.values() if path})


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
