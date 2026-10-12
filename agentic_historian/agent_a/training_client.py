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

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

import httpx

import config


# ── enums ────────────────────────────────────────────────────────────────────

class JobStatus(str, Enum):
    """The trainer's nine statuses, plus one for a value this build does not know.

    The authority is the trainer's own ``JobStatus`` literal in
    ``src/atr_training/contracts.py`` (training-atr-models), not this file. It
    reads::

        "queued", "preparing", "compiling", "training", "testing",
        "registering", "completed", "failed", "cancelled"

    This enum said ``DONE = "done"``, a value the trainer never sends. A
    finished job therefore hit the unknown-status branch and came back as
    ``QUEUED`` — "has not started yet" for a run that was over. Nothing noticed
    because nothing read a job record until the watcher did (#363), which cannot
    survive it: a run whose terminal state never arrives is polled for ever and
    never reported. The same class of mistake as the request body #362 found,
    and the same fix: read the contract rather than guess it.
    """

    QUEUED      = "queued"
    PREPARING   = "preparing"
    COMPILING   = "compiling"
    TRAINING    = "training"
    TESTING     = "testing"
    REGISTERING = "registering"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"
    #: Not the trainer's. A status this build has never heard of — a newer
    #: trainer's — kept distinct from every real one. See ``from_dict``.
    UNKNOWN     = "unknown"


#: Finished moving. Mirrors the trainer's ``TERMINAL_STATUSES`` frozenset.
#: ``UNKNOWN`` is deliberately absent: a status we cannot read is not evidence
#: that a run is over, and treating it as terminal would stop the watcher on the
#: one record it understands least.
TERMINAL = (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)


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

    The shape is the trainer's ``TrainJob`` model (``contracts.py``), which the
    gateway forwards unchanged. Three of its fields are not where this class
    first assumed they were, and the watcher (#363) reads all three:

    * ``stages`` is a **list** of stage records, not a mapping. ``.items()`` on
      it raises, so any job that had started a stage was unparseable — hidden
      only by the fact that nothing read a job record until now.
    * ``log_tail`` is a **list of lines**, joined here into the text a reader
      wants.
    * ``model_id`` and ``engine`` live inside the submitted ``request``, and
      ``cer``/``wer`` inside ``metrics``. The flat keys are read as a fallback,
      because the recorded payloads in the tests have them there.

    Fields that may not be present in older recordings are typed as Optional
    so that a partial record from a live watcher reattach is still loadable.
    """

    id:             str
    status:         JobStatus
    preset:         str
    model_id:       str
    #: The status exactly as the trainer wrote it. Equal to ``status.value`` for
    #: the nine known ones; for anything else ``status`` is ``UNKNOWN`` and this
    #: is the only place the real word survives, so a message can print it.
    status_raw:     str                = ""
    #: Which of the five stages the job is in, from the record's own ``stage``
    #: field. None while queued, and on a terminal job.
    stage:          Optional[str]      = None
    engine:         str                = ""
    created_at:     Optional[datetime] = None
    started_at:     Optional[datetime] = None
    finished_at:    Optional[datetime] = None
    queued_reason:  Optional[str]      = None
    error:          Optional[str]      = None
    cer:            Optional[float]    = None     # test set CER on completion
    wer:            Optional[float]    = None
    log_tail:       Optional[str]      = None     # last lines of the job log
    #: stage → that stage's status ("pending" | "running" | "completed" |
    #: "failed" | "cancelled"). The *status*, not the log: a stage that ran is
    #: what a progress board and a reattach after a restart both need, and the
    #: record's ``log`` field is a path on the training box, which is of no use
    #: from here.
    stages:         dict[Stage, str]   = field(default_factory=dict)
    #: ``epoch``, ``epochs``, ``total_steps``, … — passed through as given.
    progress:       dict[str, Any]     = field(default_factory=dict)
    #: ``cer``, ``wer``, ``samples``, ``benchmark_cer``, … — as given.
    metrics:        dict[str, Any]     = field(default_factory=dict)
    #: Filled by the register stage.
    model_path:     Optional[str]      = None
    #: What auto-publish did, in words — including why it did nothing.
    published:      Optional[str]      = None

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

        raw_status = str(d.get("status") or "queued").lower()
        try:
            status = JobStatus(raw_status)
        except ValueError:
            # Not QUEUED. A status this build cannot read is unknown, and
            # "unknown" and "has not started yet" are different facts: the first
            # draft returned the second for both, so the trainer's "completed"
            # — which this enum did not have — read as a run still waiting for
            # a card. UNKNOWN is neither terminal nor progress, so the watcher
            # keeps polling and says the word the trainer used.
            status = JobStatus.UNKNOWN

        request = d.get("request") or {}
        if not isinstance(request, dict):
            request = {}
        metrics = d.get("metrics") or {}
        if not isinstance(metrics, dict):
            metrics = {}
        progress = d.get("progress") or {}
        if not isinstance(progress, dict):
            progress = {}

        return cls(
            id              = d.get("id", ""),
            status          = status,
            status_raw      = raw_status,
            stage           = d.get("stage") or None,
            # The trainer's record has no ``preset``: a preset is the bot's own
            # naming for a request body (#362), so the gateway cannot return
            # one. It stays readable here for the 202 of a submit and for the
            # recorded payloads; the watcher takes the preset from its registry,
            # which is where that fact actually lives.
            preset          = d.get("preset", ""),
            model_id        = request.get("model_id") or d.get("model_id", ""),
            engine          = request.get("engine") or d.get("engine", ""),
            created_at      = _dt(d.get("created_at")),
            started_at      = _dt(d.get("started_at")),
            finished_at     = _dt(d.get("finished_at")),
            queued_reason   = d.get("queued_reason"),
            error           = d.get("error"),
            cer             = _float_or_none(_first(metrics.get("cer"), d.get("cer"))),
            wer             = _float_or_none(_first(metrics.get("wer"), d.get("wer"))),
            log_tail        = _text(d.get("log_tail")),
            stages          = _stages(d.get("stages")),
            progress        = progress,
            metrics         = metrics,
            model_path      = d.get("model_path"),
            published       = d.get("published"),
        )

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def is_active(self) -> bool:
        """True when the watcher should keep polling this job.

        An ``UNKNOWN`` status counts as active, which is the safe direction: a
        word this build does not know is not a finished run, and dropping it
        would stop watching the one job we understand least.
        """
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
            JobStatus.COMPLETED:   "completed",
            JobStatus.FAILED:      f"failed: {self.error}" if self.error else "failed",
            JobStatus.CANCELLED:   "cancelled",
        }
        # The trainer's own word for an unknown status, not "unknown": the
        # reader can look that up, and we cannot.
        return desc.get(self.status, self.status_raw or str(self.status.value))


def _float_or_none(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _first(*values: Any) -> Any:
    """The first value that is not None. ``0`` and ``0.0`` are values.

    A CER of 0.0 is a measurement, not a missing one, so this may not fall
    through on falsiness — the project's recurring mistake, in the one field
    where a zero is the best possible result.
    """
    for value in values:
        if value is not None:
            return value
    return None


def _text(v: Any) -> Optional[str]:
    """``log_tail`` as text. The record holds a list of lines; older ones a str."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, (list, tuple)):
        return "\n".join(str(line) for line in v)
    return str(v)


def _stages(raw: Any) -> dict[Stage, str]:
    """stage → its status, from the record's ``stages`` **list**.

    A mapping is also accepted, because the payloads recorded before the
    contract was read have one; its values are taken as given. Anything whose
    stage name this build does not know is skipped rather than guessed.
    """
    out: dict[Stage, str] = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, (list, tuple)):
        items = [(e.get("name"), e.get("status") or "pending")
                 for e in raw if isinstance(e, dict)]
    else:
        return out
    for name, value in items:
        try:
            out[Stage(str(name).lower())] = str(value or "")
        except ValueError:
            continue
    return out


# ── client ────────────────────────────────────────────────────────────────────

class TrainingClient:
    """
    Thin HTTP client for the trainer gateway's /train/* routes.

    Shares the same auth (X-API-Key), base URL (ATR_GATEWAY_URL), and
    timeout (ATR_HTTP_TIMEOUT) as KrakenHTTPClient — one door for both. Behind
    it they are two machines since 16.09.2026: recognition is served on idhefix,
    and the gateway there proxies /train/* to the trainer on asteraix. From this
    side nothing changes, which is the point of going through the gateway.

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

    def submit(self, request: dict) -> TrainJob:
        """
        POST /train/jobs — start a run from a **complete** request body.

        The shape #361 specified, and the only one the trainer accepts: its
        ``TrainRequest`` has no ``preset`` field. It requires ``engine``,
        ``model_id`` and at least one ``datasets`` entry, and defaults the rest —
        including ``params.spec``, the VGSL architecture, which therefore stays
        in the repository that trains with it.

        Resolving a preset name into this body is the *bot's* job
        (``training_presets.resolve``), because the body is also what the
        confirmation card has to show: what somebody approves must be what is
        sent (#362).

        Raises TrainingClientError on network or HTTP errors, with the trainer's
        own ``detail`` attached — a 507 names the full filesystem and a 409 says
        the job is already terminal, and those are actionable in a way that
        "training failed" is not.
        """
        resp = self._post("/train/jobs", json=dict(request))
        return TrainJob.from_dict(resp.json())

    def create_job(
        self,
        preset:   str,
        model_id: str,
        *,
        extra: Optional[dict] = None,
    ) -> TrainJob:
        """
        Start a run from a **preset name** — resolved here, not by the trainer.

        This used to send ``{"preset": …, "model_id": …}`` and its docstring said
        the trainer resolved it. The trainer does not: ``TrainRequest`` has no
        ``preset`` field and requires ``datasets``, so that body was refused at
        validation. Nothing in production called it, which is why the contract
        could stay wrong this long (#362 found it).

        ``extra`` is merged last, so a caller can override one field of a preset
        without a new preset — ``{"params": {"epochs": 100}}`` replaces the
        params block, it does not merge into it.
        """
        import training_presets

        body: dict[str, Any] = training_presets.resolve(preset, model_id)
        if extra:
            body.update(extra)
        return self.submit(body)

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

    def get_log(self, job_id: str, stage: Optional[Stage] = None,
                lines: Optional[int] = None) -> str:
        """
        GET /train/jobs/{job_id}/log?stage=<stage> — fetch the log for a stage.

        Parameters
        ----------
        job_id : job whose log to fetch.
        stage  : specific stage to fetch (prepare | compile | train | test |
                 register). When None the route's own default (``train``) applies.
        lines  : how many lines from the end; the trainer allows 1–5000 and
                 defaults to 200. Omitted means "the trainer's default".

        Returns the log text (multiline string).

        The trainer answers ``{"job_id", "stage", "lines": [...]}`` — a **list**
        of lines, under ``lines``. This read ``log``, a key the route does not
        have, so it returned ``""`` for every log that exists: "the stage has no
        output yet" for a run that was printing one. `atr_status.log` had the
        right key all along, which is why nobody noticed (#364).

        A stage with no log on disk is a **404**, not an empty list, so it
        arrives as ``TrainingJobNotFound`` — and so does an unknown job id. The
        two need different sentences, and only the job record can tell them
        apart; `train_ops` does that rather than reading the detail text.
        """
        params: dict[str, Any] = {}
        if stage:
            params["stage"] = stage.value
        if lines is not None:
            params["lines"] = lines
        resp = self._get(f"/train/jobs/{job_id}/log", params=params)
        return _text(resp.json().get("lines", resp.json().get("log"))) or ""

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
            # 404 gets its own class. "The trainer does not know this job" and
            # "the trainer did not answer" need opposite handling in the watcher:
            # the first means stop watching an id that will never resolve, the
            # second means keep the run and try again. Collapsed into one
            # exception, the watcher would have to guess — the same distinction
            # serving-atr-inference#149 drew between a busy engine and an
            # unreachable one.
            if resp.status_code == 404:
                raise TrainingJobNotFound(
                    f"Trainer gateway 404 at {self.base_url}{path}: {body}",
                    status_code=404)
            raise TrainingClientError(
                f"Trainer gateway {resp.status_code} at {self.base_url}{path}: {body}",
                status_code=resp.status_code,
            )
        return resp


# ── exceptions ───────────────────────────────────────────────────────────────

class TrainingClientError(Exception):
    """Raised when the trainer HTTP service returns an error or is unreachable.

    ``status_code`` is the HTTP status when there was one and **None** when the
    request never got an answer. Callers that need to tell a refusal from an
    outage read it instead of matching the message text, which is this module's
    own wording and not a contract (#364).
    """

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TrainingJobNotFound(TrainingClientError):
    """The gateway answered, and the trainer has no such job (404).

    A subclass, so every ``except TrainingClientError`` still catches it and
    only the callers that care about the difference have to say so.
    """
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


def submit_training_job(
    request:  dict,
    base_url: Optional[str] = None,
) -> TrainJob:
    """One-shot ``TrainingClient`` for a complete request body.

    Beside ``create_training_job``, which resolves a preset name into one. This
    is the seam the Discord command uses, so the blocking call it runs in a
    thread is a plain function rather than a context manager opened by hand —
    a client entered without being exited leaks its connection pool.
    """
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.submit(request)


def get_training_job(
    job_id:  str,
    base_url: Optional[str] = None,
) -> TrainJob:
    """One-shot get a single training job by id."""
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.get_job(job_id)


def cancel_training_job(
    job_id:   str,
    base_url: Optional[str] = None,
) -> TrainJob:
    """One-shot ``TrainingClient`` for a cancel.

    Beside ``submit_training_job`` and for the same reason: the Discord command
    runs this in a thread, so it has to be a plain function rather than a
    context manager opened by hand — a client entered without being exited leaks
    its connection pool (#362).
    """
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.cancel_job(job_id)


def list_training_jobs(
    limit:   int = 20,
    status:  Optional[JobStatus] = None,
    base_url: Optional[str] = None,
) -> list[TrainJob]:
    """One-shot list recent training jobs."""
    url = base_url or config.ATR_GATEWAY_URL
    with TrainingClient(base_url=url) as client:
        return client.list_jobs(limit=limit, status=status)
