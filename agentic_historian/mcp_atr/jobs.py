"""
mcp_atr/jobs.py — start a batch from a request, and answer "how far is it".

An MCP call has to return in seconds; an ATR run over a few hundred pages takes
hours. So a tool cannot *be* the run — it can only start one and hand back a
handle. Everything here exists for that gap:

* **Detached children.** A job is started with ``start_new_session=True`` and
  keeps running when the MCP server is restarted or redeployed. The server is a
  door, not a supervisor.

  That takes ``KillMode=process`` in the unit as well, and it is not obvious why:
  ``start_new_session`` gives the child its own session and process group, but
  leaves it in the service's **cgroup**, and the cgroup is what systemd kills by.
  Under ``mixed`` a restart takes every running job with it — an 18-minute share
  walk on 2026-09-15 is how that was found.
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
    "list_outputs",
    "read_outputs",
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

#: Caps on one read of a run's transcriptions. A page of Kurrent is a couple of
#: kilobytes, so ten pages travel comfortably — but a caller can name a run of
#: several thousand, and the answer would then be neither carryable by the broker
#: nor readable by anyone. Paged instead, with the remainder stated rather than
#: silently dropped: an answer that stops without saying so is the same failure
#: as a transcription that stops at the token ceiling and looks complete.
MAX_READ_PAGES = 25
MAX_PAGE_CHARS = 20_000
READ_BUDGET_CHARS = 150_000

#: Keys listed in one inventory. Past this the count is still exact; the names
#: are not all there, and the reply says so.
MAX_LISTED_KEYS = 500


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

    ``start_new_session=True`` puts the child in its own session and process
    group. That is necessary and not sufficient: the child stays in the service's
    cgroup, so surviving a restart also takes ``KillMode=process`` in the unit.
    With the ``mixed`` this shipped with, systemd SIGKILLed the whole cgroup after
    the main process went, and every running job died with the deploy.
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


# ── reading a run's transcriptions ───────────────────────────────────────────

def _run_dir(run: str) -> Path:
    """The run's directory under ``VLM_TEST_ROOT``, or a ``JobError``.

    The name is validated first, so the join cannot leave the root.
    """
    directory = Path(config.VLM_TEST_ROOT) / validate_run(run)
    if not directory.is_dir():
        raise JobError(f"no run directory for {run!r}")
    return directory


def _model_dir(run: str, model: str) -> Path:
    directory = _run_dir(run) / validate_models([model])[0]
    if not directory.is_dir():
        raise JobError(f"run {run!r} has no output for model {model!r}")
    return directory


def _page_path(directory: Path, key: str) -> Path:
    """``<key>.txt`` inside ``directory``, or a ``JobError``.

    Keys are filenames the runner chose from the corpus, so they carry whatever
    the archive's folders carry — spaces, umlauts, parentheses. A charset check
    would reject real pages, so this checks the thing that actually matters: the
    *resolved* path has to stay inside the model's directory. That catches
    ``../`` and a symlink pointing out of it alike, which a regex would not.
    """
    root = directory.resolve()
    candidate = (directory / f"{key}.txt").resolve()
    if candidate != root and root not in candidate.parents:
        raise JobError(f"key {key!r} does not name a page in this run")
    if not candidate.is_file():
        raise JobError(f"no transcription for key {key!r}")
    return candidate


def _was_truncated(txt_path: Path) -> Optional[bool]:
    """Whether the model stopped at the token ceiling, from the sibling JSON.

    A page cut off at the ceiling comes back as an ordinary success and reads as
    a normal transcription until the last line; the flag is the only thing that
    distinguishes it, so it travels with every text this module hands out.
    """
    try:
        data = json.loads(txt_path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return bool(data.get("truncated"))


def list_outputs(run: str, model: Optional[str] = None) -> dict:
    """What a run holds: per model, how many pages and which keys.

    The inventory a caller needs before asking for text — `read_outputs` pages
    through the same keys in the same order.
    """
    directory = _run_dir(run)
    wanted = validate_models([model])[0] if model else None
    models: dict = {}
    for child in sorted(directory.iterdir()):
        if not child.is_dir() or (wanted and child.name != wanted):
            continue
        keys = sorted(p.stem for p in child.glob("*.txt"))
        entry: dict = {"pages": len(keys), "keys": keys[:MAX_LISTED_KEYS]}
        if len(keys) > MAX_LISTED_KEYS:
            entry["keys_omitted"] = len(keys) - MAX_LISTED_KEYS
        models[child.name] = entry
    if wanted and wanted not in models:
        raise JobError(f"run {run!r} has no output for model {model!r}")
    out: dict = {"run": run, "run_dir": str(directory), "models": models}
    extras = sorted(p.name for p in directory.glob("*.*") if p.is_file())
    if extras:
        out["run_files"] = extras
    return out


def read_outputs(run: str, model: str, keys: Optional[Sequence[str]] = None,
                 offset: int = 0, limit: int = 10) -> dict:
    """The transcriptions themselves, capped and paged.

    Without ``keys`` it walks the model's pages in key order from ``offset``;
    with them it reads exactly those. Either way the reply says what it left
    out — how many pages remain, and whether a text was cut to fit — because the
    caller is usually copying these somewhere else, and a silent gap there
    becomes a corpus with holes nobody can see.
    """
    directory = _model_dir(run, model)
    limit = max(1, min(int(limit), MAX_READ_PAGES))
    offset = max(0, int(offset))

    if keys:
        selected = [str(k) for k in keys][:MAX_READ_PAGES]
        remaining = 0
    else:
        available = sorted(p.stem for p in directory.glob("*.txt"))
        selected = available[offset:offset + limit]
        remaining = max(0, len(available) - (offset + len(selected)))

    pages, budget = [], READ_BUDGET_CHARS
    for index, key in enumerate(selected):
        path = _page_path(directory, key)
        text = path.read_text(encoding="utf-8")
        entry: dict = {"key": key, "chars": len(text),
                       "truncated_by_model": _was_truncated(path)}
        room = min(MAX_PAGE_CHARS, budget)
        if len(text) > room:
            entry["text"] = text[:room]
            entry["text_cut"] = True
        else:
            entry["text"] = text
        budget -= len(entry["text"])
        pages.append(entry)
        if budget <= 0 and index + 1 < len(selected):
            remaining += len(selected) - (index + 1)
            break

    return {"run": run, "model": model, "offset": offset,
            "returned": len(pages), "remaining": remaining,
            "next_offset": offset + len(pages) if remaining and not keys else None,
            "pages": pages}


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
