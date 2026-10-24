"""train_kraken.py — fine-tune a kraken model on one volume's ground truth,
through the gateway's ``/train/*`` (the trainer on asteraix), from tei.

    python agentic_historian/ruemlang/train_kraken.py request   # print the body, send nothing
    python agentic_historian/ruemlang/train_kraken.py submit    # POST /train/jobs
    python agentic_historian/ruemlang/train_kraken.py status JOB_ID
    python agentic_historian/ruemlang/train_kraken.py log [JOB_ID] [--stage train] [--lines N]

The defaults are the Hertwig run (A V 1443): the private dataset that
``gt_export.py`` wrote and ``upload_hf`` published, its ``_train`` project only
(``_test`` stays unseen), fine-tuned from ``kraken-cursive_inzigkofen`` — a
fifteenth-century German cursive model, itself a fine-tune of CATMuS Medieval,
and the nearest hand among the kraken models the gateway serves.

**Why these numbers.** ~2 000 training lines is two orders of magnitude below
where training from scratch works (docs/TRAINING.md in serving-atr-inference:
"fine-tune rather than train from scratch below ~100 K lines"), so: a base
model, ``resize: union`` for the characters the base does not know (ů, ꝛ, ...),
and a batch size scaled to the corpus. At batch 16 and 0.9 of ~2 000 lines,
one epoch is ~115 optimizer steps; 60 epochs are ~6 900, far above the
trainer's fine-tune floor of 500 (``convergence.FLOOR_FINETUNE``), and
``quit: early`` with ``lag`` 10 stops when validation stops improving.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

DEFAULTS = {
    "model_id": "kraken-bern-hertwig-v1",
    "hf_repo": "dh-unibe/image-text_bern-disputation-1528",
    "train_project": "A_V_1443_Hertwig_train",
    "base_model": "kraken-cursive_inzigkofen",
}


#: Where the trainer keeps the models it trained, as the registry's
#: ``local_path`` names them. The trainer takes a base model as a registry id of
#: the *curated* list, a Zenodo DOI, or a file — not the id of a model it trained
#: itself (24.10.: "base_model 'kraken-bern-hertwig-v2' is not a file, a
#: registry id, or a Zenodo reference"). ``trained:<id>`` names one of those by
#: id and is turned into its file here, so a caller never handles a path.
TRAINED_ROOT = os.environ.get(
    "ATR_TRAINED_ROOT", "/mnt/wbkolleg_dh_1/Textrecognition_Training/training_folder/trained")


def resolve_base(base: str) -> str:
    """``trained:kraken-x`` → ``<TRAINED_ROOT>/kraken-x/kraken-x.mlmodel``; else as given."""
    if base.startswith("trained:"):
        mid = base.split(":", 1)[1]
        if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", mid):
            raise SystemExit(f"not a model id: {mid!r}")
        return f"{TRAINED_ROOT}/{mid}/{mid}.mlmodel"
    return base


def projects(value) -> list[str]:
    """``--train-project`` may be given more than once, or as ``a,b``."""
    values = value if isinstance(value, list) else [value]
    return [p.strip() for v in values for p in str(v).split(",") if p.strip()]


def request_body(args) -> dict:
    train = projects(args.train_project)
    return {
        "engine": "kraken",
        "model_id": args.model_id,
        "datasets": [{
            "hf_repo": args.hf_repo,
            "train_projects": train,
            "partition": 0.9,
            "granularity": "page",
        }],
        "base_model": resolve_base(args.base_model),
        "params": {
            "resize": "union",
            "batch_size": args.batch_size,
            "epochs": args.epochs,
            "quit": "early",
            "lag": 10,
            "min_epochs": 15,
            "lrate": args.lrate,
            "schedule": "cosine",
            "augment": True,
            "normalization": "NFD",
        },
        "notes": getattr(args, "notes", None) or (f"{' + '.join(train)} from {args.hf_repo}, fine-tuned from "
                                f"{args.base_model} for the ruemlang pipeline (reader D)."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("request", "submit"):
        p = sub.add_parser(name)
        p.add_argument("--model-id", default=DEFAULTS["model_id"])
        p.add_argument("--hf-repo", default=DEFAULTS["hf_repo"])
        p.add_argument("--train-project", action="append", default=None,
                       help="repeatable, or a,b: several projects train one model")
        p.add_argument("--base-model", default=DEFAULTS["base_model"])
        p.add_argument("--batch-size", type=int, default=16)
        p.add_argument("--epochs", type=int, default=60)
        p.add_argument("--lrate", type=float, default=1e-4)
        p.add_argument("--notes", default=None, help="free text on the job record")
    p = sub.add_parser("list", help="recent jobs, newest first, with their ids")
    p.add_argument("--model-id", default=DEFAULTS["model_id"])
    p = sub.add_parser("status"); p.add_argument("job_id", nargs="?", default=None,
                                                 help="default: the newest job of the model id")
    p.add_argument("--model-id", default=DEFAULTS["model_id"])
    p = sub.add_parser("log"); p.add_argument("job_id", nargs="?", default=None)
    p.add_argument("--model-id", default=DEFAULTS["model_id"])
    p.add_argument("--stage", default=None, help="prepare|compile|train|test|register")
    p.add_argument("--lines", type=int, default=None)
    args = ap.parse_args(argv)
    if getattr(args, "train_project", "unset") is None:
        args.train_project = [DEFAULTS["train_project"]]

    if args.cmd == "request":
        print(json.dumps(request_body(args), indent=1, ensure_ascii=False))
        return 0
    from agent_a.training_client import Stage, TrainingClient, TrainingJobNotFound
    with TrainingClient() as tc:
        def newest(model_id):
            jobs = [j for j in tc.list_jobs(limit=50) if j.model_id == model_id]
            return jobs[0] if jobs else None

        if args.cmd == "submit":
            job = tc.submit(request_body(args))
            # The 202 answer does not always carry the id under "id"
            # (15.10.: it printed empty); the job list does.
            job_id = job.id or getattr(newest(args.model_id), "id", "")
            print(f"submitted: {job_id or '?'}  status={job.status.value}")
            print("follow: python agentic_historian/ruemlang/train_kraken.py status "
                  f"{job_id}".rstrip())
        elif args.cmd == "list":
            for j in tc.list_jobs(limit=20):
                if args.model_id in ("", "all") or j.model_id == args.model_id:
                    print(f"{j.id}  {j.status.value:10} stage={j.stage or '-':10} "
                          f"cer={j.cer if j.cer is not None else '-'}  {j.model_id}")
        elif args.cmd == "status":
            job = tc.get_job(args.job_id) if args.job_id else newest(args.model_id)
            if job is None:
                print(f"no job for {args.model_id}")
                return 1
            print(f"{job.id}  status={job.status.value}  stage={job.stage}  cer={job.cer}  "
                  f"wer={job.wer}")
            if job.queued_reason:
                print(f"queued: {job.queued_reason}")
            if job.error:
                print(f"error: {job.error}")
            for stage, state in (job.stages or {}).items():
                print(f"  {stage}: {state}")
            # Only the numbers that say how far it is; the full record is in the log.
            shown = ("epoch", "epochs", "val_accuracy", "train_lines", "steps_per_epoch",
                     "total_steps", "energy_wh")
            prog = {k: job.progress[k] for k in shown if job.progress.get(k) is not None}
            if prog:
                print("progress: " + ", ".join(f"{k}={v}" for k, v in prog.items()))
            # The gate's verdict is on the record but not on TrainJob.
            rec = tc._get(f"/train/jobs/{job.id}").json()
            for k in ("promoted", "promotion_reason"):
                if rec.get(k) is not None:
                    print(f"{k}: {rec[k]}")
            if job.log_tail:
                print("--- log tail ---")
                print(job.log_tail[-2500:])
        else:
            job_id = args.job_id or getattr(newest(args.model_id), "id", None)
            if not job_id:
                print(f"no job for {args.model_id}")
                return 1
            try:
                print(tc.get_log(job_id, stage=Stage(args.stage) if args.stage else None,
                                 lines=args.lines))
            except TrainingJobNotFound:
                # A stage that wrote nothing (register, often) is a 404 too.
                print(f"no {args.stage or 'train'} log for {job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
