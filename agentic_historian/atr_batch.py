"""
atr_batch.py — run several recognition models over the same corpus, side by side.

The pipeline's existing entry points process **one document well**: Agent A reads
it, Agent B describes it, the ensemble picks a winner, a gate asks a historian.
That is the right shape for a document and the wrong shape for the question "how
do these three models read this collection", which needs the opposite: no
selection, no fusion, no gate — every model's reading of every page, kept apart
and kept whole, so they can be compared afterwards.

Four things make it a batch runner rather than a loop.

**Model-major iteration.** For each model, every page; then the next model. Never
page-major. The gateway's vLLM models are `residency: lazy` on one GPU that holds
one at a time, so asking for three models per page pays an evict-and-reload cycle
*per page* — minutes of weight loading against seconds of recognition. Over a few
hundred pages that ordering is the difference between an afternoon and a week,
and it buys nothing.

**Resumable.** A page whose result is already on disk is skipped. Every result is
written through a temp file and renamed, so an interrupted run leaves completed
pages and no half-written ones; there is no state to reconcile because the output
*is* the state. A multi-hour run that cannot be resumed will be restarted from
zero at least once.

**Failures classified, not just caught.** A page that times out is retried, then
recorded and stepped over. A model the gateway does not know (404) aborts *that
model immediately* — every remaining page would fail identically, and five
hundred identical failures is not more information than one. A model whose first
few pages all fail aborts too: something is wrong with the model or the gateway,
and the run should say so while the rest of the models still have time.

**Honest about what it is not.** It reports what each model produced and what it
cost. It does not rank them. There is no ground truth here, and length, line
count and confidence are not quality — a model that hallucinates fluently scores
well on all three. Ranking needs `eval/linebench.py` and transcribed lines.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol, Sequence

from loguru import logger

import config

__all__ = [
    "IMAGE_EXTS",
    "SCHEMA",
    "PageRef",
    "PageSource",
    "PageOutcome",
    "ModelOutcome",
    "BatchReport",
    "discover_pages",
    "pages_from_paths",
    "result_paths",
    "is_complete",
    "classify_failure",
    "gateway_recogniser",
    "run_model",
    "run_batch",
    "report_from_outputs",
    "format_report",
]

#: Page images a batch reads. PDFs are excluded on purpose — the recognisers take
#: one image per call, and silently handing them a PDF produces a failure per page
#: rather than a conversion.
IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})

#: Version tag on every result file. A consumer that finds a shape it does not
#: know should say so rather than guess, and a resumed run must not treat a file
#: written by an older, differently-shaped version as complete.
SCHEMA = "atr-batch/1"

#: Consecutive page failures after which a model is abandoned. Low on purpose:
#: past this point the evidence is about the model or the gateway, not the pages,
#: and continuing spends the rest of the run's time proving it again.
MAX_CONSECUTIVE_FAILURES = 5

#: Statuses that will never succeed on a retry, whatever the page. 404 is an
#: unknown model id, 401/403 a rejected key — both are true of every call.
FATAL_STATUSES = frozenset({401, 403, 404})

#: The gateway says this when a training run holds the GPU: nothing is broken,
#: the box is busy, and the same request works later (serving-atr-inference#129,
#: which sends it with a Retry-After). Ending the model rather than retrying is
#: the honest reading of that — a training run lasts hours, and the alternative
#: is fifteen requests spent discovering what the first reply already said.
#:
#: Not in FATAL_STATUSES, because the reason and the remedy are different: a 404
#: means the model will never work here, a 503 means not now. The report has to
#: say which, or somebody re-runs a batch that could not have succeeded — or
#: worse, does not re-run one that would.
BUSY_STATUS = 503


# ── the corpus ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageRef:
    """One page image, with the identity its outputs are keyed by."""

    path: Path
    #: Folder the page sits in, relative to the corpus root ("" when flat). This
    #: is the document a page belongs to when a share is one folder per letter.
    doc_id: str
    #: Stable output name: the path relative to the root, without its extension,
    #: with separators folded to "__". Flat, so one directory per model holds the
    #: whole corpus, and reversible enough to see where a page came from.
    key: str

    @property
    def name(self) -> str:
        return self.path.name


def _key_for(rel: Path) -> str:
    return "__".join(rel.with_suffix("").parts)


#: Seed for ``--sample``. A constant rather than the clock, because the whole
#: runner treats what is on disk as its state: a sample that changed between runs
#: would mean a resumed run reading pages the first one never saw, and a second
#: model reading a different corpus than the first. Reproducible across machines
#: for the same reason — two people comparing notes need the same ten pages.
SAMPLE_SEED = 20260915


def discover_pages(root: Path, exts: Iterable[str] = IMAGE_EXTS,
                   limit: Optional[int] = None, sample: Optional[int] = None,
                   seed: int = SAMPLE_SEED) -> list[PageRef]:
    """Every page under ``root``, in a stable order.

    Sorted by relative path, so two runs over the same corpus process it in the
    same sequence — which is what lets a resumed run's progress be compared with
    the first one's, and what makes "it failed on page 40" reproducible.

    ``limit`` takes the first N; ``sample`` takes N at random and then sorts them
    back into corpus order. The difference matters for what a short run can tell
    you: a share that is one folder per document gives ``limit 10`` ten
    consecutive pages of one letter — the same hand, the same ink, often the same
    scanner setting — while ``sample 10`` crosses documents. For a smoke test
    that proves the path, the first is enough; for any impression of how a model
    reads *this collection*, it is misleading.

    They are mutually exclusive, because a request for both has no obvious
    reading and guessing one would be worse than asking.
    """
    if limit is not None and sample is not None:
        raise ValueError("limit and sample are alternatives: first N, or N at random")
    root = Path(root)
    exts = {e.lower() for e in exts}
    rels = sorted(
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in exts
    )
    if sample is not None:
        if sample < 0:
            raise ValueError(f"sample must not be negative: {sample}")
        # Sorted back afterwards, so the processing order stays corpus order and
        # a resumed run walks the same sequence it did the first time.
        rels = sorted(random.Random(seed).sample(rels, min(sample, len(rels))))
    pages = [
        PageRef(path=root / rel, doc_id=rel.parent.as_posix().strip("."), key=_key_for(rel))
        for rel in rels
    ]
    return pages[:limit] if limit is not None else pages


def pages_from_paths(paths: Sequence[str], root: str = "",
                     limit: Optional[int] = None, sample: Optional[int] = None,
                     seed: int = SAMPLE_SEED) -> list[PageRef]:
    """The same corpus, built from remote paths instead of a directory walk.

    ``discover_pages`` and this differ only in where the list of paths comes
    from — ``rglob`` there, a WebDAV listing here. Everything downstream is
    identical, deliberately: the key is the path relative to the root with
    separators folded, so a page has the **same key** whether it was read from a
    mirror, a mount or the share itself, and a corpus half-read one way can be
    finished the other.
    """
    root = (root or "").strip("/")

    def _strip(p: str) -> str:
        # Case-insensitive, for the reason utils.nextcloud._relative gives: the
        # share answers to two spellings of the same folder, and which one the
        # caller typed must not decide a page's key. The separator check matters
        # too — `Digitalisate2` starts with `Digitalisate` and is not inside it.
        if root and p[:len(root)].lower() == root.lower() \
                and (len(p) == len(root) or p[len(root)] == "/"):
            return p[len(root):].strip("/")
        return p

    rels = sorted(Path(_strip(p)) for p in paths)
    if sample is not None:
        if sample < 0:
            raise ValueError(f"sample must not be negative: {sample}")
        rels = sorted(random.Random(seed).sample(rels, min(sample, len(rels))))
    if limit is not None and sample is not None:
        raise ValueError("limit and sample are alternatives: first N, or N at random")
    pages = [
        PageRef(path=Path(f"{root}/{rel}" if root else str(rel)),
                doc_id=rel.parent.as_posix().strip("."), key=_key_for(rel))
        for rel in rels
    ]
    return pages[:limit] if limit is not None else pages


# ── results on disk ──────────────────────────────────────────────────────────

def result_paths(out_dir: Path, key: str) -> tuple[Path, Path]:
    """``(text file, json file)`` for one page's reading by one model."""
    return out_dir / f"{key}.txt", out_dir / f"{key}.json"


def is_complete(json_path: Path) -> bool:
    """True when this page has already been read by this model, into this shape.

    Deliberately parses the file rather than trusting its existence: the whole
    point of resuming is that the previous run was interrupted, and a file that
    cannot be parsed is exactly what an interruption leaves behind. A result
    written by a different schema version is *not* complete — re-reading the page
    is cheaper than a corpus mixing two shapes.
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("schema") == SCHEMA and isinstance(data.get("text"), str)


def _write_atomic(path: Path, payload: str) -> None:
    """Write through a temp file in the same directory, then rename.

    ``os.replace`` is atomic within a filesystem, so a reader — including the next
    run's ``is_complete`` — sees either the old file or the whole new one, never a
    truncated one. Without this, a run killed mid-write leaves a file that exists,
    parses as far as it goes, and is skipped for ever.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── outcomes ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PageOutcome:
    """What happened to one page under one model."""

    key: str
    model: str
    status: str                    # "done" | "skipped" | "failed"
    chars: int = 0
    lines: int = 0
    timing_ms: int = 0
    error: str = ""
    #: The reading stopped at the model's token ceiling, not at the end of the
    #: page. Not a failure — the text is real, there is just less of it than the
    #: page has — so it is counted separately rather than folded into either.
    truncated: bool = False
    #: Set when the failure is one that ends the model rather than the page.
    fatal: bool = False
    #: Why, in the words the report should use. "Abandoned" alone leaves the
    #: reader to guess whether to re-run, and a busy GPU and an unknown model id
    #: want opposite actions.
    reason: str = ""
    #: True when this failure was a source/IO problem (share unavailable,
    #: ParseError), not a model or gateway problem.  Source errors go through
    #: the source-error circuit (pause + retry), not the model circuit.
    source_error: bool = False

    @property
    def ok(self) -> bool:
        return self.status in ("done", "skipped")


@dataclass
class ModelOutcome:
    """What happened to one model over the whole corpus."""

    model: str
    done: int = 0
    skipped: int = 0
    failed: int = 0
    chars: int = 0
    lines: int = 0
    truncated: int = 0
    #: Pages the model read without producing a single character. A success by
    #: every other measure — 200, no error, a file on disk — and empty. Counted
    #: and named, because a corpus is silently short by however many of these it
    #: contains and no other column moves when one happens.
    empty: int = 0
    empty_keys: list[str] = field(default_factory=list)
    recognition_ms: int = 0
    elapsed_s: float = 0.0
    #: Why the model was abandoned before the end of the corpus, if it was.
    aborted: str = ""
    errors: list[str] = field(default_factory=list)
    #: How many of the failed pages were source/IO failures (share down, ParseError).
    source_errors: int = 0
    #: Keys of pages that failed due to source problems (for the second-pass retry).
    source_error_keys: list[str] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.done + self.skipped + self.failed

    @property
    def mean_chars(self) -> float:
        return self.chars / self.done if self.done else 0.0

    @property
    def mean_ms(self) -> float:
        return self.recognition_ms / self.done if self.done else 0.0


@dataclass
class BatchReport:
    run: str
    out_root: Path
    pages: int
    models: list[ModelOutcome] = field(default_factory=list)
    started_at: str = ""
    elapsed_s: float = 0.0
    #: True when the report was reconstructed from the files on disk rather than
    #: observed as the run happened. What is on disk cannot show a page that
    #: failed and wrote nothing, so a rebuilt report says so where the table is.
    rebuilt: bool = False

    @property
    def any_aborted(self) -> bool:
        return any(m.aborted for m in self.models)

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA,
            "run": self.run,
            "pages": self.pages,
            "started_at": self.started_at,
            "elapsed_s": round(self.elapsed_s, 1),
            "rebuilt": self.rebuilt,
            "models": [
                {
                    "model": m.model, "done": m.done, "skipped": m.skipped,
                    "failed": m.failed, "chars": m.chars, "lines": m.lines,
                    "truncated": m.truncated, "empty": m.empty,
                    "empty_keys": list(m.empty_keys),
                    "recognition_ms": m.recognition_ms,
                    "elapsed_s": round(m.elapsed_s, 1),
                    "aborted": m.aborted,
                    "source_errors": m.source_errors,
                    "errors": m.errors[:20],
                }
                for m in self.models
            ],
        }


# ── failure classification ───────────────────────────────────────────────────

def classify_failure(exc: BaseException) -> tuple[bool, bool]:
    """``(retryable, fatal_for_model)`` for one recognition failure.

    Three kinds, and treating them alike is what makes a batch runner waste a
    night:

    * **no answer / 5xx** — the gateway is loading a model, restarting, or the
      connection dropped. Retryable; not fatal.
    * **503** — the GPU is claimed by a training run. Not retryable and not the
      next page's problem either: the claim lasts as long as the run does, so it
      ends the model. Measured on 2026-09-15, this cost fifteen requests across
      five pages before the runner gave up on a reply that said so the first time.
    * **401 / 403 / 404** — a rejected key or a model id the gateway does not
      have. Neither a retry nor the next page changes it, so it ends the model.
    * **anything else (4xx)** — the request was wrong for *this* page (an image
      the engine rejected, a 422). The page is lost, the model goes on.

    503 and 404 both end the model and mean opposite things: *not now* against
    *not here*. :func:`abandon_reason` keeps them apart in the report, because a
    run abandoned for a busy GPU is one to repeat and a run abandoned for an
    unknown model id is one to fix.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        return True, False                      # unreachable / timeout / reset
    if status == BUSY_STATUS:
        return False, True
    if status in FATAL_STATUSES:
        return False, True
    if status >= 500:
        return True, False
    return False, False


def abandon_reason(exc: BaseException) -> str:
    """Why a model stopped, in the words the report should use.

    Not decoration: "abandoned" alone leaves the reader to guess whether to
    re-run, and the two common causes want opposite actions.
    """
    status = getattr(exc, "status_code", None)
    if status == BUSY_STATUS:
        return ("the GPU is claimed by a training run — nothing is wrong with the "
                "model or this batch; re-run when the run finishes")
    if status in FATAL_STATUSES:
        return ("the gateway rejected the request outright (auth, or a model id it "
                "does not have) — re-running changes nothing")
    return "too many consecutive failures"


# ── the recogniser ───────────────────────────────────────────────────────────

# (image path, model id) -> the gateway's result object
Recogniser = Callable[[Path, str], object]


class PageSource(Protocol):
    """Where the runner gets a page's bytes from.

    One method, so the batch runner never has to know whether the corpus is a
    directory on local disk or a mounted share with a working-copy cache in front
    of it — and so a test can hand it a stub instead of a mount.
    """

    def fetch(self, src: Path) -> tuple[Path, dict]:
        """``(a local path to read, a record of the original file)``."""


def gateway_recogniser(base_url: Optional[str] = None,
                       timeout: Optional[float] = None) -> Recogniser:
    """A recogniser backed by the ATR gateway, holding one HTTP connection open.

    ``/recognize``, not ``/ocr``: ``/ocr`` accepts only kraken and trocr, and
    rejects every ``vllm`` model with a 400 — and ``/recognize`` is also the only
    one that returns the per-line readings this batch keeps.

    The client is opened once and reused for the whole run. A connection per page
    would spend a TLS handshake on every call and, over a long batch, exhaust
    ephemeral ports on the client before it exhausted anything on the gateway.
    """
    from agent_a.kraken_client import KrakenHTTPClient

    client = KrakenHTTPClient(base_url=base_url, timeout=timeout)
    client.__enter__()

    def _recognise(image: Path, model: str):
        return client.recognize(image, model=model)

    _recognise.close = lambda: client.__exit__(None, None, None)  # type: ignore[attr-defined]
    return _recognise


# ── one page ─────────────────────────────────────────────────────────────────

def _result_payload(page: PageRef, model: str, run: str, result,
                    source: Optional[dict] = None,
                    read_path: Optional[Path] = None) -> dict:
    """The JSON written beside the transcription.

    It carries the source's **sha256** as well as its name. A comparison that
    outlives the staging directory has to be able to say which bytes produced a
    reading; a filename cannot, and re-scanned or re-cropped images keep their
    names.

    ``source`` is that record, already computed — a page cache hashes the
    original while it has it in hand, and hashing it again would mean pulling
    25 MB back across a mount for a number we were handed. Without one the file
    is hashed here, as it always was. ``read_path`` names the working copy the
    model actually saw when it was not the original itself, so the digest and
    the image are never confused for each other.
    """
    src = dict(source) if source else {
        "name": page.name,
        "sha256": _sha256(page.path),
        "bytes": page.path.stat().st_size,
    }
    src["key"] = page.key
    if read_path is not None and Path(read_path) != page.path:
        src["working_copy"] = Path(read_path).name
    return {
        "schema": SCHEMA,
        "run": run,
        "model": model,
        "engine": getattr(result, "engine", "") or "",
        "doc_id": page.doc_id,
        "source": src,
        "text": getattr(result, "text", "") or "",
        "lines": list(getattr(result, "lines", []) or []),
        "confidence": getattr(result, "confidence", 0.0),
        "segmented_by": getattr(result, "segmented_by", None),
        "timing_ms": int(getattr(result, "timing_ms", 0) or 0),
        "truncated": bool(getattr(result, "truncated", False)),
        "second_opinion": getattr(result, "second_opinion", None),
        "gateway_version": getattr(result, "service_version", "?"),
        "recognised_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _fetch_page(page: PageRef, cache: "PageSource", retries: int,
                backoff: float, sleep) -> tuple[Path, dict]:
    """The page's bytes, retried the way a recognition is retried.

    **This is the half that was missing.** A recognition that hits a 5xx is
    retried with backoff; a *fetch* that hit one was not, so a transient error
    from the share made that page permanently failed — and five in a row
    abandoned the model. On 2026-09-21 the share answered four concurrent 25 MB
    downloads with `500 Internal Server Error` and with XML that does not parse,
    and a 6742-page run died after 29 pages with a report blaming the model.

    Retried on **anything**, because nothing a source can do says "this model is
    wrong". A file that is genuinely gone still fails after the retries, and it
    fails as one page rather than as the run.
    """
    last: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return cache.fetch(page.path)
        except Exception as exc:  # noqa: BLE001 — re-raised below once retries run out
            last = exc
            if attempt == retries:
                break
            wait = backoff * (2 ** attempt)
            logger.warning(f"[batch] fetching {page.key}: {type(exc).__name__}: {exc} "
                           f"— retry in {wait:.0f}s")
            sleep(wait)
    raise last  # type: ignore[misc]


def _recognise_page(page: PageRef, model: str, run: str, out_dir: Path,
                    recognise: Recogniser, retries: int,
                    backoff: float = 2.0, sleep=time.sleep,
                    cache: Optional["PageSource"] = None) -> PageOutcome:
    """Read one page with one model, with retries, and write both artifacts.

    The text file is written **after** the JSON so that the JSON — the file
    ``is_complete`` checks — is never the newer of the two. A resumed run that
    found the JSON present and the text missing would skip a page whose
    transcription does not exist.

    With a ``cache``, the page is fetched through it: the corpus root may be a
    mounted share where opening a file is a network transfer, and the cache turns
    that into one transfer per page for the life of the cache directory. It is
    consulted **after** the completeness check, so a resumed run does not touch
    the mount for pages it already has.
    """
    txt_path, json_path = result_paths(out_dir, page.key)
    if is_complete(json_path):
        return PageOutcome(key=page.key, model=model, status="skipped")

    read_path, source = page.path, None
    if cache is not None:
        try:
            read_path, source = _fetch_page(page, cache, retries, backoff, sleep)
        except Exception as exc:  # noqa: BLE001 — an unfetchable page is that page's failure
            return PageOutcome(key=page.key, model=model, status="failed",
                               error=f"source: {type(exc).__name__}: {exc}",
                               source_error=True)

    last_exc: Optional[BaseException] = None
    for attempt in range(retries + 1):
        try:
            result = recognise(read_path, model)
            break
        except Exception as exc:  # noqa: BLE001 — classified below, never swallowed
            retryable, fatal = classify_failure(exc)
            last_exc = exc
            if fatal:
                return PageOutcome(key=page.key, model=model, status="failed",
                                   error=f"{type(exc).__name__}: {exc}", fatal=True,
                                   reason=abandon_reason(exc))
            if not retryable or attempt == retries:
                return PageOutcome(key=page.key, model=model, status="failed",
                                   error=f"{type(exc).__name__}: {exc}")
            wait = backoff * (2 ** attempt)
            logger.warning(f"[batch] {model} {page.key}: {exc} — retry in {wait:.0f}s")
            sleep(wait)
    else:  # pragma: no cover — the loop always breaks or returns
        return PageOutcome(key=page.key, model=model, status="failed",
                           error=f"{type(last_exc).__name__}: {last_exc}")

    payload = _result_payload(page, model, run, result, source=source,
                              read_path=read_path)
    _write_atomic(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
    _write_atomic(txt_path, payload["text"])
    return PageOutcome(
        key=page.key, model=model, status="done",
        chars=len(payload["text"]), lines=len(payload["lines"]),
        timing_ms=payload["timing_ms"], truncated=payload["truncated"],
    )


# ── one model over the corpus ────────────────────────────────────────────────

def _append_manifest(manifest: Path, record: dict) -> None:
    """Append one line to the run manifest. Never fatal: a manifest that cannot
    be written must not cost a page that was recognised successfully."""
    try:
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning(f"[batch] manifest write failed: {exc}")


def _progress_line(model: str, done: int, total: int, outcome: "ModelOutcome",
                   elapsed: float) -> str:
    """How far a model is, at a rate that is not a lie on a resumed run.

    The rate used to be ``done / elapsed``, where ``done`` counts every position
    walked — **including pages skipped because they were already on disk**. Those
    cost microseconds, so a resumed run reported a rate inflated by however much
    it had already finished: on 2026-09-22, ``18.2 p/min`` against a measured 2.3,
    and an ETA of 248 minutes against a real 33 hours. The giveaway was the ETA
    *growing* between lines as the inflated rate decayed toward the true one.

    So the rate counts only pages this run actually worked — read or failed —
    and the line states the three counts separately, because "2250 of 6742" and
    "330 of them by this process" are both true and only one of them is a speed.

    The ETA assumes every remaining position still needs work. On a resumed run
    some of them will be skipped and it will finish earlier, which is the
    direction an estimate should be wrong in; the ``skipped`` count in the same
    line is what tells a reader to expect that.
    """
    worked = outcome.done + outcome.failed
    parts = [f"[batch] {model}: {done}/{total} pages",
             f"({outcome.done} read, {outcome.failed} failed, "
             f"{outcome.skipped} skipped)"]
    if worked and elapsed > 0:
        rate = worked / elapsed
        eta_min = (total - done) / rate / 60
        parts.append(f"{rate * 60:.1f} p/min")
        parts.append(f"ETA {eta_min:.0f} min" if eta_min < 120
                     else f"ETA {eta_min / 60:.1f} h")
    return " · ".join(parts)


def run_model(pages: Sequence[PageRef], model: str, run: str, out_root: Path,
              recognise: Recogniser, *, retries: Optional[int] = None,
              concurrency: Optional[int] = None,
              max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
              manifest: Optional[Path] = None,
              cache: Optional["PageSource"] = None,
              sleep=time.sleep) -> ModelOutcome:
    """Read every page with one model, writing into ``out_root/<model>/``.

    Abandons the model on a fatal failure (an id the gateway does not have, a
    rejected key) or after ``max_consecutive_failures`` model errors in a row.
    The model-error consecutive counter resets on any success, so a corpus with
    a few bad images runs to the end while a gateway that has gone away does
    not consume the rest of the night before saying so.

    Source errors (share unavailable, ParseError) are handled separately
    (agentic-historian#456): they do NOT increment the model-error counter
    and do NOT abandon the model. After MAX_CONSECUTIVE_SOURCE_FAILURES the
    run pauses with exponential back-off and retries the failed pages. After
    SOURCE_PAUSE_BUDGET total source errors the model is abandoned with a
    distinct reason ("share unavailable") so it is clear nothing is wrong with
    the model itself. Pages that failed the source check get a second pass at
    the end of the main loop before the model is declared abandoned.
    """
    retries = config.ATR_BATCH_RETRIES if retries is None else retries
    concurrency = config.ATR_BATCH_PAGE_CONCURRENCY if concurrency is None else concurrency
    out_dir = out_root / model
    out_dir.mkdir(parents=True, exist_ok=True)
    outcome = ModelOutcome(model=model)

    # ── source-error circuit (agentic-historian#456) ────────────────────────
    # Pages that fail to fetch (share down, ParseError, timeout) are tracked
    # and retried at the end of the main loop.  Source errors do NOT increment
    # the model-error consecutive counter and do NOT abandon the model.
    source_fail_pages: list[PageRef] = []
    SOURCE_PAUSE_BUDGET = 20      # total source errors before giving up
    SOURCE_PAUSE_BASE = 60        # seconds; doubled on each pause up to CAP
    SOURCE_PAUSE_MAX = 900        # 15 min cap
    MAX_CONSECUTIVE_SOURCE_FAILURES = 5  # pause-and-retry trigger

    # ── model-error circuit (existing behaviour) ────────────────────────────
    consecutive = 0
    consecutive_source = 0

    started = time.perf_counter()
    total = len(pages)

    def _one(page: PageRef) -> PageOutcome:
        return _recognise_page(page, model, run, out_dir, recognise, retries,
                               sleep=sleep, cache=cache)

    def _record(index: int, res: PageOutcome) -> bool:
        """Fold one page outcome in. Returns False when the model must stop.

        Source errors (res.source_error=True) go through the source circuit:
        they do NOT increment the model consecutive counter and do NOT abandon
        the model.  After MAX_CONSECUTIVE_SOURCE_FAILURES the run pauses with
        exponential back-off and retries the failed pages.  Model errors
        (res.source_error=False) go through the model circuit as before.
        """
        nonlocal consecutive, consecutive_source
        if res.status == "done":
            outcome.done += 1
            outcome.chars += res.chars
            outcome.lines += res.lines
            outcome.recognition_ms += res.timing_ms
            outcome.truncated += int(res.truncated)
            if res.chars == 0:
                outcome.empty += 1
                if len(outcome.empty_keys) < 20:
                    outcome.empty_keys.append(res.key)
        elif res.status == "skipped":
            outcome.skipped += 1
        else:
            outcome.failed += 1
            if res.source_error:
                outcome.source_errors += 1
                if len(outcome.source_error_keys) < 20:
                    outcome.source_error_keys.append(res.key)
                if res not in source_fail_pages:
                    source_fail_pages.append(res)
            else:
                outcome.errors.append(f"{res.key}: {res.error}")
        if manifest:
            _append_manifest(manifest, {
                "model": model, "key": res.key, "status": res.status,
                "chars": res.chars, "lines": res.lines, "timing_ms": res.timing_ms,
                "truncated": res.truncated, "error": res.error,
                "source_error": res.source_error,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

        if res.ok:
            consecutive = 0
            consecutive_source = 0
        elif res.source_error:
            # Source error: route through source circuit, not model circuit.
            # Reset model counter so source errors don't cause model abandon.
            logger.warning(f"[batch] {model} {res.key}: source error: {res.error}")
            consecutive = 0
            consecutive_source += 1
            if consecutive_source >= MAX_CONSECUTIVE_SOURCE_FAILURES:
                pause_s = min(
                    SOURCE_PAUSE_BASE * (2 ** (consecutive_source // MAX_CONSECUTIVE_SOURCE_FAILURES - 1)),
                    SOURCE_PAUSE_MAX,
                )
                logger.warning(f"[batch] {model}: {consecutive_source} consecutive source "
                               f"failures \u2014 pausing {pause_s:.0f}s then retrying")
                sleep(pause_s)
                # Retry source-fail pages; success resets the counter.
                retry_keys = {p.key for p in source_fail_pages}
                for rp in [p for p in pages if p.key in retry_keys]:
                    ret = _one(rp)
                    if ret.ok:
                        # A page that was a source error is now done — update outcome.
                        consecutive_source = 0
                        break
                    _record(total, ret)  # failed retry goes back through _record
        else:
            # Model error: route through model circuit (existing behaviour).
            consecutive += 1
            consecutive_source = 0  # reset source counter on model error
            logger.error(f"[batch] {model} {res.key}: {res.error}")
            if res.fatal:
                outcome.aborted = f"{res.reason or 'fatal'} \u2014 {res.error} (on {res.key})"
                return False
            if consecutive >= max_consecutive_failures:
                outcome.aborted = (
                    f"{consecutive} consecutive failures, last on {res.key}: {res.error}"
                )
                return False

        done = index + 1
        if res.status == "done" and (done % 10 == 0 or done == total):
            logger.info(_progress_line(model, done, total, outcome,
                                       time.perf_counter() - started))
        return True

    logger.info(f"[batch] {model}: {total} page(s) \u2192 {out_dir}")
    if concurrency <= 1:
        for i, page in enumerate(pages):
            if not _record(i, _one(page)):
                break
    else:
        # Bounded, *ordered*, and submitted a window at a time.  Ordered because
        # the consecutive-failure counter has to follow the corpus, not the order
        # results happen to come back in.  A window at a time because
        # ``Executor.map`` submits every page up front: the circuit breaker would
        # then "stop" a model whose remaining four hundred pages are already
        # queued, which is not stopping.
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            stop = False
            for start in range(0, total, concurrency):
                window = pages[start:start + concurrency]
                for offset, res in enumerate(list(pool.map(_one, window))):
                    if not _record(start + offset, res):
                        stop = True
                        break
                if stop:
                    break

    # ── second pass: retry source-error pages before final abandon ──────────
    # The share is usually back within minutes; without this those pages stay
    # failed until a manual re-run.  On retry success the page is promoted
    # from failed\u2192done and outcome.source_errors is decremented.
    if source_fail_pages and not outcome.aborted:
        logger.info(f"[batch] {model}: second pass \u2014 retrying {len(source_fail_pages)} "
                    "pages that failed the source check")
        still_failing: list = []
        for page in source_fail_pages:
            retry_res = _one(page)
            if retry_res.ok:
                outcome.failed = max(0, outcome.failed - 1)
                outcome.done += 1
                outcome.chars += retry_res.chars
                outcome.lines += retry_res.lines
                outcome.recognition_ms += retry_res.timing_ms
                outcome.source_errors = max(0, outcome.source_errors - 1)
            elif retry_res.source_error:
                still_failing.append(retry_res)
            else:
                # Retry got a model error — hand to model circuit.
                _record(total, retry_res)
        if still_failing:
            logger.warning(f"[batch] {model}: {len(still_failing)} source errors "
                           "persisted after second pass")
            source_fail_pages = still_failing
        else:
            source_fail_pages = []
        if len(source_fail_pages) >= SOURCE_PAUSE_BUDGET:
            outcome.aborted = (
                f"share unavailable \u2014 {len(source_fail_pages)} pages could not be "
                "fetched after multiple retries; nothing is wrong with the model or "
                "gateway; re-run later (finished pages are preserved)"
            )

    outcome.elapsed_s = time.perf_counter() - started
    if outcome.aborted:
        logger.error(f"[batch] {model} ABANDONED \u2014 {outcome.aborted}")
    else:
        logger.info(
            f"[batch] {model} finished: {outcome.done} read, {outcome.skipped} already "
            f"present, {outcome.failed} failed ({outcome.source_errors} source) "
            f"in {outcome.elapsed_s / 60:.1f} min"
        )
    return outcome


def run_batch(pages: Sequence[PageRef], models: Sequence[str], run: str,
              out_root: Path, recognise: Recogniser, **kwargs) -> BatchReport:
    """Every model over every page, **model-major** — see the module docstring.

    One model being abandoned never stops the batch: the others still run, and the
    report says which one stopped and why. A run that aborts wholesale because the
    third model was mistyped throws away the two that worked.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = kwargs.pop("manifest", out_root / "manifest.jsonl")
    report = BatchReport(
        run=run, out_root=out_root, pages=len(pages),
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    started = time.perf_counter()
    for model in models:
        report.models.append(
            run_model(pages, model, run, out_root, recognise, manifest=manifest, **kwargs)
        )
    report.elapsed_s = time.perf_counter() - started
    return report


def report_from_outputs(run_dir: Path) -> BatchReport:
    """Rebuild a run's report from the results in its directory.

    The report a run writes is a record of what the runner *observed*, and two
    things make that an incomplete account of what is actually on disk.

    A **resumed** run sees most of its corpus as `skipped` and counts nothing
    about it — not its characters, not its timings, and not whether it came back
    empty. Since a long run is resumed at least once, the empty column, which
    exists precisely because nothing else moves when a page comes back blank, is
    the number most likely to be wrong in the direction of "nothing to see".

    A run whose code changed mid-flight is the other: the corpus run of
    2026-09-17 started before the empty column existed and finished after, so its
    report simply has no such column while 77 of its 899 pages were blank.

    Rebuilding reads the results themselves, so both cases come out right. What
    it cannot recover is a page that failed: a failure writes no file, and a
    missing file is indistinguishable from a page nobody asked for. The rebuilt
    report is marked and says so rather than reporting a confident zero.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"not a run directory: {run_dir}")

    report = BatchReport(run=run_dir.name, out_root=run_dir, pages=0, rebuilt=True)
    stamps: list[datetime] = []
    for model_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        outcome = ModelOutcome(model=model_dir.name)
        seen: list[datetime] = []
        for json_path in sorted(model_dir.glob("*.json")):
            data = _read_result(json_path)
            if data is None:
                outcome.failed += 1
                if len(outcome.errors) < 20:
                    outcome.errors.append(f"{json_path.stem}: unreadable result")
                continue
            text = data["text"]
            outcome.done += 1
            outcome.chars += len(text)
            outcome.lines += len(data.get("lines") or [])
            outcome.recognition_ms += int(data.get("timing_ms") or 0)
            outcome.truncated += int(bool(data.get("truncated")))
            if not text:
                outcome.empty += 1
                if len(outcome.empty_keys) < 20:
                    outcome.empty_keys.append(json_path.stem)
            when = _parse_stamp(data.get("recognised_at"))
            if when:
                seen.append(when)
            if data.get("run"):
                report.run = data["run"]
        if seen:
            outcome.elapsed_s = (max(seen) - min(seen)).total_seconds()
            stamps += seen
        report.models.append(outcome)

    report.pages = max((m.attempted for m in report.models), default=0)
    if stamps:
        report.started_at = min(stamps).isoformat(timespec="seconds")
        report.elapsed_s = (max(stamps) - min(stamps)).total_seconds()
    return report


def _read_result(json_path: Path) -> Optional[dict]:
    """One result file, or None when it is missing, unparsable or another shape."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("schema") != SCHEMA or not isinstance(data.get("text"), str):
        return None
    return data


def _parse_stamp(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _empty_section(report: BatchReport) -> list[str]:
    """The "empty" column, spelled out — the quietest way this pipeline fails.

    A page that comes back with no text at all is a success on every signal the
    runner has: HTTP 200, no exception, a ``.txt`` and a ``.json`` on disk, and
    ``is_complete`` will skip it for ever after. Nothing in the other columns
    moves. Measured on a 25-page sample of the Lassberg share on 2026-09-16:
    **three pages**, 12%, which over the whole corpus is several hundred.

    Two causes, and they want opposite responses. A blank verso or an envelope
    flap *should* be empty, and the count is then a property of the corpus worth
    knowing before anyone extrapolates a cost per page. A written page that comes
    back empty means the segmenter found no lines on it, and that is a defect —
    invisible in every other number here.
    """
    hit = [m for m in report.models if m.empty]
    if not hit:
        return []
    out = ["", "## Pages that came back empty", ""]
    for m in hit:
        share = 100.0 * m.empty / m.done if m.done else 0.0
        out.append(f"- `{m.model}`: {m.empty} of {m.done} page(s) ({share:.0f}%)")
        out += [f"  - {key}" for key in m.empty_keys]
        if m.empty > len(m.empty_keys):
            out.append(f"  - … {m.empty - len(m.empty_keys)} more (see `manifest.jsonl`)")
    out += [
        "",
        "These are successes by every signal this runner has: a 200, no error, "
        "both files on disk, and `is_complete` will skip them on every later run. "
        "**Look at the images before reading anything into the number.** A blank "
        "verso or an envelope flap is genuinely empty, and knowing how many the "
        "corpus holds is worth having before extrapolating a cost per page; a "
        "written page that comes back empty means the segmenter found no lines "
        "on it, which no other column in this report would ever show.",
    ]
    return out


def _truncation_section(report: BatchReport) -> list[str]:
    """The "cut off" column, spelled out — because it is the one number here that
    means the corpus is wrong rather than merely expensive.

    A truncated reading is not a failure: the request succeeded, the text is real,
    there is just less of it than the page has. It ends mid-sentence and reads
    exactly like a model that gave up — so without this note, the natural response
    is to blame the model and try another one, when the fix is a larger ceiling.
    """
    hit = [m for m in report.models if m.truncated]
    if not hit:
        return []
    out = ["", "## Readings that were cut off", ""]
    out += [f"- `{m.model}`: {m.truncated} of {m.done} page(s)" for m in hit]
    out += [
        "",
        "These pages hit the model's token ceiling and stop mid-text. The request "
        "succeeded and the text is real — there is just less of it than the page "
        "has, and it reads like a model that gave up rather than one that was "
        "interrupted. Raise `ATR_VLLM_MAX_NEW_TOKENS` on the gateway, restart it, "
        "delete the affected results and re-run: the batch re-reads only what is "
        "missing.",
    ]
    return out


#: A readings appendix is for a run a person is about to read — a smoke run, a
#: sample, the ten pages someone is deciding a model on. Past this many pages per
#: model the file stops being a document and becomes a dump, so the appendix
#: names the directory instead.
READINGS_MAX_PAGES = 25

#: And a ceiling on the whole appendix, because pages differ. Kept well under the
#: 40 000 characters `mcp_atr` hands back for a report, so what a remote caller
#: receives is the whole file rather than a slice of one.
READINGS_BUDGET_CHARS = 30_000


def _readings_section(report: BatchReport) -> list[str]:
    """The transcriptions themselves, for a run small enough to read.

    The report has always said "comparing the readings is the point" and then
    shown every column except the readings. This is that appendix.

    Read from **disk**, not from this invocation's outcomes, so a run resumed one
    model at a time still shows all of them — and so the pages a run skipped
    because they were already read are in it too.
    """
    out_root = report.out_root
    if not out_root.is_dir():
        return []

    per_model: list[tuple[str, list[Path]]] = []
    for child in sorted(out_root.iterdir()):
        if child.is_dir():
            per_model.append((child.name, sorted(child.glob("*.txt"))))
    per_model = [(name, files) for name, files in per_model if files]
    if not per_model:
        return []

    biggest = max(len(files) for _, files in per_model)
    if biggest > READINGS_MAX_PAGES:
        return [
            "",
            "## Readings",
            "",
            f"{biggest} page(s) per model — too many to put in one file. The "
            f"transcriptions are in `{out_root}`, one `.txt` per page per model.",
        ]

    lines = [
        "",
        "## Readings",
        "",
        "The texts themselves, so the comparison the table refuses to make can be "
        "made by eye. **Read to the end of each one**: a page that hit the token "
        "ceiling comes back as an ordinary success and stops mid-sentence, and it "
        "is marked here where that happened.",
    ]
    budget = READINGS_BUDGET_CHARS
    omitted = 0
    for model, files in per_model:
        lines += ["", f"### `{model}`"]
        for path in files:
            text = path.read_text(encoding="utf-8").strip()
            if len(text) > budget:
                omitted += 1
                continue
            budget -= len(text)
            note = ""
            if _truncated_flag(path):
                note = " — stopped at the token ceiling"
            lines += ["", f"#### {path.stem} ({len(text)} characters{note})", "",
                      "~~~text", text or "(empty)", "~~~"]
    if omitted:
        lines += ["", f"*{omitted} page(s) omitted here for length — all of them "
                      f"are in `{out_root}`.*"]
    return lines


def _truncated_flag(txt_path: Path) -> bool:
    """Whether this page stopped at the token ceiling, per its sibling JSON."""
    try:
        data = json.loads(txt_path.with_suffix(".json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(data.get("truncated"))


def format_report(report: BatchReport) -> str:
    """A Markdown summary of the run — what each model produced and what it cost.

    Not a ranking. Nothing here is a quality measure: with no ground truth,
    characters per page and confidence say how much a model wrote and how sure it
    was while writing it, and a model that hallucinates fluently scores well on
    both. Reading them as a league table is the one way to misuse this file, so it
    says so where the table is.
    """
    lines = [
        f"# ATR batch — {report.run}",
        "",
        f"- pages: **{report.pages}**",
        f"- started: {report.started_at}",
        f"- wall time: {report.elapsed_s / 60:.1f} min",
        "",
        "| model | read | skipped | failed | empty | cut off | chars/page | s/page | wall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for m in report.models:
        lines.append(
            f"| `{m.model}` | {m.done} | {m.skipped} | {m.failed} | {m.empty} | "
            f"{m.truncated} | "
            f"{m.mean_chars:.0f} | {m.mean_ms / 1000:.1f} | {m.elapsed_s / 60:.1f} min |"
        )
    lines += [
        "",
        "**These columns are not quality.** There is no ground truth in this run, and "
        "characters per page measures how much a model wrote, not how much of it is "
        "right — a model that hallucinates fluently leads this table. Comparing the "
        "readings is the point; the numbers only say what each run cost and whether it "
        "completed. Measuring accuracy needs transcribed lines and `eval/linebench.py`.",
    ]
    if report.rebuilt:
        lines += [
            "",
            "_Rebuilt from the files in this directory, not observed as the run "
            "happened._ **The failed column is not trustworthy here**: a page that "
            "failed wrote nothing, and nothing is what a rebuilt report cannot see. "
            "It counts only results that are present and unreadable. Everything "
            "else — including the empty column, which a resumed run undercounts "
            "and this does not — is read from the results themselves.",
        ]
    lines += _truncation_section(report)
    lines += _empty_section(report)
    aborted = [m for m in report.models if m.aborted]
    if aborted:
        lines += ["", "## Abandoned", ""]
        lines += [f"- `{m.model}` — {m.aborted}" for m in aborted]
    failed = [m for m in report.models if m.failed and not m.aborted]
    if failed:
        lines += ["", "## Pages that failed", ""]
        for m in failed:
            lines.append(f"- `{m.model}`: {m.failed} page(s)")
            lines += [f"  - {e}" for e in m.errors[:10]]
            if m.failed > 10:
                lines.append(f"  - … {m.failed - 10} more (see `manifest.jsonl`)")
    lines += _readings_section(report)
    return "\n".join(lines) + "\n"


def write_report(report: BatchReport) -> Path:
    """Write ``report.md`` + ``report.json`` into the run directory."""
    md = report.out_root / "report.md"
    _write_atomic(md, format_report(report))
    _write_atomic(report.out_root / "report.json",
                  json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return md
