"""
mcp_atr/_run.py — the small process that sits between the server and a batch.

Run as ``python _run.py <job dir>``. It reads ``meta.json``, runs the argv it
finds there with output appended to ``log``, and writes ``exit_code`` when the
child is done.

Why a wrapper at all: the MCP server starts jobs detached so that redeploying it
does not kill a run that is four hours in — which also means the server is not
around to reap them. Without something that outlives the server and outlives the
batch by one syscall, every finished job would read as *vanished*, and "finished
cleanly" would be indistinguishable from "was killed".

It is a standalone script, invoked by absolute path, so it needs no package
import and no ``sys.path`` arrangement of its own.

SIGTERM is forwarded rather than ignored: ``jobs.stop`` signals the whole process
group, and the batch runner writes every page through a temp file and renames, so
a forwarded TERM leaves finished pages, no half-written ones, and an exit code
that says what happened.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _run.py <job dir>", file=sys.stderr)
        return 2
    directory = Path(argv[1])
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    command = list(meta["argv"])
    cwd = meta.get("cwd") or str(Path.cwd())

    with (directory / "log").open("ab", buffering=0) as log:
        proc = subprocess.Popen(  # noqa: S603 — argv validated by mcp_atr.jobs
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        def forward(signum, _frame):
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                pass

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, forward)

        code = proc.wait()

    tmp = directory / "exit_code.part"
    tmp.write_text(f"{code}\n", encoding="utf-8")
    os.replace(tmp, directory / "exit_code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
