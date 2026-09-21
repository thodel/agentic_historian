"""Read the training server from Discord: jobs, and the processes nobody registered.

The bot has the queue's view; asterAIx has the machine's. The two drift apart and
nothing surfaces the gap — three incidents in two weeks were each found by hand
and late, the worst of them a data-loader worker holding 27 530 MiB for sixteen
hours while both cards read 0 % utilisation.

Everything here is **read-only**. Cancelling a job or killing a process is
defensible from Discord and deserves its own confirm flow and its own decision
about who may; reading first, and see what it is actually used for (#414).

The transport is the gateway on :8200, which carries both ``/train/*`` and
``/gpu``.  The trainer runs on asterAIx (dhserver03), the gateway on idhefix
(srv); both are reachable through the same gateway URL.  The key is the one the
bot already holds for recognition, read from the environment, never from a
message.
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
    return await _get("/train/gpu")


async def serving_gpu() -> dict:
    """Cards and vllm state of the serving box (idhefix), via GET /gpu."""
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


def _fence(body: str) -> str:
    if len(body) > LIMIT:
        body = body[:LIMIT] + "\n… (gekürzt)"
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


def format_gpu(payload: dict) -> str:
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
    text = _fence("\n".join(out))
    if alarm:
        text = ("**Speicher, den kein Job und kein Dienst erklärt.**\n" + text)
    return text


def format_serving_gpu(payload: dict) -> str:
    """One-line vllm summary for the serving box: loaded models, VRAM each, budget.

    The full card/process breakdown is in format_gpu (same schema as /train/gpu).
    This line is what a batch runner reads before starting a model.
    """
    vllm = (payload or {}).get("vllm") or {}
    residents = vllm.get("residents") or []
    budget_mb = vllm.get("budget_mb")
    if not residents and budget_mb is None:
        return "vLLM: keine Daten"
    models = [r.get("id", "?") for r in residents]
    model_str = ", ".join(models) if models else "keiner"
    vram_str = ", ".join(f"{r.get('vram_mb', 0)} MB" for r in residents) if residents else ""
    budget_line = f" · Budget {budget_mb} MiB" if budget_mb is not None else ""
    model_line = f" ({vram_str})" if vram_str else ""
    return f"vLLM: {model_str}{model_line}{budget_line}"


def format_log(payload: dict, job_id: str) -> str:
    """The tail of the train log — the only place an ETA appears."""
    lines = (payload or {}).get("lines")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not lines:
        return f"Kein Log für {job_id} (die Stufe hat vielleicht noch nicht begonnen)."
    return _fence("\n".join(lines[-12:]))
