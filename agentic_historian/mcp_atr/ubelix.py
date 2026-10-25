"""
mcp_atr/ubelix.py — read UBELIX's queue from a Claude session (#674).

A cloud session cannot reach UBELIX: it sits outside the university network and
its egress proxy refuses ssh in any case (``docs/CLAUDE_CODE_CONNECTIVITY.md``).
tei can, and the MCP server on tei is reachable from the session. So the three
tools here ssh from tei to UBELIX, and every question of the kind "is the
training still queued?" stops costing a round trip through a person — three of
them on 20./21.10.2026 for the two olmOCR arms of training-atr-models#180.

**What the key can do on UBELIX is decided there, not here.** It is installed
with ``restrict,command="…/atr-mcp-ro"``, so sshd runs that script whatever this
side sends (``deploy/ubelix-ro/``). This module validates anyway, for two
reasons: a refused request should not cost an ssh round trip, and the forced
command is configuration on another machine that nothing here can check. Each
side holds the line on its own.

Same rules as :mod:`mcp_atr.jobs`: fixed argv, no shell, every argument matched
against a pattern before it is placed into the list. A caller chooses *which*
job; it never chooses a command, a path or a flag.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import config

#: The same patterns as the script on UBELIX. Kept in step by a test that reads
#: both, because two copies that drift are how a refusal turns into a hole.
SLURM_ID_RE = re.compile(r"^[0-9]{1,12}$")
RECORD_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-z0-9][a-z0-9._-]{0,120}$")
LABEL_RE = re.compile(r"^(_|[A-Za-z0-9][A-Za-z0-9._-]{0,120})$")
#: An ssh destination: ``host``, ``user@host`` or an alias from ~/.ssh/config.
#: Never anything ssh would read as an option.
HOST_RE = re.compile(r"^(?!-)[A-Za-z0-9._-]{1,64}(@[A-Za-z0-9._-]{1,253})?$")

MAX_LINES = 200
#: Well inside the broker's 60 s (``jobs.BROKER_TIMEOUT_S``): a login node that hangs must cost the caller
#: an error, not a timeout with nothing in it.
SSH_TIMEOUT_S = 30
#: What comes back is capped: squeue for a busy account, or a record whose log
#: lines are long, must not become a reply the broker chokes on.
MAX_OUTPUT_CHARS = 20_000

REFUSED = 2


class UbelixError(ValueError):
    """A request refused before anything was sent."""


def validate_slurm_id(slurm_id) -> str:
    text = str(slurm_id).strip()
    if not SLURM_ID_RE.match(text):
        raise UbelixError(f"not a Slurm job id: {slurm_id!r} (digits only)")
    return text


def validate_record(record_id: str) -> str:
    if not isinstance(record_id, str) or not RECORD_RE.match(record_id):
        raise UbelixError(
            f"not a trainer record id: {record_id!r} "
            "(expected e.g. 20261019T180118Z-olmocr2-7b-medieval-page-px4m-native-r16)")
    return record_id


def validate_label(label: str) -> str:
    if not isinstance(label, str) or not LABEL_RE.match(label):
        raise UbelixError(
            f"not a report label: {label!r} (e.g. german-medieval-v1-test, a job id, "
            "or _ for the run's own report)")
    return label


def _target() -> tuple[str, Path]:
    host = (config.ATR_UBELIX_SSH_HOST or "").strip()
    key = (config.ATR_UBELIX_SSH_KEY or "").strip()
    if not host or not key:
        raise UbelixError(
            "UBELIX access is not configured on this server: set "
            "ATR_UBELIX_SSH_HOST and ATR_UBELIX_SSH_KEY (deploy/ubelix-ro/README.md)")
    if not HOST_RE.match(host):
        raise UbelixError(f"ATR_UBELIX_SSH_HOST is not an ssh destination: {host!r}")
    return host, Path(key)


def ssh_argv(*request: str) -> list[str]:
    """The ssh command for one request. ``request`` must already be validated.

    ``BatchMode`` because there is nobody to type a passphrase; ``IdentitiesOnly``
    so ssh offers this key and not whatever an agent happens to hold — the forced
    command is bound to this key, and only to it.
    """
    host, key = _target()
    return ["ssh", "-T", "-i", str(key),
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=10",
            host, *request]


def _cap(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n… cut at {MAX_OUTPUT_CHARS} characters"


def _call(*request: str) -> dict:
    argv = ssh_argv(*request)
    try:
        done = subprocess.run(argv, capture_output=True, text=True,
                              timeout=SSH_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ssh to UBELIX timed out after {SSH_TIMEOUT_S} s"}
    except FileNotFoundError:
        return {"ok": False, "error": "ssh is not installed on this server"}
    result = {"ok": done.returncode == 0, "exit": done.returncode,
              "output": _cap(done.stdout)}
    if done.stderr.strip():
        result["stderr"] = _cap(done.stderr.strip())
    if done.returncode == REFUSED:
        result["error"] = "refused by atr-mcp-ro on UBELIX"
    elif done.returncode == 255:
        # ssh's own failure: unreachable, key refused, host key unknown.
        result["error"] = "ssh failed before reaching UBELIX (see stderr)"
    return result


def queue() -> dict:
    """This account's jobs in ``squeue``, with Slurm's start estimates."""
    try:
        return _call("queue")
    except UbelixError as exc:
        return {"ok": False, "error": str(exc)}


def job(slurm_id) -> dict:
    """``sacct -D`` for one job: every start, preemption and requeue."""
    try:
        return _call("job", validate_slurm_id(slurm_id))
    except UbelixError as exc:
        return {"ok": False, "error": str(exc)}


def record(record_id: str, lines: Optional[int] = None) -> dict:
    """The trainer's record and the tail of its current stage's log.

    The script answers in JSON; it is handed back parsed under ``record``, and as
    text only when it is not JSON (an error from ssh rather than from the script).
    """
    try:
        request = ["record", validate_record(record_id)]
        if lines is not None:
            request.append(str(max(1, min(int(lines), MAX_LINES))))
        result = _call(*request)
    except (UbelixError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        result["record"] = json.loads(result["output"])
        del result["output"]
    except ValueError:
        pass
    return result


def report(record_id: str, label: Optional[str] = None) -> dict:
    """A job's evaluation reports: which there are, or one of them.

    Parsed under ``reports`` (the list) or ``report`` and ``heldout`` (one), like
    :func:`record`; as text only when the answer is not JSON.
    """
    try:
        request = ["report", validate_record(record_id)]
        if label is not None:
            request.append(validate_label(label))
        result = _call(*request)
    except UbelixError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        parsed = json.loads(result["output"])
        del result["output"]
        result.update({k: v for k, v in parsed.items() if k != "id"})
    except (ValueError, AttributeError):
        pass
    return result
