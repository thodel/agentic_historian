"""
batch_runner.py — R2: process a corpus outside the Discord queue (#392).

Thousands of pages cannot flow through one serialised Discord queue: at 2–5
minutes a page, 5,000 pages are one to three weeks of uninterruptible runtime,
and a restart halfway through used to mean starting over. R1 (#391) gave the
corpus a manifest that survives being interrupted; this claims from it with N
workers.

**The same code path as `/run`.** Documents go through `run_full_pipeline` and
orders through `run_full_pipeline_group` — the functions the bot calls. A second
pipeline for batch work would drift from the interactive one, and the drift
would show up as a corpus that was processed differently from the pages anybody
had looked at.

Threads, not processes
──────────────────────
Every expensive step is a wait on somebody else's socket: the VLM, the ATR
gateway, kraken, the WebDAV download. The GIL is not the limit here, and threads
keep one log, one config and one manifest connection per thread, which
``corpus_manifest`` is built for. (Processes would work too — SQLite's
``BEGIN IMMEDIATE`` claim is cross-process — and nothing here forecloses them.)

Why a heartbeat thread, and not a heartbeat in the worker
─────────────────────────────────────────────────────────
A claim goes stale after ``corpus_manifest.STALE_AFTER`` (30 min) without a sign
of life, and a multi-page order can take longer than that. A worker inside
``run_full_pipeline`` cannot beat its own heartbeat — the call blocks for
minutes. So one daemon thread beats for every claim currently in flight. Without
it the runner is correct until the first order that takes 31 minutes, at which
point a second runner would reclaim live work and process it twice.

Backoff paces the worker, by the document's attempt count
─────────────────────────────────────────────────────────
``corpus_manifest`` hands a failed document straight back, so a worker could
burn all three attempts in milliseconds against a gateway that is down. After a
failure the worker therefore sleeps ``BACKOFF_BASE_S × 2^(attempts-1)``, capped.

This paces the *worker* rather than the document, which is the honest version of
what is possible against R1's API: another worker may pick the same document up
at once, and that is usually right — a flake can be worker-local. During a real
outage every worker fails and every worker sleeps, so the pool paces itself.
Per-document pacing would need a ``not_before`` column in the manifest, and
adding one quietly to R1 is not this issue's business.

Publishing
──────────
#392 says publishing hooks into the batch path rather than one commit per
document. ``run_full_pipeline`` publishes per document when
``ENABLE_GITHUB_PUBLISH`` is on, so the runner **refuses to start** in that
configuration and names ``publish-batch`` instead. It does not quietly turn the
setting off for the duration: a batch that mutates global configuration behind
the operator's back is how a bot running at the same time starts behaving
differently for reasons nobody can see.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

import config
import corpus_manifest as cm

__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "DEFAULT_WORKERS",
    "HEARTBEAT_EVERY_S",
    "ORDERS",
    "PAGES",
    "PROGRESS_EVERY",
    "AmbiguousSource",
    "Source",
    "Summary",
    "backoff",
    "inspect_source",
    "run_batch",
]

#: One document per image, or one document per subfolder.
PAGES, ORDERS = "pages", "orders"

#: Conservative: the gateway serves one model at a time on one GPU, so more
#: workers than this mostly queue inside it. `--workers` overrides.
DEFAULT_WORKERS = 2

BACKOFF_BASE_S = 30.0
BACKOFF_CAP_S = 600.0

#: Well inside ``corpus_manifest.STALE_AFTER`` so a slow document is never
#: reclaimed while it is being worked on.
HEARTBEAT_EVERY_S = 120.0

#: Documents between progress announcements. Per-N-documents, not per-step: a
#: message per stage would bury the channel and nobody would read the one that
#: mattered.
PROGRESS_EVERY = 10


class AmbiguousSource(RuntimeError):
    """The folder is both a page folder and a folder of orders."""


def backoff(attempts: int, *, base: float = BACKOFF_BASE_S,
            cap: float = BACKOFF_CAP_S) -> float:
    """Seconds to wait after a document's ``attempts``-th failure."""
    if attempts < 1:
        return 0.0
    return min(cap, base * (2 ** (attempts - 1)))


# ── what the folder is ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class Source:
    root: Path
    mode: str
    doc_ids: list = field(default_factory=list)
    #: doc_id → the files that make it up, in natural order.
    paths: dict = field(default_factory=dict)
    #: How the mode was decided, so a surprising run is diagnosable from the log.
    why: str = ""

    @property
    def pages(self) -> int:
        return sum(len(v) for v in self.paths.values())


def _ingestible(folder: Path) -> list[Path]:
    from utils.switchdrive import INGEST_EXTS
    import re

    def natural(name: str):
        return [int(c) if c.isdigit() else c.lower()
                for c in re.split(r"(\d+)", name)]

    try:
        found = [p for p in folder.iterdir()
                 if p.is_file() and p.suffix.lower() in INGEST_EXTS]
    except OSError as e:
        logger.warning(f"[batch] cannot read {folder}: {e}")
        return []
    return sorted(found, key=lambda p: natural(p.name))


def inspect_source(root: Path, *, mode: Optional[str] = None) -> Source:
    """What is in this folder, and how each document is made of it.

    The mode is **derived** and the derivation is reported, with one case left
    to a human: a folder that holds both loose pages and subfolders of pages is
    genuinely ambiguous, and guessing would either process the loose pages as a
    phantom order or ignore them entirely. Both are silent, so neither is
    allowed — ``--mode`` settles it.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise RuntimeError(f"not a directory: {root}")

    loose = _ingestible(root)
    try:
        subdirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as e:                         # pragma: no cover — defensive
        raise RuntimeError(f"cannot read {root}: {e}") from e
    orders = {d.name: _ingestible(d) for d in subdirs}
    orders = {name: files for name, files in orders.items() if files}

    if mode is None:
        if loose and orders:
            raise AmbiguousSource(
                f"{root} holds {len(loose)} loose page(s) and "
                f"{len(orders)} folder(s) of pages. Processing it as pages "
                f"would ignore the folders; as orders it would ignore the loose "
                f"pages. Say which with --mode pages|orders.")
        mode = ORDERS if orders else PAGES
        why = (f"{len(orders)} subfolder(s) with pages and none loose"
               if mode == ORDERS else
               f"{len(loose)} page(s) in the folder itself, no subfolder with any")
    else:
        if mode not in (PAGES, ORDERS):
            raise RuntimeError(f"unknown mode {mode!r}; use {PAGES} or {ORDERS}")
        why = f"--mode {mode}"

    if mode == ORDERS:
        paths = orders
    else:
        # One document per image, keyed as the orchestrator keys it (`fp.stem`),
        # so a RunState written here is the one `/route` and `/votes` find.
        paths = {p.stem: [p] for p in loose}
    return Source(root=root, mode=mode, doc_ids=sorted(paths), paths=paths,
                  why=why)


# ── running ──────────────────────────────────────────────────────────────────

def _publish_batch(doc_ids: list, label: str):
    """Commit these documents' outputs in one commit (#394).

    Each commit to the output repo triggers its index-rebuild Action, so one
    commit per document meant 500 commits and 500 Action runs for a holding.
    Non-fatal and retryable: an identical tree makes no second commit, so a
    re-run of the same documents reports `unchanged` rather than piling up.
    """
    from utils.publish_github import publish_docs
    return publish_docs(doc_ids, label=label)


@dataclass
class Summary:
    run_id: str
    mode: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    #: doc_id → the worker that finished it, for the log.
    done: dict = field(default_factory=dict)
    failed: dict = field(default_factory=dict)
    #: One entry per publish commit the run made.
    publishes: list = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return max(0.0, self.ended_at - self.started_at)

    @property
    def progress(self):
        return cm.progress(self.run_id)


def _default_pipeline(doc_id: str, paths: list, mode: str, *,
                      publish: bool = False) -> None:
    """The bot's own entry points, so batch and interactive cannot drift.

    ``publish=False`` by default: the runner commits N documents in one commit
    (#394) and the output repo rebuilds its index once per commit. Publishing
    per document here as well is the 500-commits-per-holding that #394 replaced.
    True restores the old behaviour for whoever wants it, at one commit and one
    index rebuild per document.
    """
    import orchestrator
    if mode == ORDERS:
        orchestrator.run_full_pipeline_group(doc_id, [str(p) for p in paths],
                                             publish=publish)
    else:
        orchestrator.run_full_pipeline(str(paths[0]), publish=publish)


def run_batch(run_id: str, source: Source, *, workers: int = DEFAULT_WORKERS,
              max_attempts: int = cm.MAX_ATTEMPTS,
              pipeline: Optional[Callable] = None,
              announce: Optional[Callable[[str], None]] = None,
              sleep: Optional[Callable[[float], None]] = None,
              heartbeat_every: float = HEARTBEAT_EVERY_S,
              progress_every: int = PROGRESS_EVERY,
              publish_every: Optional[int] = None,
              publish: Optional[Callable] = None,
              per_doc_publish: bool = False) -> Summary:
    """Claim and process until nothing claimable is left.

    Registering is additive and claiming only takes ``pending``/``failed``, so
    re-running this on the same ``run_id`` resumes: finished documents are not
    touched. Stale claims from a killed run are handed back **once, at the
    start** — explicitly, because a reclaim mid-run would fight the heartbeat.
    """
    if pipeline is None:
        def pipeline(doc_id, paths, mode):
            _default_pipeline(doc_id, paths, mode, publish=per_doc_publish)
    publish = publish or _publish_batch
    if publish_every is None:
        publish_every = getattr(config, "BATCH_PUBLISH_EVERY", 0)
    sleep = sleep or time.sleep
    say = announce or (lambda text: logger.info(f"[batch] {text}"))
    workers = max(1, int(workers or 1))

    cm.register(run_id, source.doc_ids, source=str(source.root),
                label=source.mode)
    reclaimed = cm.reclaim_stale(run_id, max_attempts=max_attempts)
    if reclaimed:
        say(f"{len(reclaimed)} verwaiste Beanspruchung(en) eines abgebrochenen "
            f"Laufs zurückgeholt: "
            f"{', '.join(i.doc_id for i in reclaimed)}")

    start = cm.progress(run_id)
    summary = Summary(run_id=run_id, mode=source.mode, started_at=time.time())
    say(f"**{run_id}** — {start.total} Dokument(e), {source.pages} Seite(n), "
        f"{start.terminal} schon fertig, {workers} Worker ({source.why})")

    in_flight: dict[str, str] = {}               # doc_id → worker
    guard = threading.Lock()
    stop = threading.Event()
    announced = {"at": start.terminal}
    #: An interrupt raised inside a worker, for the caller. A thread's exception
    #: dies in that thread and `join()` does not re-raise it, so without this
    #: slot an interrupted run returned a Summary that looked like a clean
    #: finish. Found by the test that asserted it propagated.
    interrupted: list = []

    def _beat() -> None:
        """Keep every in-flight claim alive. A worker inside the pipeline cannot
        do this itself: the call blocks for minutes."""
        while not stop.wait(heartbeat_every):
            with guard:
                current = dict(in_flight)
            for doc_id, worker in current.items():
                try:
                    if not cm.heartbeat(run_id, doc_id, worker=worker):
                        logger.warning(f"[batch] {doc_id}: claim is no longer "
                                       f"ours — another runner may have "
                                       f"reclaimed it")
                except Exception as e:           # noqa: BLE001
                    logger.warning(f"[batch] heartbeat {doc_id}: {e}")

    def _maybe_progress() -> None:
        prog = cm.progress(run_id)
        with guard:
            if prog.terminal - announced["at"] < max(1, progress_every):
                return
            announced["at"] = prog.terminal
        pct = f" ({prog.percent:.0%})" if prog.percent is not None else ""
        say(f"**{run_id}** {prog.terminal}/{prog.total}{pct} — "
            f"{prog.done} fertig, {prog.failed} offen nach Fehler, "
            f"{prog.dead} aufgegeben")

    pending_publish: list = []

    def _flush_publish(final: bool = False) -> None:
        """Commit the documents finished since the last publish.

        Every N when `publish_every` is positive, otherwise once at the end —
        which is the per-order case and the fewest Action runs. The buffer is
        cleared **before** the call, so a publish that throws cannot make the
        next flush try the same documents twice; the content is idempotent
        anyway (identical tree, no commit), but the log would claim two
        attempts at work that was one.
        """
        if per_doc_publish:
            # The pipeline already committed each document. Publishing them
            # again would be a second commit with an identical tree — harmless
            # (no commit is made) but it would claim a publish that was not one.
            return
        with guard:
            if not pending_publish:
                return
            if not final and (publish_every <= 0
                              or len(pending_publish) < publish_every):
                return
            batch = list(pending_publish)
            pending_publish.clear()
        try:
            got = publish(batch, f"{run_id}")
        except Exception as e:                   # noqa: BLE001
            logger.warning(f"[batch] publish of {len(batch)} doc(s) failed: {e}")
            return
        summary.publishes.append(got)
        outcome = getattr(got, "outcome", "?")
        say(f"**{run_id}** publiziert: {len(batch)} Dokument(e) in einem "
            f"Commit — {outcome}"
            + (f" ({got.url})" if getattr(got, "url", None) else ""))

    def _work(name: str) -> None:
        while not stop.is_set():
            try:
                claimed = cm.claim(run_id, worker=name, limit=1)
            except Exception as e:               # noqa: BLE001
                logger.warning(f"[batch] {name}: claim failed: {e}")
                return
            if not claimed:
                return
            item = claimed[0]
            with guard:
                in_flight[item.doc_id] = name
            try:
                pipeline(item.doc_id, source.paths.get(item.doc_id, []),
                         source.mode)
                cm.mark_done(run_id, item.doc_id)
                summary.done[item.doc_id] = name
                with guard:
                    pending_publish.append(item.doc_id)
                logger.info(f"[batch] {name}: {item.doc_id} done")
            except BaseException as e:           # noqa: BLE001
                # Every failure is the document's, never the run's: #392 is
                # explicit that the run continues. KeyboardInterrupt is caught
                # too — so the claim is handed back rather than left running —
                # and then re-raised below.
                got = cm.mark_failed(run_id, item.doc_id, f"{type(e).__name__}: {e}",
                                     max_attempts=max_attempts)
                summary.failed[item.doc_id] = f"{type(e).__name__}: {e}"
                status = got.status if got else "?"
                logger.warning(f"[batch] {name}: {item.doc_id} {status}: {e}")
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    stop.set()
                    with guard:
                        interrupted.append(e)
                    return
                delay = backoff(got.attempts if got else 1)
                if delay:
                    sleep(delay)
            finally:
                with guard:
                    in_flight.pop(item.doc_id, None)
            _maybe_progress()
            _flush_publish()

    beat = threading.Thread(target=_beat, name="batch-heartbeat", daemon=True)
    beat.start()
    threads = [threading.Thread(target=_work, args=(f"w{i + 1}",),
                                name=f"batch-w{i + 1}")
               for i in range(workers)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        stop.set()
        beat.join(timeout=5)

    # Whatever is left, in one commit. Before the interrupt is re-raised: an
    # interrupted run has still produced the documents it finished, and leaving
    # them unpublished would throw away work that is done and paid for.
    _flush_publish(final=True)

    summary.ended_at = time.time()
    if interrupted:
        # Before the closing summary: an interrupted run did not finish, and a
        # tidy "fertig nach N min" line would say it did.
        raise interrupted[0]
    end = cm.progress(run_id)
    pct = f" ({end.percent:.0%})" if end.percent is not None else ""
    say(f"**{run_id}** fertig nach {summary.seconds / 60:.1f} min — "
        f"{end.done} verarbeitet, {end.failed} offen nach Fehler, "
        f"{end.dead} aufgegeben, {end.terminal}/{end.total}{pct}")
    return summary
