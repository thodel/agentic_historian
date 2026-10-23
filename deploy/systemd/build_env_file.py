#!/usr/bin/env python3
"""Build the bot's own env file from the checkout's dotenv files (#567, step 1).

    sudo agentic_historian/.venv/bin/python deploy/systemd/build_env_file.py
    sudo agentic_historian/.venv/bin/python deploy/systemd/build_env_file.py --target /etc/agentic_historian.env

Writes ONLY the keys that ``agentic_historian.env.example`` lists, each with the
value ``config.py`` resolves today: first file wins (``.env.gpustack``, ``.env``,
``agentic_historian/.env``), and within a file python-dotenv keeps the last
assignment — so a key that is assigned three times in ``.env.gpustack`` arrives
once, with the value that is in force. Prints key names, never values.

This is what ran on tei on 23.10.2026, in place of the ``sudoedit`` the runbook
first prescribed: 26 keys out of three files, typed by hand, is where a copy
goes wrong silently — and ``env_diff.py`` then measures the result either way.
The target must not exist (an operator who wants it rebuilt removes it first),
and a value with a single quote or a newline stops the run rather than being
written half-quoted.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_TARGET = Path("/etc/agentic_historian.env")


def wanted_keys(example: Path) -> list[str]:
    return re.findall(r"^([A-Z0-9_]+)=", example.read_text(encoding="utf-8"), re.M)


def resolve(sources: tuple[Path, ...], wanted: list[str]) -> dict[str, str]:
    """``{key: value}`` for the wanted keys, first file wins, last line within a file."""
    from dotenv import dotenv_values
    resolved: dict[str, str] = {}
    for src in sources:
        if not src.exists():
            continue
        for key, value in dotenv_values(src).items():
            if key in wanted and value is not None:
                resolved.setdefault(key, value)
    return resolved


def render(wanted: list[str], resolved: dict[str, str]) -> str:
    lines = ["# built by deploy/systemd/build_env_file.py from the checkout's dotenv files (#567)\n"]
    for key in wanted:
        if key not in resolved:
            continue
        value = resolved[key]
        if "'" in value or "\n" in value:
            raise ValueError(f"{key}: value contains a quote or newline — write that one by hand")
        lines.append(f"{key}='{value}'\n")  # single quotes: literal for dotenv and for systemd
    return "".join(lines)


def build(repo: Path, target: Path, *, owner_uid: int | None = 0) -> tuple[list[str], list[str]]:
    """Write *target*; return ``(written, left_out)`` key names."""
    example = repo / "deploy/systemd/agentic_historian.env.example"
    sources = (repo / ".env.gpustack", repo / ".env", repo / "agentic_historian/.env")
    if target.exists():
        raise FileExistsError(f"{target} exists — remove it first if it should be rebuilt")
    wanted = wanted_keys(example)
    resolved = resolve(sources, wanted)
    body = render(wanted, resolved)
    old_umask = os.umask(0o077)
    try:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        if owner_uid is not None and os.geteuid() == 0:
            os.chown(tmp, owner_uid, owner_uid)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        os.umask(old_umask)
    written = [k for k in wanted if k in resolved]
    return written, [k for k in wanted if k not in resolved]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--repo", type=Path, default=REPO)
    args = parser.parse_args(argv)
    if args.target == DEFAULT_TARGET and os.geteuid() != 0:
        print("run with sudo: the target is root-owned 0600", file=sys.stderr)
        return 2
    try:
        written, left_out = build(args.repo, args.target)
    except (FileExistsError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"{args.target}: {len(written)} keys written (0600)")
    print("  written:", " ".join(written))
    print("  in the example but set nowhere (defaults apply):", " ".join(left_out) or "—")
    print("next: switch_to_own_user.sh sets root:agentic_historian 0640; env_diff.py must say 'no setting changes'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
