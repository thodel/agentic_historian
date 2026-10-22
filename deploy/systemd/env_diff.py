#!/usr/bin/env python3
"""What changes when the bot reads ONLY this env file? Names, never values (#567).

    sudo agentic_historian/.venv/bin/python deploy/systemd/env_diff.py /etc/agentic_historian.env

Step 1 of the switch (SEPARATION.md) is "copy the bot's keys into
/etc/agentic_historian.env" — by hand, from three dotenv files, and nothing
checked the copy. The first attempt copied only the keys the example listed and
would have started the bot with four settings silently back on their defaults:
ATR_MOUNT_DIR, ATR_PAGE_CACHE_ARCHIVE, LASSBERG_REGISTER, NEXTCLOUD_REMOTE_DIR
(tei, 22.10.2026). None of those fails loudly; each one points a job somewhere
else.

So this imports ``config`` twice in two subprocesses — once the way the bot reads
it today (the dotenv files in the checkout, ``AH_ENV_FILE`` unset) and once with
``AH_ENV_FILE`` pointing at the candidate file — and prints the names of the
module-level settings whose value differs. An empty list is the condition for
the switch. Values are never printed, because the point of the exercise is a
file that holds secrets.

Run it in the bot's venv, from the checkout, as a user that can read all four
files (root, once the candidate is ``0600 root``). Exit 0 when nothing differs,
1 when something does, 2 when either import failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "agentic_historian"

#: The dotenv files config.py reads when AH_ENV_FILE is unset, in its order.
#: Their keys are scrubbed from the process environment for both runs, so a key
#: exported in the operator's shell cannot mask a difference.
CHECKOUT_DOTENV = (REPO / ".env.gpustack", REPO / ".env", PACKAGE / ".env")

_SNAPSHOT = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pathlib import Path\n"
    "import config\n"
    "simple = (str, int, float, bool, type(None), Path)\n"
    "print(json.dumps({k: repr(v) for k, v in vars(config).items()\n"
    "                  if k.isupper() and isinstance(v, simple)}))\n"
)


def dotenv_keys(files: tuple[Path, ...] | None = None) -> set[str]:
    """Key names in the checkout's dotenv files — names only."""
    try:
        from dotenv import dotenv_values
    except ImportError:  # the system python; the venv has it
        return set()
    keys: set[str] = set()
    # Resolved at call time, not bound as a default: the module globals are what
    # a test (and a caller) points elsewhere.
    for path in CHECKOUT_DOTENV if files is None else files:
        if path.exists():
            keys.update(k for k in dotenv_values(path) if k)
    return keys


def snapshot(env_file: Path | None, *, scrub: set[str], python: str = sys.executable,
             package: Path | None = None) -> dict[str, str] | None:
    """``config``'s simple module-level settings, imported in a fresh process.

    *env_file* None means "as the bot reads it today"; a path means
    "with AH_ENV_FILE set to it". None back means the import failed.
    """
    package = PACKAGE if package is None else package
    env = {k: v for k, v in os.environ.items() if k not in scrub and k != "AH_ENV_FILE"}
    if env_file is not None:
        env["AH_ENV_FILE"] = str(env_file)
    done = subprocess.run([python, "-c", _SNAPSHOT, str(package)], env=env,
                          capture_output=True, text=True, cwd=str(package), check=False)
    if done.returncode != 0:
        sys.stderr.write(done.stderr[-800:])
        return None
    return json.loads(done.stdout.strip().splitlines()[-1])


def differing(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Names whose value differs between the two snapshots — the diff, sorted."""
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] in ("-h", "--help"):
        print(__doc__.strip().splitlines()[0])
        print(f"usage: {Path(sys.argv[0]).name} <candidate env file>")
        return 2
    candidate = Path(argv[0])
    if not candidate.is_file():
        print(f"not a readable file: {candidate}", file=sys.stderr)
        return 2
    scrub = dotenv_keys()
    before = snapshot(None, scrub=scrub)
    after = snapshot(candidate, scrub=scrub)
    if before is None or after is None:
        print("one of the two imports failed — see above; nothing compared.", file=sys.stderr)
        return 2
    changed = differing(before, after)
    if not changed:
        print(f"{candidate}: no setting changes when the bot reads only this file.")
        return 0
    print(f"{candidate}: {len(changed)} setting(s) would change — names only, never values:")
    for name in changed:
        print(f"  {name}")
    print("Add each to the file (from the checkout's dotenv files), or decide that the default is wanted.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
