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


#: Where pinned request bodies are kept, beside the specs: a spec with
#: ``revision: null`` does not say what a run read, the body that ran does.
REQUEST_DIR = SPEC_DIR.parent / "requests"
RAW_BASE = "https://raw.githubusercontent.com/thodel/agentic_historian/main/training/requests"
#: Where ``prepare.sbatch`` writes the trainer job id of a Slurm prepare job
#: (training-atr-models, ubelix/prepare.sbatch: ``$S/runs/prep-$SLURM_JOB_ID.jobid``).
PREP_JOBID = "/scratch/network/users/$USER/runs/prep-${PREP}.jobid"


def request_name(name: str, prepared: dict) -> str:
    """``<spec>@<rev7>.json`` — the revision this call pinned, so two files of
    one spec pinned to two uploads do not overwrite each other."""
    revs = sorted(set(prepared.get("pinned", {}).values()))
    tag = revs[0][:7] if len(revs) == 1 else ("+".join(r[:7] for r in revs) if revs else "pinned")
    return f"{name}@{tag}.json"


def request_state(name: str, prepared: dict, request_dir: Path = REQUEST_DIR) -> dict:
    """Is the pinned body in the repository, and is it this body?"""
    fname = request_name(name, prepared)
    path = Path(request_dir) / fname
    if not path.is_file():
        return {"file": fname, "present": False, "same": False}
    try:
        same = json.loads(path.read_text(encoding="utf-8")) == prepared["body"]
    except ValueError:
        same = False
    return {"file": fname, "present": True, "same": same}


def write_request(name: str, prepared: dict, request_dir: Path = REQUEST_DIR) -> Path:
    """Write the pinned body as ``training/requests/<spec>@<rev>.json`` (#739).
    For a working tree that is then committed — UBELIX fetches it from ``main``."""
    if not prepared["ok"]:
        raise SpecError("not writing a request with problems: " + "; ".join(prepared["problems"]))
    path = Path(request_dir) / request_name(name, prepared)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(prepared["body"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


_CORPUS_KEYS = ("hf_repo", "split", "revision", "train_projects", "eval_projects", "max_pages",
                "partition", "seed", "granularity", "all_projects", "chunk_size")


def corpus_of(body: dict) -> tuple:
    """What a prepare builds, as far as this side can tell: the datasets with
    everything that selects pages, and the engine. An approximation of the
    trainer's artefact key — the preflight on UBELIX names the real one."""
    def one(d):
        return tuple((k, json.dumps(d.get(k), sort_keys=True)) for k in _CORPUS_KEYS)
    return (body.get("engine"), tuple(sorted(one(d) for d in body.get("datasets") or [])))


def ubelix_commands(specs: list[tuple[str, dict]], states: dict) -> list[str]:
    """The commands a person runs for one or more specs (#739).

    Fixed on 26.10., where every one of these went wrong once:
    * the body is fetched on UBELIX from ``main`` — tei has no route to scp it;
    * everything runs **in** ``~/training-atr-models``, after a pull — from
      ``~`` an old copy of the scripts in ``~/ubelix`` answered;
    * ``JOB_ID`` is the **trainer** id ``prepare`` writes, read from its file,
      and the training waits for the prepare with ``--dependency``;
    * specs that build the same corpus get **one** build; the others queue
      behind it and adopt it in seconds.
    """
    out = ["# on UBELIX — every line in the trainer's own checkout",
           "cd ~/training-atr-models && git pull --ff-only",
           "mkdir -p ~/ubelix/specs ~/ubelix/logs"]
    missing = [n for n, _ in specs if not states[n]["present"] or not states[n]["same"]]
    for name, _ in specs:
        out.append(f"curl -fsSL -o ~/ubelix/specs/{name}.json {RAW_BASE}/{states[name]['file']}")
    groups: dict[tuple, list[str]] = {}
    for name, body in specs:
        groups.setdefault(corpus_of(body), []).append(name)
    first_of: dict[str, str] = {}
    for members in groups.values():
        for m in members:
            first_of[m] = members[0]
    var = {name: f"PREP_{i + 1}" for i, (name, _) in enumerate(specs)}
    grab = "2>&1 | tee /dev/stderr | sed -n 's/^Submitted batch job \\([0-9]*\\).*/\\1/p'"
    for name, _ in specs:
        lead = first_of[name]
        v = var[name]
        out.append("")
        if lead == name:
            out.append(f"# {name}: build the corpus")
            dep = ""
        else:
            out.append(f"# {name}: the same corpus as {lead} — queued behind its build, "
                       f"adopts it in seconds")
            dep = f" -- --dependency=afterok:${var[lead]}"
        out.append(f"{v}=$(ubelix/submit.sh ubelix/prepare.sbatch ~/ubelix/specs/{name}.json{dep} {grab})")
        out.append(f'echo "{name}: prepare ${{{v}:-NOT SUBMITTED — read the preflight above}}"')
    out.append("")
    out.append("# the trainings: each reads the TRAINER job id its prepare wrote (not the Slurm number)")
    out.append("# and waits for that prepare. The id file appears when the prepare STARTS; for one still")
    out.append("# queued the line says so and submits nothing — run it again once it runs (ubelix_queue).")
    for name, _ in specs:
        v = var[name]
        out.append(f'PREP=${v}; if [ -s {PREP_JOBID} ]; then JOB_ID=$(cat {PREP_JOBID}) '
                   f'ubelix/submit.sh ubelix/train.sbatch -- --partition=gpu --qos=job_gratis '
                   f'--cpus-per-task=12 --time=15:00:00 --dependency=afterok:$PREP; '
                   f'else echo "{name}: prep-$PREP.jobid not there yet — the prepare has not started"; fi')
    out.append("")
    out.append("# follow with ubelix_queue / ubelix_record")
    if missing:
        out = ([f"# FIRST, in a working tree of agentic_historian (not on tei): the pinned "
                f"request{'s are' if len(missing) > 1 else ' is'} not on main yet",
                "python3 -m agentic_historian train-spec --spec " + ",".join(missing)
                + " --write-request   # then commit training/requests/ and merge",
                ""] + out)
    if len(groups) < len(specs):
        out.insert(0, "# same corpus (by the datasets in the requests — the preflight names the "
                      "real artefact key): " + "; ".join(" + ".join(m) for m in groups.values() if len(m) > 1))
    return out


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
