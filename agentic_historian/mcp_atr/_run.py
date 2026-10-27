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

It also writes ``memcg.json``: the ``oom_kill`` counter of its memory cgroup
before the child starts and after it ends (#736). A job that died of
``exited -9`` said nothing more on 25.10.; the cause — the service's
``MemoryMax=200M`` — was only in the kernel log, which takes sudo. The counter
is in ``memory.events`` and readable without it, and ``jobs.status`` compares
the two.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

PROC_CGROUP = Path("/proc/self/cgroup")
CGROUP_ROOT = Path("/sys/fs/cgroup")


def memcg() -> dict:
    """This process's cgroup, its ``memory.max`` and two counters of
    ``memory.events``: ``oom`` (the cgroup hit its own limit) and ``oom_kill``
    (a process in it was OOM-killed, by the cgroup or by the host). None where
    unread — no package import, see the module docstring."""
    out = {"cgroup": None, "memory_max": None, "oom": None, "oom_kill": None}
    try:
        line = next(ln for ln in PROC_CGROUP.read_text().splitlines()
                    if ln.startswith("0::"))
    except (OSError, StopIteration):
        return out
    out["cgroup"] = line.split("::", 1)[1].strip()
    base = CGROUP_ROOT / out["cgroup"].lstrip("/")
    try:
        raw = (base / "memory.max").read_text().strip()
        out["memory_max"] = raw if raw == "max" else int(raw)
    except (OSError, ValueError):
        pass
    try:
        for ln in (base / "memory.events").read_text().splitlines():
            key, _, value = ln.partition(" ")
            if key in ("oom", "oom_kill"):
                out[key] = int(value)
    except (OSError, ValueError):
        pass
    return out


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: _run.py <job dir>", file=sys.stderr)
        return 2
    directory = Path(argv[1])
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    command = list(meta["argv"])
    cwd = meta.get("cwd") or str(Path.cwd())

    with (directory / "log").open("ab", buffering=0) as log:
        # Unbuffered: stdout into a file is block-buffered and stderr is not, so
        # an error printed at the end landed at the *top* of the log, above the
        # output it ended (25.10.: a Transkribus refusal read as a silent stop).
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        # Temporary files in the job's own directory, not /tmp. The server runs
        # with PrivateTmp=yes, and a restart throws its /tmp away — under every
        # job still running, which KillMode=process otherwise lets survive
        # (25.10.: two dataset uploads died with "No such file or directory:
        # '/tmp/hf_datasets-…'" while another session redeployed tei).
        scratch = directory / "tmp"
        scratch.mkdir(exist_ok=True)
        env.update(TMPDIR=str(scratch), TEMP=str(scratch), TMP=str(scratch))
        # Where a job keeps what it produced — upload-hf-card's spliced card
        # (#738), instead of the production checkout's docs/.
        env["AH_JOB_DIR"] = str(directory)
        # Its own file, not meta.json: the server rewrites meta.json with the
        # pid right after starting this process.
        record = {"start": memcg()}
        _write_json(directory / "memcg.json", record)
        proc = subprocess.Popen(  # noqa: S603 — argv validated by mcp_atr.jobs
            command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, env=env,
        )

        def forward(signum, _frame):
            try:
                proc.send_signal(signum)
            except ProcessLookupError:
                pass

        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, forward)

        code = proc.wait()
        record["end"] = memcg()
        _write_json(directory / "memcg.json", record)

    # Scratch is the job's, and can be gigabytes (a parquet conversion); the
    # log and the exit code are what stays.
    shutil.rmtree(scratch, ignore_errors=True)

    tmp = directory / "exit_code.part"
    tmp.write_text(f"{code}\n", encoding="utf-8")
    os.replace(tmp, directory / "exit_code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
