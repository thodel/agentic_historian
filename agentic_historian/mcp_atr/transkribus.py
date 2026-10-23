"""
mcp_atr/transkribus.py — the Transkribus volume pipeline, startable over MCP.

What the Rümlang (A V 1446) and Hertwig (A V 1443) runs did by hand on tei, one
command at a time, as jobs: export a volume's ground truth, fine-tune a kraken
model on it through the gateway's trainer, measure every reader against the
transcribed pages, then read the rest of the volume and post it back to
Transkribus as a new IN_PROGRESS version. ``docs/HERTWIG_RUN.md`` is the run
these tools were cut from; ``docs/TRANSKRIBUS_PIPELINE_MCP.md`` the walkthrough.

The rules are ``jobs.py``'s and nothing here loosens them:

* **Fixed argv.** Each tool runs one of three scripts in ``ruemlang/`` with a
  list of flags built here. A caller picks a document, pages, models, a run
  name — never a path, a command or a flag. Every value is checked against a
  pattern or a range before it goes near the list.
* **Names, not paths.** Output lives under ``VLM_TEST_ROOT/<run>`` (pipeline)
  and ``VLM_TEST_ROOT/<tree>`` (ground-truth export); both names pass
  ``jobs.validate_run``.
* **The steps that change something outside tei default to not doing it.**
  ``kraken_train`` prints the request unless ``action="submit"``;
  ``transkribus_pipeline`` builds the PAGE XML but posts nothing unless
  ``upload=True``; ``gt_export`` is a dry run unless ``dry_run=False``. An
  upload only ever adds a version — Transkribus keeps every earlier one.

One step is deliberately **not** here: switching on a trained model whose gate
did not run (``kraken-bern-hertwig-v2``, 22.10.: a cache hit left the job no
held-out page). That rewrites a registration on the trainer's share from the
trainer's own venv on asteraix; tei has neither, and a tool that could flip
``enabled`` on any model would be a much larger door than the rest of this
file. The fix belongs in the trainer (the gate must find a page on a cache hit).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

import config
from mcp_atr import jobs
from mcp_atr.jobs import JobError

#: The three scripts. Resolved from this checkout, so the server runs the
#: version it was deployed with.
_RUEMLANG = Path(__file__).resolve().parents[1] / "ruemlang"
PIPELINE = _RUEMLANG / "pipeline.py"
GT_EXPORT = _RUEMLANG / "gt_export.py"
TRAIN_KRAKEN = _RUEMLANG / "train_kraken.py"

DEFAULT_COLLECTION = "2313234"          # Bern_Reformation

#: Transkribus ids are integers.
ID_RE = re.compile(r"^[0-9]{1,10}$")
#: ``3-60``, ``8,17,26``, ``1-2,61-735``. Page numbers, commas, ranges; nothing else.
PAGES_RE = re.compile(r"^[0-9]{1,4}(-[0-9]{1,4})?(,[0-9]{1,4}(-[0-9]{1,4})?){0,199}$")
#: A Hugging Face dataset id, ``owner/name``.
HF_REPO_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,95}/[A-Za-z0-9][\w.-]{0,95}$")
#: A base model is a registry id or a Zenodo DOI (the trainer checks which).
DOI_RE = re.compile(r"^10\.[0-9]{4,9}/[\w.:-]{1,64}$")
#: The trainer's job ids: ``20261022T134133Z-kraken-bern-hertwig-v2``.
TRAIN_JOB_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9_.:-]{1,80}$")

TRAIN_ACTIONS = ("request", "submit", "status", "list", "log")
TRAIN_STAGES = ("prepare", "compile", "train", "test", "register")
PIPELINE_STEPS = ("plan", "eval", "run", "upload")
READERS = ("A", "B", "D")


# ── validation ───────────────────────────────────────────────────────────────

def _id(value, what: str) -> str:
    value = str(value or "").strip()
    if not ID_RE.match(value):
        raise JobError(f"{what} must be a Transkribus id (digits), got {value!r}")
    return value


def _pages(value: Optional[str], what: str = "pages") -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    value = re.sub(r"\s+", "", str(value))
    if not PAGES_RE.match(value):
        raise JobError(f"{what} must look like 3-60 or 8,17,26 — got {value!r}")
    return value


def _model(value: Optional[str], what: str) -> Optional[str]:
    """A gateway model id, or None for "leave this reader out"."""
    if value is None or str(value).strip().lower() in ("", "none"):
        return None
    value = str(value).strip()
    if not jobs.MODEL_RE.match(value):
        raise JobError(f"{what}: invalid model id {value!r}")
    return value


def _number(value, lo, hi, what: str, kind=float):
    try:
        v = kind(value)
    except (TypeError, ValueError):
        raise JobError(f"{what} must be a number, got {value!r}") from None
    if not lo <= v <= hi:
        raise JobError(f"{what} must lie in [{lo}, {hi}], got {v}")
    return v


def _py() -> str:
    return jobs._python()


# ── ground truth → dataset ───────────────────────────────────────────────────

def gt_export_argv(doc: str, pages: str, tree: str, *, test_pages: Optional[str] = None,
                   prefix: str = "A_V_1443_Hertwig", collection: str = DEFAULT_COLLECTION,
                   dry_run: bool = True) -> list[str]:
    argv = [_py(), str(GT_EXPORT), "--collection", _id(collection, "collection"),
            "--doc", _id(doc, "doc"), "--pages", _pages(pages) or "",
            "--tree", jobs.validate_run(tree), "--prefix", jobs.validate_run(prefix)]
    if not argv[argv.index("--pages") + 1]:
        raise JobError("pages is required: the transcribed pages to export, e.g. 3-60")
    tp = _pages(test_pages, "test_pages")
    if tp:
        argv += ["--test-pages", tp]
    if dry_run:
        argv.append("--dry-run")
    return argv


def gt_export_job(doc: str, pages: str, tree: str, test_pages: Optional[str] = None,
                  prefix: str = "A_V_1443_Hertwig", collection: str = DEFAULT_COLLECTION,
                  dry_run: bool = True) -> dict:
    argv = gt_export_argv(doc, pages, tree, test_pages=test_pages, prefix=prefix,
                          collection=collection, dry_run=dry_run)
    return {"ok": True, "tree": str(Path(config.VLM_TEST_ROOT) / tree), "dry_run": dry_run,
            "next": "upload_hf(tree=…) publishes it as a private dataset" if not dry_run else
                    "repeat with dry_run=false to write the tree",
            **jobs.start_and_peek("transkribus-gt-export", argv)}


# ── kraken fine-tune through the gateway's trainer ───────────────────────────

def kraken_train_argv(action: str, *, model_id: Optional[str] = None,
                      hf_repo: Optional[str] = None, train_project: Optional[str] = None,
                      base_model: Optional[str] = None, lrate: Optional[float] = None,
                      epochs: Optional[int] = None, batch_size: Optional[int] = None,
                      job_id: Optional[str] = None, stage: Optional[str] = None,
                      lines: Optional[int] = None) -> list[str]:
    if action not in TRAIN_ACTIONS:
        raise JobError(f"action must be one of {', '.join(TRAIN_ACTIONS)}, got {action!r}")
    argv = [_py(), str(TRAIN_KRAKEN), action]
    mid = _model(model_id, "model_id")
    if action in ("request", "submit"):
        if mid:
            argv += ["--model-id", mid]
        if hf_repo:
            if not HF_REPO_RE.match(hf_repo):
                raise JobError(f"hf_repo must be owner/name, got {hf_repo!r}")
            argv += ["--hf-repo", hf_repo]
        if train_project:
            argv += ["--train-project", jobs.validate_run(train_project)]
        if base_model:
            if not (jobs.MODEL_RE.match(base_model) or DOI_RE.match(base_model)):
                raise JobError(f"base_model must be a registry id or a Zenodo DOI, got {base_model!r}")
            argv += ["--base-model", base_model]
        if lrate is not None:
            argv += ["--lrate", repr(_number(lrate, 1e-6, 1e-2, "lrate"))]
        if epochs is not None:
            argv += ["--epochs", str(_number(epochs, 1, 500, "epochs", int))]
        if batch_size is not None:
            argv += ["--batch-size", str(_number(batch_size, 1, 256, "batch_size", int))]
        return argv
    if action == "list":
        return argv + ["--model-id", mid or "all"]
    if job_id:
        if not TRAIN_JOB_RE.match(job_id):
            raise JobError(f"job_id must be a trainer job id like 20261022T134133Z-<model>, got {job_id!r}")
        argv.append(job_id)
    elif mid:
        argv += ["--model-id", mid]
    if action == "log":
        if stage:
            if stage not in TRAIN_STAGES:
                raise JobError(f"stage must be one of {', '.join(TRAIN_STAGES)}")
            argv += ["--stage", stage]
        if lines is not None:
            argv += ["--lines", str(_number(lines, 1, 5000, "lines", int))]
    return argv


def kraken_train_job(action: str = "request", **kw) -> dict:
    argv = kraken_train_argv(action, **kw)
    return {"ok": True, "action": action, **jobs.start_and_peek(f"kraken-train-{action}", argv)}


# ── recognize · adjudicate · PAGE XML · upload ───────────────────────────────

def pipeline_argv(step: str, run: str, *, doc: Optional[str] = None,
                  pages: Optional[str] = None, collection: str = DEFAULT_COLLECTION,
                  extra_model: Optional[str] = None, primary: Optional[str] = None,
                  only_extra: bool = False, line_model: Optional[str] = None,
                  trocr_model: Optional[str] = None, page_model: Optional[str] = None,
                  no_llm: bool = False, upload: bool = False, limit: Optional[int] = None,
                  variants: Optional[str] = None) -> list[str]:
    if step not in PIPELINE_STEPS:
        raise JobError(f"step must be one of {', '.join(PIPELINE_STEPS)}, got {step!r}")
    out = Path(config.VLM_TEST_ROOT) / jobs.validate_run(run)
    argv = [_py(), str(PIPELINE), step, "--out", str(out)]
    if step in ("plan", "eval", "run"):
        # Required: the CLI's own default is Rümlang, which over MCP would be a
        # silent choice nobody made.
        argv += ["--collection", _id(collection, "collection"), "--doc", _id(doc, "doc")]
    p = _pages(pages)
    if p:
        argv += ["--pages", p]
    if limit is not None:
        argv += ["--limit", str(_number(limit, 1, 10000, "limit", int))]
    if step == "upload":
        if not upload:
            argv.append("--dry-run")
        return argv
    if step == "plan":
        return argv            # writes plan.json under the run; reads Transkribus only
    # eval and run read and decide
    em = _model(extra_model, "extra_model")
    if em:
        argv += ["--extra-model", em]
    if primary:
        if primary not in READERS:
            raise JobError(f"primary must be one of {', '.join(READERS)}")
        if primary == "D" and not em:
            raise JobError("primary D needs extra_model")
        argv += ["--primary", primary]
    if only_extra:
        if not em:
            raise JobError("only_extra needs extra_model: it is the one reader left")
        argv += ["--line-model", "none", "--trocr-model", "none", "--page-model", "none"]
    else:
        for flag, value, what in (("--line-model", line_model, "line_model"),
                                  ("--trocr-model", trocr_model, "trocr_model"),
                                  ("--page-model", page_model, "page_model")):
            if value is not None:
                argv += [flag, _model(value, what) or "none"]
    if no_llm:
        argv.append("--no-llm")
    if step == "eval" and variants is not None:
        if variants and not re.match(r"^[ABD](,[ABD]){0,2}$", variants):
            raise JobError("variants is a comma list of A, B, D (or empty)")
        argv += ["--variants", variants]
    if step == "run" and not upload:
        argv.append("--dry-run")      # everything up to the PAGE XML, nothing posted
    return argv


def pipeline_job(step: str, run: str, **kw) -> dict:
    argv = pipeline_argv(step, run, **kw)
    posts = step in ("run", "upload") and kw.get("upload")
    return {"ok": True, "step": step, "run_dir": str(Path(config.VLM_TEST_ROOT) / run),
            "posts_to_transkribus": bool(posts),
            **jobs.start_and_peek(f"transkribus-{step}", argv)}


def pipeline_status(run: str) -> dict:
    """What a pipeline run has on disk: the plan, per-page files, the eval table."""
    root = Path(config.VLM_TEST_ROOT) / jobs.validate_run(run)
    if not root.is_dir():
        raise JobError(f"no pipeline run {run!r} under {config.VLM_TEST_ROOT}")
    out: dict = {"ok": True, "run_dir": str(root)}
    plan = root / "plan.json"
    if plan.is_file():
        data = json.loads(plan.read_text(encoding="utf-8"))
        out["planned_pages"] = len(data.get("pages", []))
        out["eval_plan"] = bool(data.get("eval"))
    pages = root / "pages"
    counts: dict[str, int] = {}
    errors = []
    if pages.is_dir():
        for d in pages.iterdir():
            if not d.is_dir():
                continue
            for f in d.iterdir():
                name = f.name
                if name.endswith(".error.json"):
                    errors.append(f"{d.name}/{name}")
                    key = "errors"
                elif name.startswith("rec_") and name.endswith(".json"):
                    key = name[4:-5]
                elif name in ("adjudicated.json", "page.xml", "uploaded.json"):
                    key = name.split(".")[0]
                else:
                    continue
                counts[key] = counts.get(key, 0) + 1
    out["on_disk"] = counts
    if errors:
        out["errors"] = sorted(errors)[:20]
    table = root / "eval" / "eval.json"
    if table.is_file():
        rows = json.loads(table.read_text(encoding="utf-8"))
        cols = sorted({k for r in rows for k in r if k not in ("page", "gt_chars", "summary")
                       and not k.endswith("~seg") and isinstance(r.get(k), (int, float))})
        out["eval_mean_cer"] = {k: round(sum(r[k] for r in rows if isinstance(r.get(k), (int, float)))
                                         / max(1, sum(1 for r in rows if isinstance(r.get(k), (int, float)))), 4)
                                for k in cols}
        out["eval_pages"] = [r["page"] for r in rows]
    mine = [j.as_dict() for j in jobs.list_jobs(50)
            if j.kind.startswith("transkribus-") and str(root) in " ".join(j.argv)]
    if mine:
        latest = mine[0]
        out["latest_job"] = {k: latest[k] for k in ("job_id", "kind", "state", "started_at",
                                                    "finished_at", "exit_code")}
    return out
