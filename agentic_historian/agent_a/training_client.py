"""
Training client — typed HTTP wrapper for the ATR trainer gateway /train/* routes.

Mirrors the KrakenHTTPClient pattern: same context-manager lifecycle, same
X-API-Key auth header, same error-surfacing behaviour. This is T1 of the
training epic (#360).

Endpoint contract (from serving-atr-inference):
    POST /train/jobs
        Body (JSON): {"preset": <str>, "model_id": <str>, ...}
        Returns    : TrainJob

    GET /train/jobs/{job_id}
        Returns    : TrainJob

    GET /train/jobs
        Query      : ?limit=<int>, ?status=<str>
        Returns    : {"jobs": [TrainJob, ...]}

    POST /train/jobs/{job_id}/cancel
        Returns    : TrainJob

    GET /train/jobs/{job_id}/log?stage=<stage>
        Query      : stage = "prepare" | "compile" | "train" | "test" | "register"
        Returns    : {"log": "<multiline string>"}

Auth: same X-API-Key as KrakenHTTPClient (ATR_API_KEY in config).

Run: pytest agentic_historian/tests/test_ah_361_training_client.py -v
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

import config


# ── enums ────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    """Terminal and transitional job states returned by the trainer."""

    QUEUED      = "queued"
    PREPARING   = "preparing"
    COMPILING   = "compiling"
    TRAINING    = "training"
    TESTING     = "testing"
    REGISTERING = "registering"
    DONE        = "done"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


class Stage(str, Enum):
    """Named pipeline stages for /log and progress reporting."""

    PREPARE   = "prepare"
    COMPILE   = "compile"
    TRAIN     = "train"
    TEST      = "test"
    REGISTER  = "register"


# ── dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class TrainJob:
    """
    A training job record returned by every /train/* endpoint.

    Fields that may not be present in older recordings are typed as Optional
    so that a partial record from a live watcher reattach is still loadable.
    """

    id:             str
    status:         JobStatus
    preset:         str
    model_id:       str
    created_at:     Optional[datetime] = None
    started_at:     Optional[datetime] = None
    finished_at:    Optional[datetime] = None
    queued_reason:  Optional[str]      = None
    error:          Optional[str]      = None
    cer:            Optional[float]    = None     # test set CER on completion
    wer:            Optional[float]    = None
    log_tail:       Optional[str]      = None     # last lines of the job log
    stages:         dict[Stage, str]   = field(default_factory=dict)  # stage → log

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TrainJob":
        """Parse a raw gateway JSON dict into a TrainJob."""
        def _dt(v: Any) -> Optional[datetime]:
            if not v:
                return None
            if isinstance(v, datetime):
                return v
            try:
                return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
            except ValueError:
                return None

        raw_status = (d.get("status") or "queued").lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            status = JobStatus.QUEUED

        stages_raw = d.get("stages", {}) or {}
        stages = {}
        for stage_name, log in stages_raw.items():
            try:
                stages[Stage(stage_name.lower())] = log or ""
            except ValueError:
                pass

        return cls(
            id              = d.get("id", ""),
            status          = status,
            preset          = d.get("preset", ""),
            model_id        = d.get("model_id", ""),
            created_at      = _dt(d.get("created_at")),
            started_at      = _dt(d.get("started_at")),
            finished_at     = _dt(d.get("finished_at")),
            queued_reason   = d.get("queued_reason"),
            error           = d.get("error"),
            cer             = _float_or_none(d.get("cer")),
            wer             = _float_or_none(d.get("wer")),
            log_tail        = d.get("log_tail"),
            stages          = stages,
        )

    def is_terminal(self) -> bool:
        return self.status in (JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED)

    def is_active(self) -> bool:
        """True when the watcher should keep polling this job."""
        return not self.is_terminal()

    def stage_description(self) -> str:
        """Human-readable stage name for progress reporting."""
        desc = {
            JobStatus.QUEUED:      "queued",
            JobStatus.PREPARING:   "preparing dataset",
            JobStatus.COMPILING:   "compiling model",
            JobStatus.TRAINING:    "training",
            JobStatus.TESTING:     "running test set",
            JobStatus.REGISTERING: "registering model",
            JobStatus.DONE:        "done",
            JobStatus.FAILED:      f"failed: {self.error}" if self.error else "failed",
            JobStatus.CANCELLED:   "cancelled",
        }
        return desc.get(self.status, str(self.status))


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── client ────────────────────────────────────────────────────────────────────

class TrainingClient:
    """
    Thin HTTP client for the trainer gateway's /train/* routes.

    Shares the same auth (X-API-Key), base URL (ATR_GATEWAY_URL), and
    timeout (ATR_HTTP_TIMEOUT) as KrakenHTTPClient — both services live on
    the same asterAIx box behind the same gateway.

    Usage::

        with TrainingClient() as client:
            job = client.create_job(preset="catmus_medieval", model_id="my-new-model")
            status = client.get_job(job.id)
            jobs = client.list_jobs(limit=10)
            client.cancel_job(job.id)
            log = client.get_log(job.id, stage=Stage.TRAIN)
    """

    def __init__(
        self,
        base_url:  Optional[str] = None,
        timeout:   Optional[float] = None,
        api_key:   Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or config.ATR_GATEWAY_URL or "").rstrip("/")
        self.timeout  = config.ATR_HTTP_TIMEOUT if timeout is None else timeout
        self.api_key  = config.ATR_API_KEY if api_key is None else api_key
        self._client: Optional[httpx.Client] = None

    # ── context manager ─────────────────────────────────────────────────────

    def __enter__(self) -> "TrainingClient":
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        self._client = httpx.Client(
            base_url = self.base_url,
            timeout  = self.timeout,
            follow_redirects = True,
            headers  = headers,
        )
        return self

    def __exit__(self, *args) -> None:
        assert self._client is not None
        self._client.close()
        self._client = None

    # ── public API ─────────────────────────────────────────────────────────

    def create_job(
        self,
        preset:   str,
        model_id: str,
        *,
        extra: Optional[dict] = None,
    ) -> TrainJob:
        """
        POST /train/jobs — start a new training run.

        Parameters
        ----------
        preset   : name of a named preset registered in the trainer (e.g.
                   "catmus_medieval"). The trainer resolves this to a complete
                   VGSL spec, dataset, and hyper-parameters — the bot never
                   sends raw hyper-parameters over Discord.
        model_id : target model id for the finished model (e.g.
                   "10.5281/zenodo.XXXXXXX"). The trainer registers the output
                   under this id when the run succeeds.
        extra    : optional dict of additional fields to merge into the request
                   body (e.g. {"epochs": 100} for a preset that accepts an
                   override). Most calls should omit this.

        Returns a TrainJob with status QUEUED (or an error from the trainer).
        Raises TrainingClientError on network or HTTP errors.
        """
        body: dict[str, Any] = {"preset": preset, "model_id": model_id}
        if extra:
            body.update(extra)
        resp = self._post("/train/jobs", json=body)
        return TrainJob.from_dict(resp.json())

    def get_job(self, job_id: str) -> TrainJob:
        """
        GET /train/jobs/{job_id} — return the current state of one job.

        Returns a TrainJob. Raises TrainingClientError if the job does not
        exist or the gateway is unreachable.
        """
        resp = self._get(f"/train/jobs/{job_id}")
        return TrainJob.from_dict(resp.json())

    def list_jobs(
        self,
        limit:  int = 20,
        status: Optional[JobStatus] = None,
    ) -> list[TrainJob]:
        """
        GET /train/jobs — return recent jobs, newest first.

        Parameters
        ----------
        limit  : maximum number of jobs to return (default 20, max 100).
        status : optional filter by JobStatus (e.g. JobStatus.TRAINING to get
                 only active runs). When None all jobs are returned.

        Returns a list of TrainJob (empty if the payload is malformed).
        """
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if status:
            params["status"] = status.value
        resp = self._get("/train/jobs", params=params)
        raw_jobs = resp.json().get("jobs", [])
        if not isinstance(raw_jobs, list):
            return []
        return [TrainJob.from_dict(j) for j in raw_jobs]

    def cancel_job(self, job_id: str) -> TrainJob:
        """
        POST /train/jobs/{job_id}/cancel — stop a running or queued job.

        Returns the updated TrainJob (typically status CANCELLED or FAILED
        depending on how far the job got before the cancel was processed).
        Raises TrainingClientError if the job does not exist.
        """
        resp = self._post(f"/train/jobs/{job_id}/cancel", json={})
        return TrainJob.from_dict(resp.json())

    def get_log(self, job_id: str, stage: Optional[Stage] = None) -> str:
        """
        GET /train/jobs/{job_id}/log?stage=<stage> — fetch the log for a stage.

        Parameters
        ----------
        job_id : job whose log to fetch.
        stage  : specific stage to fetch (prepare | compile | train | test |
                 register). When None returns the job-level log_tail.

        Returns the log text (multiline string). Returns "" when the gateway
        has no log for the requested stage yet.
        Raises TrainingClientError if the job does not exist.
        """
        params: dict[str, Any] = {}
        if stage:
            params["stage"] = stage.value
        resp = self._get(f"/train/jobs/{job_id}/log", params=params)
        return resp.json().get("log", "")

    # ── internals ───────────────────────────────────────────────────────────

    def _get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        assert self._client is not None
        try:
            resp = self._client.get(path, params=params)
        except httpx.RequestError as exc:
            raise TrainingClientError(
                f"Trainer gateway unreachable at {self.base_url}{path}: {exc}"
            ) from exc
        return self._check(resp, path)

    def _post(
        self,
        path: str,
        json: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        assert self._client is not None
        try:
            resp = self._client.post(path, json=json)
        except httpx.RequestError as exc:
            raise TrainingClientError(
                f"Trainer gateway error at {self.base_url}{path}: {exc}"
            ) from exc
        return self._check(resp, path)

    def _check(self, resp: httpx.Response, path: str) -> httpx.Response:
        """Surface 4xx/5xx as TrainingClientError with the response body."""
        if resp.status_code >= 400:
            body = resp.text[:500]
            raise TrainingClientError(
                f"Trainer gateway {resp.status_code} at {self.base_url}{path}: {body}"
            )
        return resp


# ── exceptions ───────────────────────────────────────────────────────────────

class TrainingClientError(Exception):
    """Raised when the trainer HTTP service returns an error or is unreachable."""
    pass


# ── convenience helpers ──────────────────────────────────────────────────────

def create_training_job(
    preset:   str,
    model_id: str,
    *,
    extra:    Optional[dict] = None,
    base_url: Optional[str] = None,
) -> TrainJob:
    """
    One-shot ``TrainingClient`` context-manager helper for creating a job.

    Usage::

        job = create_training_job("catmus_medieval", "10.5281/zenodo.XXXXXXX")
    """
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.create_job(preset=preset, model_id=model_id, extra=extra)


def get_training_job(
    job_id:  str,
    base_url: Optional[str] = None,
) -> TrainJob:
    """One-shot get a single training job by id."""
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.get_job(job_id)


def list_training_jobs(
    limit:   int = 20,
    status:  Optional[JobStatus] = None,
    base_url: Optional[str] = None,
) -> list[TrainJob]:
    """One-shot list recent training jobs."""
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.list_jobs(limit=limit, status=status)
