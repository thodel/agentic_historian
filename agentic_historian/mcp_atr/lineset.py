"""
mcp_atr/lineset.py — an external model on a published line test set, as a job.

``external_lineset.py`` reads a line-level Hugging Face test set through the
external API (Gemini, Argon) and scores it the way the trainer scores its
adapters, so the number lands in the same table. The rules are ``jobs.py``'s:
a fixed argv, every value checked, output under ``VLM_TEST_ROOT/<run>``.

**Billed per line**, so it refuses to start without ``sample`` or ``full=True``
— the gateway's cost is a card already paid for, an API's an invoice.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import config
from mcp_atr import jobs
from mcp_atr.jobs import JobError

SCRIPT = Path(__file__).resolve().parents[1] / "external_lineset.py"
DATASET_RE = re.compile(r"^[A-Za-z0-9][\w.-]{0,63}/[A-Za-z0-9][\w.-]{0,95}$")
PROMPT_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*\.md$")
#: external_atr.REASONING_LEVELS, repeated so building argv imports no API client.
REASONING_LEVELS = ("none", "minimal", "low", "medium", "high")


def lineset_argv(run: str, model: str, *, dataset: Optional[str] = None,
                 sample: Optional[int] = None, full: bool = False, workers: int = 8,
                 prompt: Optional[str] = None, reasoning: Optional[str] = None) -> list[str]:
    if not model or not jobs.MODEL_RE.match(model):
        raise JobError(f"model: invalid model id {model!r}")
    if bool(sample) == bool(full):
        raise JobError("give exactly one of sample (lines, spread over the set) or full=true: "
                       "the API bills every line")
    out = Path(config.VLM_TEST_ROOT) / jobs.validate_run(run)
    argv = [jobs._python(), str(SCRIPT), "--model", model, "--out", str(out)]
    if dataset:
        if not DATASET_RE.match(dataset):
            raise JobError(f"dataset: owner/name on the Hub, got {dataset!r}")
        argv += ["--dataset", dataset]
    if sample:
        if not (1 <= int(sample) <= 100000):
            raise JobError("sample: 1 to 100000 lines")
        argv += ["--sample", str(int(sample))]
    else:
        argv.append("--full")
    if not (1 <= int(workers) <= 32):
        raise JobError("workers: 1 to 32")
    argv += ["--workers", str(int(workers))]
    if prompt:
        if not PROMPT_RE.match(prompt):
            raise JobError(f"prompt: a file name under prompts/, got {prompt!r}")
        argv += ["--prompt", prompt]
    if reasoning is not None:
        if reasoning not in ("", *REASONING_LEVELS):
            raise JobError(f"reasoning: one of {', '.join(REASONING_LEVELS)} or ''")
        argv += ["--reasoning", reasoning]
    return argv


def lineset_job(run: str, model: str, **kw) -> dict:
    argv = lineset_argv(run, model, **kw)
    return {"ok": True, "run_dir": str(Path(config.VLM_TEST_ROOT) / run), "billed": True,
            **jobs.start_and_peek("external-lineset", argv)}
