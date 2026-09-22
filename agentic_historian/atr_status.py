"""Read the ATR machines from Discord: jobs, and the processes nobody registered.

Since 16.09.2026 these are two machines. **idhefix** (130.92.59.240) runs the
gateway and the recognition engines; **astraix** (130.92.59.242) trains. The
gateway answers for both: ``/gpu`` with its own cards, ``/train/*`` passed through
to the trainer (serving-atr-inference#137, #139).

The bot has the queue's view; the machines have theirs. The two drift apart and
nothing surfaces the gap — three incidents in two weeks were each found by hand
and late, the worst of them a data-loader worker holding 27 530 MiB for sixteen
hours while both cards read 0 % utilisation.

Everything here is **read-only**. Cancelling a job or killing a process is
defensible from Discord and deserves its own confirm flow and its own decision
about who may; reading first, and see what it is actually used for (#414).

The transport is the gateway on idhefix :8200, which already carries ``/train/*``
and already checks ``X-API-Key``. It is the only way in to either machine from
this host — and the key is the one the bot already holds for recognition, read
from the environment, never from a message.
"""

from __future__ import annotations

import httpx

import config

#: Discord hard-limits a message to 2000 characters; leave room for the fence.
LIMIT = 1900
TIMEOUT_S = 30


class AtrStatusError(RuntimeError):
    """The gateway could not answer. Carries what to tell the user."""


async def _get(path: str, params: dict | None = None):
    url = f"{config.ATR_GATEWAY_URL}{path}"
    headers = {"X-API-Key": config.ATR_API_KEY} if config.ATR_API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
            response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        # Name the URL. "Connection failed" without it sends the reader to the
        # wrong box, and this one is reachable only over the VPN.
        raise AtrStatusError(f"{type(exc).__name__} talking to {url}") from exc
    if response.status_code == 401:
        raise AtrStatusError("the gateway rejected the API key (401)")
    if response.status_code >= 400:
        raise AtrStatusError(f"{url} answered {response.status_code}: "
                             f"{response.text[:200]}")
    return response.json()


async def jobs() -> dict:
    return await _get("/train/jobs")


async def job(job_id: str) -> dict:
    return await _get(f"/train/jobs/{job_id}")


async def gpu() -> dict:
    """The TRAINING machine's cards (asteraix), with jobs attributed."""
    return await _get("/train/gpu")


async def serving_gpu() -> dict:
    """The SERVING machine's cards (idhefix) — the ones recognition runs on.

    Same card and process rows as ``gpu()``, plus a ``vllm`` block: which models
    are resident and the budget a new one has to fit into. No job attribution,
    because idhefix runs no jobs.
    """
    return await _get("/gpu")


async def log(job_id: str, lines: int = 30, stage: str = "train") -> dict:
    return await _get(f"/train/jobs/{job_id}/log",
                      {"stage": stage, "lines": lines})


# ── formatting ───────────────────────────────────────────────────────────────

def _hours(seconds) -> str:
    if not seconds:
        return "?"
    hours = seconds / 3600
    return f"{hours:.0f} h" if hours >= 1 else f"{seconds / 60:.0f} min"


def _fence(body: str, limit: int = LIMIT) -> str:
    if len(body) > limit:
        body = body[:limit] + "\n… (gekürzt)"
    return f"```\n{body}\n```"


def format_jobs(payload: dict, limit: int = 5) -> str:
    """The most recent jobs, newest first."""
    items = (payload or {}).get("jobs") or []
    if not items:
        return "Keine Trainingsjobs."
    items = sorted(items, key=lambda j: j.get("created_at") or "", reverse=True)
    rows = [f"{'status':<10} {'stage':<12} id"]
    for item in items[:limit]:
        rows.append("{:<10} {:<12} {}".format(
            (item.get("status") or "?")[:10],
            (item.get("stage") or "—")[:12],
            item.get("id") or "?"))
    tail = f"\n({len(items)} insgesamt)" if len(items) > limit else ""
    return _fence("\n".join(rows)) + tail


def format_job(item: dict) -> str:
    """One job: where it is, and why, when that is the interesting part."""
    if not item:
        return "Kein solcher Job."
    lines = [f"id      {item.get('id')}",
             f"status  {item.get('status')}",
             f"stage   {item.get('stage') or '—'}"]
    if item.get("queued_reason"):
        # The line that explains a job sitting still against free memory.
        lines.append(f"wartet  {item['queued_reason']}")
    if item.get("pid"):
        lines.append(f"pid     {item['pid']}")
    progress = item.get("progress") or {}
    if progress:
        lines.append("fort.   " + ", ".join(f"{k}={v}" for k, v in progress.items()))
    metrics = item.get("metrics") or {}
    if metrics:
        lines.append("metrik  " + ", ".join(f"{k}={v}" for k, v in metrics.items()))
    if item.get("published"):
        lines.append(f"publ.   {item['published']}")
    if item.get("error"):
        lines.append(f"fehler  {item['error'][:300]}")
    return _fence("\n".join(lines))


def _classify(proc: dict) -> str:
    """Which of the four things a process holding GPU memory can be.

    The server's ``unaccounted_mib`` means "memory our jobs cannot have", which
    is the right number for a queued job and the wrong one for an alarm: the
    neighbours' RAG service is unavailable to us *and* perfectly well explained.
    Collapsing those two ideas made every ``/atr_gpu`` open with a red banner
    over four gunicorn workers that had been running for 657 hours — and a
    warning that is always on is not a warning.
    """
    if proc.get("registered") or proc.get("own_service"):
        return "ours"          # our engine, or a process a job accounts for
    if proc.get("orphaned"):
        return "orphaned"      # no owner resolvable — the sixteen-hour case
    if not proc.get("service"):
        return "unmanaged"     # someone's shell, e.g. sweep_seed43.sh: real
                               # work, invisible to the API, and 15.6 GB a
                               # queued job will be told it cannot have
    return "foreign"           # a named unit belonging to somebody else


def format_gpu(payload: dict, limit: int = LIMIT) -> str:
    """Cards, and what is holding them.

    Ordered by what needs a person. The banner is reserved for memory nothing
    accounts for; a neighbour's named service is a capacity fact and prints
    without one, because a report that shouts when all is well is one nobody
    reads when it is not.
    """
    cards = (payload or {}).get("cards") or []
    if not cards:
        return "Keine GPU-Daten."
    out: list[str] = []
    alarm = False

    for card in cards:
        procs = card.get("processes") or []
        buckets: dict[str, list[dict]] = {}
        for proc in procs:
            buckets.setdefault(_classify(proc), []).append(proc)

        unexplained = buckets.get("orphaned", []) + buckets.get("unmanaged", [])
        foreign = buckets.get("foreign", [])
        mark = "⚠ " if unexplained else ""
        if unexplained:
            alarm = True

        out.append(
            f"{mark}GPU {card.get('index')} {card.get('name','')}  "
            f"{card.get('memory_used_mib',0)}/{card.get('memory_total_mib',0)} MiB  "
            f"{card.get('utilisation_pct',0)}% util")

        if not unexplained and not foreign:
            out.append(f"    alles zugeordnet ({card.get('service_mib', 0)} MiB "
                       "eigene Dienste)")
            continue

        # Never collapsed: each one is its own incident, and the command line is
        # what turned "what is pid 2771780?" into an answer.
        for proc in sorted(unexplained, key=lambda p: -p.get("used_mib", 0)):
            label = "VERWAIST" if proc.get("orphaned") else "OHNE DIENST"
            out.append("    {:>7} MiB  pid {:<9} {:<8} {:>7}  {}".format(
                proc.get("used_mib", 0), proc.get("pid"),
                proc.get("user") or "?", _hours(proc.get("age_s")), label))
            if proc.get("command"):
                out.append(f"        {proc['command'][:88]}")

        # Collapsed: four workers of one unit are one fact, not four. Printing
        # them separately is how the row that matters gets read past.
        if foreign:
            total = sum(p.get("used_mib", 0) for p in foreign)
            out.append(f"    {total} MiB fremd, für uns nicht verfügbar")
            groups: dict[tuple, list[dict]] = {}
            for proc in foreign:
                groups.setdefault((proc.get("service"), proc.get("user")), []).append(proc)
            for (service, user), group in sorted(
                    groups.items(), key=lambda kv: -sum(p.get("used_mib", 0) for p in kv[1])):
                count = f"{len(group)}× " if len(group) > 1 else ""
                oldest = max((p.get("age_s") or 0) for p in group)
                out.append("    {:>7} MiB  {}{} ({}) {:>9}".format(
                    sum(p.get("used_mib", 0) for p in group),
                    count, service, user or "?", _hours(oldest)))

    if not payload.get("job_attribution_available", True):
        out.append("")
        out.append("Trainer nicht erreichbar — nichts ist einem Job zugeordnet, "
                   "die Speicherzahlen stimmen trotzdem.")
    text = _fence("\n".join(out), limit)
    if alarm:
        text = ("**Speicher, den kein Job und kein Dienst erklärt.**\n" + text)
    return text


# ── both machines ────────────────────────────────────────────────────────────

#: Discord's own ceiling, as opposed to LIMIT, which leaves room for a fence.
MESSAGE_LIMIT = 2000

#: The two views /atr_gpu shows, serving first: "why is recognition slow or
#: refusing a model" is the question asked mid-run, "why is a job waiting" the
#: one asked before. Labelled here and not from the payload's ``host`` — that
#: says ``srv`` and ``dhserver03``, neither of which a reader can place (#436).
#: Names, not function objects, so each call finds the current binding.
GPU_VIEWS = (
    ("Serving — idhefix", "serving_gpu"),
    ("Training — asteraix", "gpu"),
)


async def gpu_views() -> list[tuple[str, dict | None, str | None]]:
    """``[(label, payload, error)]`` for both machines, asked independently.

    One machine that does not answer must not hide the other: the training box
    was off the network for a day on 2026-09-10, and a report that fails whole
    would have hidden the serving cards exactly while someone needed them.
    """
    import asyncio
    import sys
    module = sys.modules[__name__]
    results = await asyncio.gather(
        *(getattr(module, name)() for _, name in GPU_VIEWS),
        return_exceptions=True)
    out = []
    for (label, _), result in zip(GPU_VIEWS, results):
        if isinstance(result, AtrStatusError):
            out.append((label, None, str(result)))
        elif isinstance(result, BaseException):
            out.append((label, None, f"{type(result).__name__}: {result}"))
        else:
            out.append((label, result, None))
    return out


def format_vllm(vllm: dict | None) -> str:
    """One line: what is resident, and the budget a new model has to fit.

    The budget is what decides whether a model start is refused. On 2026-09-21
    a batch died on "GPU 1 has 9742 MB free, needs 15848 MB" while the only card
    Discord could show was the trainer's, idle.
    """
    if not vllm:
        return ""
    residents = vllm.get("residents") or []
    if residents:
        loaded = ", ".join(
            f"{r.get('id')} ({r.get('vram_mb', '?')} MB"
            + (f", {r['residency']}" if r.get("residency") else "") + ")"
            for r in residents)
    else:
        loaded = "nichts geladen"
    line = f"vLLM: {loaded}"
    if vllm.get("budget_mb") is not None:
        line += f" · Budget {vllm['budget_mb']} MiB"
    return line


def _section(label: str, payload: dict | None, error: str | None) -> str:
    head = f"**{label}**"
    if error:
        return f"{head}\n❌ {error}"
    lines = [head]
    vllm_line = format_vllm((payload or {}).get("vllm"))
    if vllm_line:
        lines.append(vllm_line)
    header = "\n".join(lines)
    # Room for the header, the alarm banner format_gpu may prepend, and the fence.
    body = format_gpu(payload, limit=max(200, LIMIT - len(header) - 80))
    return f"{header}\n{body}"


def format_gpu_views(views: list[tuple[str, dict | None, str | None]]) -> list[str]:
    """Messages to post, each within Discord's limit — one if both fit."""
    sections = [_section(*view) for view in views]
    messages: list[str] = []
    for section in sections:
        if messages and len(messages[-1]) + 2 + len(section) <= MESSAGE_LIMIT:
            messages[-1] += "\n\n" + section
        else:
            messages.append(section[:MESSAGE_LIMIT])
    return messages


def format_log(payload: dict, job_id: str) -> str:
    """The tail of the train log — the only place an ETA appears."""
    lines = (payload or {}).get("lines")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not lines:
        return f"Kein Log für {job_id} (die Stufe hat vielleicht noch nicht begonnen)."
    return _fence("\n".join(lines[-12:]))
