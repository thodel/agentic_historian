"""
mcp_atr/jobs.py — start a batch from a request, and answer "how far is it".

An MCP call has to return in seconds; an ATR run over a few hundred pages takes
hours. So a tool cannot *be* the run — it can only start one and hand back a
handle. Everything here exists for that gap:

* **Detached children.** A job is started with ``start_new_session=True`` and
  keeps running when the MCP server is restarted or redeployed. The server is a
  door, not a supervisor.
* **State on disk, not in memory.** A restarted server must still be able to
  answer about jobs it did not start. Everything needed lives in the job
  directory, and progress is read from the run's own output, which
  ``atr_batch`` already treats as its state.
* **Fixed argv.** No shell anywhere on this path. A tool's arguments are
  validated against a charset and a root directory and then placed into a list
  that ``subprocess`` hands to ``execve``. A remote caller can choose *which*
  corpus and *which* models; it can never choose a command.

The last point is the reason this module is separate from ``server.py`` and has
its own tests. The server is exposed to the public internet behind a bearer
token; the validation below is what stands between that token and the box.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

import config

__all__ = [
    "JobError",
    "Job",
    "MODEL_RE",
    "RUN_RE",
    "jobs_root",
    "validate_run",
    "validate_models",
    "resolve_source",
    "batch_argv",
    "pull_argv",
    "start",
    "status",
    "read_log",
    "list_jobs",
    "run_progress",
    "start_and_peek",
    "stop",
]


class JobError(ValueError):
    """A request that must not be turned into a process."""


#: A run name becomes a directory under ``VLM_TEST_ROOT``. Anchored, bounded,
#: and without ``/`` or ``.`` sequences, so it cannot climb out of that root.
RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

#: Gateway model ids: ``qwen3vl-german-xix-v1``, ``kraken-fondue_gd_v2``,
#: ``qwen3.5-4b-german-xix-v1``. Colons are allowed because engine-qualified ids
#: exist; nothing else is.
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")

#: How many models one request may ask for. Each is a full pass over the corpus;
#: a list of forty is a mistake or an attack, never an intention.
MAX_MODELS = 8


def jobs_root() -> Path:
    """Where job directories live. One per job, named by job id."""
    root = config.DATA_DIR / "mcp_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ── validation ───────────────────────────────────────────────────────────────

def validate_run(run: str) -> str:
    run = (run or "").strip()
    if not RUN_RE.match(run):
        raise JobError(
            f"invalid run name {run!r} — letters, digits, dot, dash and underscore "
            "only, starting with a letter or digit, at most 64 characters"
        )
    return run


def validate_models(models: Sequence[str] | str) -> list[str]:
    if isinstance(models, str):
        models = [m for m in models.split(",")]
    cleaned = [m.strip() for m in models if m and m.strip()]
    if not cleaned:
        raise JobError("no models given")
    if len(cleaned) > MAX_MODELS:
        raise JobError(f"{len(cleaned)} models requested; at most {MAX_MODELS}")
    for m in cleaned:
        if not MODEL_RE.match(m):
            raise JobError(f"invalid model id {m!r}")
    return cleaned


def _roots() -> list[Path]:
    """Directories a batch may read pages from.

    The staging area the share is mirrored into, and the comparison root — the
    second because re-reading a previous run's source is legitimate. Nothing
    else on the machine is a corpus.
    """
    return [Path(config.NEXTCLOUD_STAGING_DIR).resolve(),
            Path(config.VLM_TEST_ROOT).resolve()]


def resolve_source(source: str) -> Path:
    """Turn a requested source directory into an absolute path, or refuse.

    ``resolve()`` first, then containment: resolving follows symlinks, so a link
    inside the staging area that points at ``/etc`` is rejected by the same check
    that rejects ``../../etc`` — which is why the check is on the resolved path
    and never on the string.
    """
    raw = (source or "").strip()
    if not raw:
        raise JobError("no source directory given")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = Path(config.NEXTCLOUD_STAGING_DIR) / candidate
    resolved = candidate.resolve()
    roots = _roots()
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        raise JobError(
            f"source {raw!r} is outside the corpus roots "
            f"({', '.join(str(r) for r in roots)})"
        )
    if not resolved.is_dir():
        raise JobError(f"source is not a directory: {resolved}")
    return resolved


# ── argv construction ────────────────────────────────────────────────────────

def _python() -> str:
    return sys.executable or "python3"


#: The wrapper that actually holds the job open. See its own docstring.
RUNNER = Path(__file__).resolve().parent / "_run.py"


def batch_argv(source: Path, models: Sequence[str], run: str, *,
               limit: Optional[int] = None, sample: Optional[int] = None,
               concurrency: Optional[int] = None,
               retries: Optional[int] = None, dry_run: bool = False) -> list[str]:
    """The exact argv for one ``atr-batch`` run.

    Deliberately the documented CLI rather than an in-process call: the MCP path
    and the terminal path then cannot drift, and a job that misbehaves can be
    reproduced by a person pasting the same line.
    """
    argv = [_python(), "-m", "agentic_historian", "atr-batch",
            "--source", str(source),
            "--models", ",".join(models),
            "--run", run]
    if limit is not None:
        argv += ["--limit", str(int(limit))]
    if sample is not None:
        argv += ["--sample", str(int(sample))]
    if concurrency is not None:
        argv += ["--concurrency", str(int(concurrency))]
    if retries is not None:
        argv += ["--retries", str(int(retries))]
    if dry_run:
        argv.append("--dry-run")
    return argv


def pull_argv(folder: Optional[str] = None, *, limit: Optional[int] = None,
              list_only: bool = False) -> list[str]:
    argv = [_python(), "-m", "agentic_historian", "pull-share"]
    if folder is not None:
        if "/" in folder and folder.startswith("/"):
            raise JobError("folder is a path inside the share, not an absolute path")
        argv += ["--folder", folder]
    if limit is not None:
        argv += ["--limit", str(int(limit))]
    if list_only:
        argv.append("--list")
    return argv


# ── the job itself ───────────────────────────────────────────────────────────

@dataclass
class Job:
    job_id: str
    kind: str
    argv: list[str]
    started_at: str
    pid: Optional[int] = None
    run: Optional[str] = None
    state: str = "running"
    exit_code: Optional[int] = None
    finished_at: Optional[str] = None
    progress: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "job_id": self.job_id, "kind": self.kind, "state": self.state,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "exit_code": self.exit_code, "pid": self.pid, "run": self.run,
            "argv": self.argv, "progress": self.progress,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_dir(job_id: str) -> Path:
    if not re.match(r"^[0-9A-Za-z_.-]{1,64}$", job_id or ""):
        raise JobError(f"invalid job id {job_id!r}")
    return jobs_root() / job_id


def start(kind: str, argv: Sequence[str], *, run: Optional[str] = None,
          cwd: Optional[Path] = None) -> Job:
    """Launch ``argv`` detached, and return the handle immediately.

    ``start_new_session=True`` puts the child in its own process group, so it
    survives the server being restarted — which will happen, because deploying a
    new version of the server must not kill a run that is four hours in.
    """
    job_id = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    directory = jobs_root() / job_id
    directory.mkdir(parents=True)
    meta = {"job_id": job_id, "kind": kind, "argv": list(argv),
            "started_at": _now(), "run": run,
            "cwd": str(cwd or Path(config.BASE_DIR).parent)}
    meta_path = directory / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    # The child is _run.py, not the batch itself: it outlives the server (which
    # will be restarted) and outlives the batch by one write, which is what makes
    # "finished cleanly" distinguishable from "was killed".
    proc = subprocess.Popen(  # noqa: S603 — a fixed script plus one directory path
        [_python(), str(RUNNER), str(directory)],
        cwd=meta["cwd"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    meta["pid"] = proc.pid
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return Job(job_id=job_id, kind=kind, argv=list(argv), started_at=meta["started_at"],
               pid=proc.pid, run=run, state="running")


def _alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # someone else's pid — alive, not ours
        return True
    return True


def run_progress(run: Optional[str]) -> dict:
    """Pages written so far, per model, read from the run's own output.

    ``atr_batch`` writes one ``.json`` per page per model and treats what is on
    disk as its state; counting those files is therefore not an approximation of
    progress, it is the same number the runner itself would compute on resume.
    """
    if not run:
        return {}
    run_dir = Path(config.VLM_TEST_ROOT) / run
    if not run_dir.is_dir():
        return {}
    per_model = {}
    for child in sorted(run_dir.iterdir()):
        if child.is_dir():
            per_model[child.name] = sum(1 for _ in child.glob("*.json"))
    out: dict = {"run_dir": str(run_dir), "pages_written": per_model}
    report = run_dir / "report.md"
    if report.is_file():
        out["report"] = str(report)
    return out


def status(job_id: str) -> Job:
    directory = _job_dir(job_id)
    meta_path = directory / "meta.json"
    if not meta_path.is_file():
        raise JobError(f"no such job: {job_id}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    job = Job(job_id=meta["job_id"], kind=meta.get("kind", "?"),
              argv=meta.get("argv", []), started_at=meta.get("started_at", "?"),
              pid=meta.get("pid"), run=meta.get("run"))

    recorded = directory / "exit_code"
    if recorded.is_file():
        job.exit_code = int(recorded.read_text().strip() or -1)
        job.state = "done" if job.exit_code == 0 else "failed"
        job.finished_at = datetime.fromtimestamp(
            recorded.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    elif _alive(job.pid):
        job.state = "running"
    else:
        # The child is gone and left no exit code. Nothing distinguishes a
        # SIGKILL from a reboot here, and calling it "done" would be a lie that
        # costs somebody a re-run they did not know they needed.
        job.state = "vanished"
    job.progress = run_progress(job.run)
    return job


def list_jobs(limit: int = 20) -> list[Job]:
    root = jobs_root()
    ids = sorted((d.name for d in root.iterdir() if (d / "meta.json").is_file()),
                 reverse=True)[:max(1, limit)]
    return [status(i) for i in ids]


#: How long a tool may block before the client gives up on it. The MCP broker cut
#: a `share_list` call at exactly 60 s on 2026-09-15 while the listing was still
#: running on tei — the worst of both answers: the caller sees a failure and the
#: work continues unseen. Everything synchronous has to finish well inside this.
BROKER_TIMEOUT_S = 60

#: How long :func:`start_and_peek` waits before handing back a handle instead of
#: an answer. Comfortably inside the broker's patience, and long enough that
#: anything quick — a small share, a dry run — still answers in one call.
PEEK_S = 25


def start_and_peek(kind: str, argv: Sequence[str], *, run: Optional[str] = None,
                   grace_s: float = PEEK_S, poll_s: float = 0.5,
                   lines: int = 300) -> dict:
    """Start a job, wait a little, and report whatever is true by then.

    The shape that fits a protocol with a request timeout and work that does not
    respect one. A listing of forty files answers inline and the caller never
    learns there was a job; a listing of four thousand comes back as a handle
    with the work still running, which is an answer rather than a timeout.

    Deliberately not a longer synchronous call with a bigger timeout: the ceiling
    belongs to the client, not to us, so raising ours only moves the failure to a
    place where the job is also lost.
    """
    job = start(kind, argv, run=run)
    deadline = time.monotonic() + max(0.0, grace_s)
    while time.monotonic() < deadline:
        current = status(job.job_id)
        if current.state != "running":
            return {"done": True, **current.as_dict(),
                    "output": read_log(job.job_id, lines)}
        time.sleep(poll_s)
    return {
        "done": False,
        **status(job.job_id).as_dict(),
        "note": (f"still running after {grace_s:.0f}s — poll job_status and read "
                 f"job_log with this job_id"),
    }


def read_log(job_id: str, lines: int = 50) -> str:
    """The tail of a job's output. Bounded on both ends: at most ``lines`` lines,
    and the file is read from the end, so a 200 MB log costs one seek."""
    path = _job_dir(job_id) / "log"
    if not path.is_file():
        return ""
    lines = max(1, min(int(lines), 500))
    chunk = 8192
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= lines:
            step = min(chunk, size)
            size -= step
            handle.seek(size)
            data = handle.read(step) + data
    return b"\n".join(data.splitlines()[-lines:]).decode("utf-8", "replace")


def stop(job_id: str) -> Job:
    """Ask a job to stop. SIGTERM to the process group, never SIGKILL.

    ``atr_batch`` writes each page through a temp file and renames, so a job
    interrupted this way leaves finished pages and no half-written ones, and the
    same command resumes it. Killing harder would gain nothing and risk exactly
    the half-written file the rename is there to prevent.
    """
    job = status(job_id)
    if job.state == "running" and job.pid:
        try:
            os.killpg(os.getpgid(job.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            raise JobError(f"cannot signal job {job_id}: {exc}") from exc
    return status(job_id)
