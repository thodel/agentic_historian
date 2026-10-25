"""train_spec.py — a training request from ``training/specs/``, pinned and sent (#715).

The specs in ``training/specs/`` are the record of what a run was asked to be:
in the repository, reviewed, the same file for UBELIX and asteraix. Two things
were still done by hand between the file and the trainer:

* **The dataset revision.** A spec written before its dataset was uploaded
  says ``revision: null``, and the trainer then reads whatever ``main`` is on
  the day it fetches — a different set of pages after the next upload, with
  nothing in the job record to say so. :func:`prepare` resolves every
  ``null`` to the hub's current commit and says which it chose.
* **Notes for people.** Keys that start with ``_`` (``"_note"``) are for the
  reader of the file; they are dropped from the body sent, so the trainer's
  schema never has to tolerate them.

Actions: ``request`` builds the body and sends nothing; ``submit`` sends it to
the gateway's trainer (asteraix) and refuses a second job for one
``model_id``; ``status`` follows the newest job of it; ``ubelix`` prints the
two-stage Slurm commands — submitting on UBELIX runs on a person's account and
quota, so it stays a person's act.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

import hf_export as hf

SPEC_DIR = Path(__file__).resolve().parents[1] / "training" / "specs"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
ENGINES = ("kraken", "vllm", "trocr")
ACTIONS = ("request", "submit", "status", "ubelix")


class SpecError(Exception):
    """The spec cannot be used as it is; the message says why."""


def spec_names(spec_dir: Path = SPEC_DIR) -> list[str]:
    return sorted(p.stem for p in Path(spec_dir).glob("*.json"))


def load(name: str, spec_dir: Path = SPEC_DIR) -> dict:
    name = (name or "").removesuffix(".json")
    if not NAME_RE.match(name):
        raise SpecError(f"a spec is a name under training/specs/, got {name!r}")
    path = Path(spec_dir) / f"{name}.json"
    if not path.is_file():
        raise SpecError(f"no spec {name!r} — there are: {', '.join(spec_names(spec_dir)) or 'none'}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SpecError(f"{name}.json is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise SpecError(f"{name}.json is not an object")
    return data


def _strip_notes(value):
    if isinstance(value, dict):
        return {k: _strip_notes(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [_strip_notes(v) for v in value]
    return value


def _hub_sha(repo: str) -> Optional[str]:
    return hf.hub_state(repo).get("sha")


def resolve_base(base: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``(what the trainer is sent, a problem or None)`` for a ``base_model``.

    The trainer takes a curated registry id, a Zenodo DOI or a **file** — not
    the id of a model it trained itself (24.10.: "base_model
    'kraken-bern-hertwig-v2' is not a file, a registry id, or a Zenodo
    reference"). ``trained:<id>`` names such a model and becomes its file,
    by the rule `ruemlang/train_kraken.py` uses; the file is checked here,
    on the share both machines read, before any GPU is asked for.
    """
    if not base or not str(base).startswith("trained:"):
        return base, None
    from ruemlang.train_kraken import resolve_base as _resolve

    try:
        path = _resolve(str(base))
    except SystemExit as exc:
        return base, str(exc)
    if not Path(path).is_file():
        return path, (f"base_model {base}: no file at {path} — is the model id right, "
                      f"is the training share mounted?")
    return path, None


def prepare(spec: dict, *, resolve: Callable[[str], Optional[str]] = _hub_sha) -> dict:
    """The body to send, the revisions it pinned, and what is wrong with it."""
    body = _strip_notes(spec)
    problems = []
    if body.get("base_model"):
        body["base_model"], problem = resolve_base(body["base_model"])
        if problem:
            problems.append(problem)
    if body.get("engine") not in ENGINES:
        problems.append(f"engine must be one of {', '.join(ENGINES)}, got {body.get('engine')!r}")
    if not body.get("model_id"):
        problems.append("no model_id")
    datasets = body.get("datasets") or []
    if not datasets:
        problems.append("no datasets")
    pinned = {}
    for ds in datasets:
        repo = ds.get("hf_repo")
        if not repo:
            problems.append("a dataset without hf_repo")
            continue
        if ds.get("revision"):
            continue
        sha = resolve(repo)
        if sha:
            ds["revision"] = sha
            pinned[repo] = sha
        else:
            problems.append(f"{repo}: revision is null and the hub named no commit — "
                            f"is the dataset uploaded, is the token set?")
    for ds in datasets:
        overlap = set(ds.get("train_projects") or []) & set(ds.get("eval_projects") or [])
        if overlap:
            problems.append(f"{ds.get('hf_repo')}: projects in train and eval: {sorted(overlap)}")
    return {"body": body, "pinned": pinned, "problems": problems, "ok": not problems}


def ubelix_commands(name: str, body: dict) -> list[str]:
    """The two stages as a person types them on UBELIX (training-atr-models)."""
    return [
        f"# on tei: the pinned request, as UBELIX reads it",
        f"cd /home/dh/agentic_historian && .venv/bin/python3 -m agentic_historian train-spec "
        f"--spec {name} --body-only > /tmp/{name}.json && "
        f"scp /tmp/{name}.json ubelix:~/ubelix/specs/{name}.json",
        f"# on UBELIX, in training-atr-models:",
        f"ubelix/submit.sh ubelix/prepare.sbatch ~/ubelix/specs/{name}.json",
        f"JOB_ID=<prepare job id> ubelix/submit.sh ubelix/train.sbatch -- "
        f"--partition=gpu --qos=job_gratis --cpus-per-task=12 --time=15:00:00",
        f"# follow with ubelix_queue / ubelix_record ({body.get('model_id')})",
    ]


def format_prepared(name: str, prepared: dict) -> str:
    b = prepared["body"]
    lines = [f"spec      : {name}",
             f"engine    : {b.get('engine')}   model_id: {b.get('model_id')}   "
             f"base: {b.get('base_model') or '—'}",
             f"datasets  : {len(b.get('datasets') or [])}"]
    for repo, sha in prepared["pinned"].items():
        lines.append(f"pinned    : {repo} @ {sha}")
    for ds in (b.get("datasets") or []):
        if ds.get("eval_projects"):
            lines.append(f"held out  : {ds['hf_repo']}: {', '.join(ds['eval_projects'])}")
    if prepared["problems"]:
        lines += ["", f"{len(prepared['problems'])} problem(s):"]
        lines += [f"  - {p}" for p in prepared["problems"]]
    return "\n".join(lines)
