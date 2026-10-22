"""train_kraken.py — fine-tune a kraken model on one volume's ground truth,
through the gateway's ``/train/*`` (the trainer on asteraix), from tei.

    python agentic_historian/ruemlang/train_kraken.py request   # print the body, send nothing
    python agentic_historian/ruemlang/train_kraken.py submit    # POST /train/jobs
    python agentic_historian/ruemlang/train_kraken.py status JOB_ID
    python agentic_historian/ruemlang/train_kraken.py log JOB_ID [--stage train]

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


def request_body(args) -> dict:
    return {
        "engine": "kraken",
        "model_id": args.model_id,
        "datasets": [{
            "hf_repo": args.hf_repo,
            "train_projects": [args.train_project],
            "partition": 0.9,
            "granularity": "page",
        }],
        "base_model": args.base_model,
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
        "notes": ("Bern Disputation 1528, A V 1443 (Hertwig), pages 3-60 minus the held-out "
                  "test pages; fine-tune for the ruemlang pipeline as a fourth reader."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("request", "submit"):
        p = sub.add_parser(name)
        p.add_argument("--model-id", default=DEFAULTS["model_id"])
        p.add_argument("--hf-repo", default=DEFAULTS["hf_repo"])
        p.add_argument("--train-project", default=DEFAULTS["train_project"])
        p.add_argument("--base-model", default=DEFAULTS["base_model"])
        p.add_argument("--batch-size", type=int, default=16)
        p.add_argument("--epochs", type=int, default=60)
        p.add_argument("--lrate", type=float, default=1e-4)
    p = sub.add_parser("status"); p.add_argument("job_id")
    p = sub.add_parser("log"); p.add_argument("job_id"); p.add_argument("--stage", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "request":
        print(json.dumps(request_body(args), indent=1, ensure_ascii=False))
        return 0
    from agent_a.training_client import TrainingClient
    with TrainingClient() as tc:
        if args.cmd == "submit":
            job = tc.submit(request_body(args))
            print(f"submitted: {job.id}  status={job.status}")
            print(f"follow: python agentic_historian/ruemlang/train_kraken.py status {job.id}")
        elif args.cmd == "status":
            job = tc.get_job(args.job_id)
            print(json.dumps(getattr(job, "raw", None) or job.__dict__, indent=1,
                             default=str, ensure_ascii=False)[:6000])
        else:
            print(tc.get_log(args.job_id, stage=args.stage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
