"""Discord bot for the Agentic Historian project.

Provides slash commands to trigger and monitor the A→B→C pipeline
directly from this Discord channel.
"""

import asyncio
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import config
from orchestrator import run_full_pipeline, run_agent_a, run_agent_b, run_agent_c, run_agent_d, run_agent_e, run_hot_folder
from discord import Intents, Option, Interaction, ButtonStyle
import discord
from discord.ui import View
from discord.ext import commands

from loguru import logger

logging.basicConfig(level=logging.INFO)
logger.configure(extra={"extra": {}})

intents = Intents.default()
# `debug_guilds` registers every slash command in that one guild, where Discord
# makes it usable immediately. Without it the commands are global and Discord
# takes up to an hour to roll a *new* one out — during which a correctly
# deployed command is indistinguishable from a missing one (#589). None keeps
# the global behaviour, so a multi-server install is unaffected.
bot = commands.Bot(
    command_prefix="!", intents=intents,
    debug_guilds=[config.DISCORD_GUILD_ID] if config.DISCORD_GUILD_ID else None)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _job_status(phase: str) -> str:
    """Return a one-liner status for a pipeline phase."""
    from reporter import load_progress
    data = load_progress()
    key = f"phase_{phase}"
    if key not in data:
        return "unknown"
    info = data[key]
    return f"[{info.get('status','?')}] {info.get('notes','')}"


# ── Concurrency guard (#15) ──────────────────────────────────────────────────
# The orchestrator/agents are synchronous and can run for minutes. Calling them
# directly inside an async command handler blocks the Discord event loop (the bot
# stops answering heartbeats and other commands). We instead push blocking work
# onto a single FIFO queue drained by one worker that runs each job in a thread
# via asyncio.to_thread (#148). Commands/gate clicks enqueue and await a future,
# so they never race and the event loop stays responsive.

_job_queue: "asyncio.Queue" = asyncio.Queue()
_worker_task = None


# ── Role-based access control (#105, fail-closed #572) ───────────────────────
#
# Fail-CLOSED (#572): an unset role id no longer means "open to everyone". When
# no role is configured a gated command is refused to ordinary members; only a
# Discord server admin / guild owner may run it (so the operator is never locked
# out while the gate is unconfigured), and a warning is logged pointing at the
# fix. Set REQUIRED_DISCORD_ROLE_ID to restrict commands to exactly that role.
import functools


def _caller_role_ids(ctx) -> set:
    """The role ids the invoking member carries (empty set when unknown)."""
    return {role.id for role in getattr(ctx.author, "roles", None) or []}


def _is_guild_admin(ctx) -> bool:
    """A Discord server admin or the guild owner — the floor when no role gate is
    configured, so ordinary members are refused but the operator is not."""
    guild = getattr(ctx, "guild", None)
    author = getattr(ctx, "author", None)
    if guild is not None and getattr(author, "id", None) == getattr(guild, "owner_id", None):
        return True
    perms = getattr(author, "guild_permissions", None)
    return bool(perms is not None
                and (getattr(perms, "administrator", False)
                     or getattr(perms, "manage_guild", False)))


def _authorised(ctx, role_id, *, what: str) -> bool:
    """Whether the caller may run a gated command (fail-closed, #572).

    A configured role id → the caller must hold exactly that role. No role id →
    refuse all but Discord server admins / the guild owner, and warn so the gate
    gets configured.
    """
    if role_id:
        return role_id in _caller_role_ids(ctx)
    logger.warning(
        "[auth] no {} role configured — refusing all but Discord server admins. "
        "Set REQUIRED_DISCORD_ROLE_ID (and REQUIRED_ADMIN_ROLE_ID) to gate by role.",
        what)
    return _is_guild_admin(ctx)


def require_role(func):
    """Decorator: reject non-guild users and callers not authorised by the gate.

    Reads REQUIRED_DISCORD_ROLE_ID; fail-closed when it is unset (#572).
    """
    async def wrapper(ctx, *args, **kwargs):
        if not ctx.guild:
            await ctx.respond("❌ Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True)
            return
        if not _authorised(ctx, getattr(config, "REQUIRED_DISCORD_ROLE_ID", None), what="base"):
            await ctx.respond("⛔ Du hast nicht die erforderliche Rolle für diesen Befehl.", ephemeral=True)
            return
        return await func(ctx, *args, **kwargs)
    # Preserve command metadata so py-cord registers it correctly
    return functools.wraps(func)(wrapper)


def admin_only(func):
    """Decorator: require the admin role (REQUIRED_ADMIN_ROLE_ID) for /update etc.

    Like require_role but reads the separate admin role id. REQUIRED_ADMIN_ROLE_ID
    defaults to REQUIRED_DISCORD_ROLE_ID (config.py), so configuring the base gate
    also gates admin commands; fail-closed when neither is set (#572).
    """
    async def wrapper(ctx, *args, **kwargs):
        if not ctx.guild:
            await ctx.respond("❌ Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True)
            return
        if not _authorised(ctx, getattr(config, "REQUIRED_ADMIN_ROLE_ID", None), what="admin"):
            await ctx.respond("⛔ Dieser Befehl ist admins reserviert.", ephemeral=True)
            return
        return await func(ctx, *args, **kwargs)
    return functools.wraps(func)(wrapper)


def deploy_admin_only(func):
    """Decorator for /update — the deploy command (SEC-5, #576).

    Stricter than :func:`admin_only`. `/update` pulls arbitrary `origin/main`,
    installs requirements and restarts the process with every secret on the host,
    so it requires a **dedicated** admin role that was configured in its own right
    (``config.DEPLOY_ADMIN_ROLE_ID``) and admits nobody otherwise. Unlike
    ``admin_only`` it does NOT inherit the base role and does NOT fall back to the
    Discord-admin / guild-owner bootstrap floor: deploying code with all secrets
    has no safe default operator, so with no dedicated admin role set `/update` is
    refused for everyone. Branch protection on `main` is the real safeguard
    (#565); this only keeps the Discord trigger from widening it.
    """
    async def wrapper(ctx, *args, **kwargs):
        if not ctx.guild:
            await ctx.respond("❌ Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True)
            return
        role_id = getattr(config, "DEPLOY_ADMIN_ROLE_ID", None)
        if not role_id:
            logger.warning(
                "[auth] /update refused: no dedicated REQUIRED_ADMIN_ROLE_ID is set. "
                "A deploy pulls arbitrary main with all secrets and needs its own admin "
                "role (SEC-5, #576) — it does not fall back to the base role or to "
                "server admins.")
            await ctx.respond(
                "⛔ `/update` ist gesperrt: keine eigene Admin-Rolle "
                "(`REQUIRED_ADMIN_ROLE_ID`) konfiguriert. Ein Deploy zieht beliebigen "
                "`main`-Stand mit allen Secrets — das verlangt eine ausdrücklich "
                "gesetzte Rolle, nicht bloss Server-Admins.", ephemeral=True)
            return
        if role_id not in _caller_role_ids(ctx):
            await ctx.respond("⛔ Dieser Befehl ist der Deploy-Admin-Rolle vorbehalten.", ephemeral=True)
            return
        return await func(ctx, *args, **kwargs)
    return functools.wraps(func)(wrapper)


# ── Cost / DoS throttle (SEC-11, #582) ───────────────────────────────────────
#
# All blocking work runs through one FIFO queue with a single worker, so an
# unthrottled member can hold the queue — and the GPUs/LLM — for minutes by
# repeating an expensive command. The role gate (SEC-1) caps WHO can run these;
# this caps HOW OFTEN each person can, with a short per-(user, command) cooldown.
EXPENSIVE_COOLDOWN_S = 60.0
_last_expensive: dict[tuple[int, str], float] = {}


def _cooldown_remaining(ctx, command: str, *, seconds: float | None = None) -> float:
    """Seconds the caller must still wait before re-running *command*, or 0.0 if
    it may run now (recording the attempt when it is allowed)."""
    window = EXPENSIVE_COOLDOWN_S if seconds is None else seconds
    uid = getattr(getattr(ctx, "author", None), "id", 0)
    now = time.time()
    remaining = window - (now - _last_expensive.get((uid, command), 0.0))
    if remaining > 0:
        return remaining
    _last_expensive[(uid, command)] = now
    return 0.0


async def _cooldown_block(ctx, command: str) -> bool:
    """Tell the caller to wait and return True if *command* is on cooldown for
    them; otherwise record the run and return False. Call right after defer()."""
    wait = _cooldown_remaining(ctx, command)
    if wait > 0:
        await ctx.followup.send(
            f"⏳ Zu schnell — bitte {int(wait) + 1}s warten, bevor `/{command}` erneut läuft.")
        return True
    return False


# ── Entity-index cache (SEC-11, #582) ────────────────────────────────────────
#
# /entity rebuilt the index from data/outputs on EVERY call, walking the tree
# each time. Cache it briefly so a burst of look-ups does not re-walk repeatedly.
ENTITY_INDEX_TTL_S = 60.0
_entity_index_cache: dict = {"index": None, "at": 0.0}


def _get_entity_index():
    """The entity index, rebuilt at most once per ENTITY_INDEX_TTL_S (SEC-11)."""
    import entity_index
    now = time.time()
    if (_entity_index_cache["index"] is None
            or now - _entity_index_cache["at"] > ENTITY_INDEX_TTL_S):
        _entity_index_cache["index"] = entity_index.build_index(config.OUTPUTS_DIR)
        _entity_index_cache["at"] = now
    return _entity_index_cache["index"]


# ── Error reporting (SEC-14, #585) ───────────────────────────────────────────
#
# A raw exception posted into the channel can carry internal URLs, filesystem
# paths, gateway responses or (with SEC-10) file contents. This posts a generic
# line and sends the traceback to the log, where loguru records which handler
# raised it. Use it in place of `❌ Error: {e}`.
async def _report_error(ctx, exc: Exception, *, note: str = "",
                        ephemeral: bool = False) -> None:
    logger.exception("[cmd] {}", exc)
    msg = note or "Das hat nicht geklappt"
    try:
        await ctx.followup.send(f"❌ {msg} — Details siehe Log.", ephemeral=ephemeral)
    except Exception:                                # the report must never raise
        pass


async def _worker() -> None:
    """Single consumer: run queued blocking jobs serially, one thread at a time.

    A failing job resolves its caller's future with the exception and the worker
    keeps going (one bad job never stalls the queue).
    """
    while True:
        func, args, kwargs, fut = await _job_queue.get()
        try:
            result = await asyncio.to_thread(func, *args, **kwargs)
            if not fut.done():
                fut.set_result(result)
        except Exception as e:  # noqa: BLE001 — surfaced to the caller's future
            if not fut.done():
                fut.set_exception(e)
        finally:
            _job_queue.task_done()


def _ensure_worker() -> None:
    """Start the single worker on the running loop (idempotent, lazy).

    Also (re)starts it if the previous task is done or bound to a different
    (e.g. closed) loop.
    """
    global _worker_task
    loop = asyncio.get_running_loop()
    if (_worker_task is None or _worker_task.done()
            or _worker_task.get_loop() is not loop):
        _worker_task = loop.create_task(_worker())


async def _run_blocking(ctx, func, *args, **kwargs):
    """Enqueue a blocking job and await its result.

    Jobs are serialised through one worker (no per-user race, nothing dropped);
    the event loop stays responsive because the blocking call runs in a thread.
    """
    _ensure_worker()
    fut = asyncio.get_running_loop().create_future()
    await _job_queue.put((func, args, kwargs, fut))
    ahead = _job_queue.qsize()
    if ahead > 1 and ctx is not None:
        try:
            await ctx.followup.send(f"⏳ In Warteschlange (Position {ahead})…")
        except Exception:
            pass
    return await fut


async def _run_with_board(ctx, channel, func, *args, **kwargs):
    """Run a pipeline job with a live Discord progress board (#289, V-3).

    Builds a ProgressReporter bound to ``channel`` (the invoking channel for a
    command, or VERBOSE_PROGRESS_CHANNEL_ID for a background run), passes its
    thread-safe ``emit`` into the orchestrator as ``on_phase``, and always closes
    it. Flag off / no channel → no reporter, ``on_phase`` unset, behaviour
    byte-identical. Reporting never affects the job's result or errors.
    """
    import progress_reporter
    doc_id = Path(str(args[0])).stem if args else "run"
    reporter = progress_reporter.make_reporter(channel, doc_id)
    if reporter is not None:
        kwargs = {**kwargs, "on_phase": reporter.emit}
    try:
        return await _run_blocking(ctx, func, *args, **kwargs)
    finally:
        if reporter is not None:
            await reporter.close()


# ── Hot-folder watch ────────────────────────────────────────────────────────

_HOT_QUEUE: asyncio.Queue = asyncio.Queue()
_observer = None


class _HotFolderHandler(FileSystemEventHandler):
    """Debounced watchdog handler: enqueues (action, stem, path) to _HOT_QUEUE.

    - New file (stem unknown) → action="run"
    - Updated file (stem matches a RunState) → action="reprocess"
    - Ignores non-watched extensions.
    Debounce: a burst of events for the same path collapses to one enqueue after
    HOT_FOLDER_DEBOUNCE_SEC seconds of quiescence (#227).
    """

    def __init__(self):
        super().__init__()
        self._pending: dict[str, float] = {}  # stem → last_event_time

    def _enqueue(self, action: str, stem: str, path: Path) -> None:
        asyncio.get_event_loop().call_soon_threadsafe(
            _HOT_QUEUE.put_nowait, (action, stem, path)
        )

    def _stem_and_action(self, path: Path) -> tuple[str, str] | None:
        if path.suffix.lower() not in config.WATCHED_EXTENSIONS:
            return None
        stem = path.stem
        # Known run state → reprocess (pick up where it left off);
        # no run state → run (start fresh from model_select).
        from runstate import RunState
        if RunState.exists(stem):
            return (stem, "reprocess")
        return (stem, "run")

    def _dispatch(self, event, path: Path):
        if event.event_type not in ("created", "modified"):
            return
        result = self._stem_and_action(path)
        if result is None:
            return
        stem, action = result
        # Debounce: record event time; schedule dispatch after DEBOUNCE_SEC
        import time
        when = time.monotonic()
        self._pending[stem] = when
        delay = config.HOT_FOLDER_DEBOUNCE_SEC

        def _fire(when=when):
            # Only fire if no newer event has arrived for this stem
            last = self._pending.get(stem, 0)
            if last == when:
                self._pending.pop(stem, None)
                self._enqueue(action, stem, path)
        threading.Timer(delay, _fire).start()

    def on_created(self, event):
        self._dispatch(event, Path(event.src_path))

    def on_modified(self, event):
        self._dispatch(event, Path(event.src_path))


def _ensure_hot_watch() -> None:
    """Start (or re-start) the hot-folder watchdog observer."""
    global _observer
    if not config.ENABLE_HOT_FOLDER_WATCH:
        return
    if _observer is not None:
        _observer.stop()
        _observer.join(timeout=2)
    handler = _HotFolderHandler()
    _observer = Observer()
    _observer.schedule(handler, str(config.HOT_FOLDER), recursive=False)
    _observer.start()
    logger.info(f"[hot-watch] started on {config.HOT_FOLDER}")


async def _process_hot_queue() -> None:
    """Background task: poll _HOT_QUEUE and dispatch to _run_blocking."""
    while True:
        try:
            action, stem, path = await asyncio.wait_for(_HOT_QUEUE.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            if action == "run":
                logger.info(f"[hot-watch] new file → run_full_pipeline({stem})")
                _chan = (bot.get_channel(config.VERBOSE_PROGRESS_CHANNEL_ID)
                         if config.VERBOSE_PROGRESS_CHANNEL_ID else None)
                await _run_with_board(None, _chan, run_full_pipeline, path)
            else:  # reprocess
                logger.info(f"[hot-watch] updated file → reprocess({stem})")
                import ingest as _ingest
                # Re-run all downstream stages for the new image (agent_a onward)
                await _run_blocking(
                    None, _ingest.reprocess, stem,
                    stages=[],
                )
        except Exception as e:
            logger.exception(f"[hot-watch] error processing {stem}: {e}")



async def _atr_watch_loop() -> None:
    """Poll the training server and announce what changed (#418).

    The deciding is in ``atr_watch.decide``, which is pure and tested offline;
    this is the part that cannot be — a socket, a clock and a channel.

    The state is saved **after** the messages are away, so a crash in between
    repeats an announcement rather than losing one. A repeat is noise; a loss is
    the eleven-hour failure nobody heard about, which is the thing this exists
    to prevent.
    """
    import atr_status
    import atr_watch

    channel = bot.get_channel(config.ATR_WATCH_CHANNEL_ID)
    if channel is None:
        logger.warning("[atr-watch] channel {} not visible to the bot — not started",
                       config.ATR_WATCH_CHANNEL_ID)
        return
    state = atr_watch.load_state(config.ATR_WATCH_STATE)
    logger.info("[atr-watch] every {:.0f}s into #{} (mention={}, seeded={})",
                config.ATR_WATCH_INTERVAL_S, config.ATR_WATCH_CHANNEL_ID,
                bool(config.ATR_WATCH_MENTION), state.seeded)

    while True:
        try:
            jobs_payload = await atr_status.jobs()
            gpu_payload = await atr_status.gpu()
        except (atr_status.AtrStatusError, Exception) as exc:  # noqa: BLE001
            # A blip is expected — the gateway is restarted for every deploy of
            # the serving stack — so this is logged and not announced. A *held*
            # outage is different: on 2026-09-10 the box left the network with a
            # 33-hour run on it, and the first draft of this loop would never have
            # said so. note_unreachable draws that line at half an hour.
            logger.info("[atr-watch] gateway unreachable: {}", exc)
            try:
                notice, state = atr_watch.note_unreachable(state, time.time())
                if notice is not None:
                    await channel.send(notice.render(config.ATR_WATCH_MENTION)[:1900])
                atr_watch.save_state(config.ATR_WATCH_STATE, state)
            except Exception as inner:  # noqa: BLE001
                logger.warning("[atr-watch] outage notice failed: {}", inner)
            await asyncio.sleep(config.ATR_WATCH_INTERVAL_S)
            continue

        try:
            back, state = atr_watch.note_reachable(state, time.time())
            if back is not None:
                await channel.send(back.render(config.ATR_WATCH_MENTION)[:1900])
            announcements, state = atr_watch.decide(state, jobs_payload, gpu_payload)
            for item in announcements:
                await channel.send(item.render(config.ATR_WATCH_MENTION)[:1900])
            atr_watch.save_state(config.ATR_WATCH_STATE, state)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[atr-watch] announcing failed: {}", exc)

        await asyncio.sleep(config.ATR_WATCH_INTERVAL_S)


async def _credential_watch_loop() -> None:
    """Poll the mailbox credentials and announce when they break (#589).

    The deciding is in ``credential_watch.decide``, which is pure and tested
    offline; this is the part that cannot be — a socket, a clock and a channel.

    The state is saved **after** the message is away, so a crash in between
    repeats an announcement rather than losing one. The same trade as
    ``_atr_watch_loop``, for the same reason: a repeat is noise, and a loss is
    the two hours of 2026-10-09.
    """
    import credential_watch

    channel = bot.get_channel(config.CREDENTIAL_WATCH_CHANNEL_ID)
    if channel is None:
        logger.warning("[credwatch] channel {} not visible to the bot — not started",
                       config.CREDENTIAL_WATCH_CHANNEL_ID)
        return
    state = credential_watch.load_state()
    logger.info("[credwatch] every {:.0f}s into #{} (blocked={!r})",
                config.CREDENTIAL_WATCH_INTERVAL_S,
                config.CREDENTIAL_WATCH_CHANNEL_ID, state.blocked)

    while True:
        try:
            from utils import switchdrive
            # No target folder: this watches the credentials, and a folder that
            # was renamed is not a credential problem. `/pull` finds that out
            # when somebody actually pulls.
            probe = await asyncio.to_thread(switchdrive.preflight, None)
            notice, state = credential_watch.decide(state, probe, time.time())
            if notice is not None:
                await channel.send(str(notice)[:1900])
            credential_watch.save_state(state)
        except Exception as exc:  # noqa: BLE001
            # The watcher may never be the reason the bot stops working.
            logger.warning("[credwatch] poll failed: {}", exc)
        await asyncio.sleep(config.CREDENTIAL_WATCH_INTERVAL_S)


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.slash_command(name="status", description="Overall pipeline status")
async def status(ctx):
    await ctx.defer()
    from reporter import generate_report
    report = generate_report()
    await ctx.followup.send(report)


@bot.slash_command(
    name="search",
    description="Federated person search across the Knowledge Hub (HLS/HBLS/KF/EOS)",
)
async def search_cmd(ctx, query: Option(str, "Name/Person to search", required=True)):
    # Read-only federated query; the MCP calls are async I/O so we await directly.
    await ctx.defer()
    try:
        from agents import search_agent
        resp = await search_agent.search(query, limit=20)
        await ctx.followup.send(search_agent.format_response(resp))
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(
    name="entity",
    description="Look up an entity in the local index (documents, authority links)",
)
async def entity_cmd(ctx, name: Option(str, "Entity name to look up", required=True)):
    # Thin shell over entity_index (#33/#224): (re)build from data/outputs, look up.
    await ctx.defer()
    try:
        import entity_index
        index = _get_entity_index()              # SEC-11 (#582): cached, not rebuilt per call
        entry = entity_index.lookup(index, name)
        if entry is None:
            suggestions = entity_index.suggest(index, name)
            if suggestions:
                hint = ", ".join(f"`{s}`" for s in suggestions)
                await ctx.followup.send(f"Keine Entität «{name}» gefunden. Meinten Sie: {hint}?")
            else:
                await ctx.followup.send(f"Keine Entität «{name}» gefunden.")
            return
        site_base = entity_index.pages_site_base() if config.ENABLE_GITHUB_PUBLISH else None
        await ctx.followup.send(entity_index.format_entity(entry, site_base=site_base))
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(
    name="route",
    description="Show the Gate-1 routing card for a document (correct metadata, re-route HTR)",
)
@require_role
async def route_cmd(ctx, doc_id: Option(str, "Document id", required=True)):
    await ctx.defer()
    try:
        from utils import document_id
        bad = document_id.slug_violation(doc_id)
        if bad:
            await ctx.followup.send(
                f"❌ Ungültige Dokument-ID `{doc_id}` — {bad}.")
            return
        import routing_card
        import persistent_views
        import ingest
        from runstate import RunState
        state = RunState.load_or_new(doc_id)
        runners = ingest.build_stage_runners(state) if config.AUTO_RESUME_AFTER_GATE else None
        view = routing_card.build_view(state, runners=runners)
        msg = await ctx.followup.send(routing_card.render_card(state), view=view)
        # Persist the message id so the view survives a bot restart (#150).
        if msg is not None:
            persistent_views.store_message_id(state, "gate1", msg.id)
    except Exception as e:
        # SEC-10 (#581): a load error's text can carry the start of the file it
        # failed on — a run-state read oracle if posted. Log the detail, tell the
        # channel nothing but that it failed. (Traversal is already blocked up
        # front by the slug check / SEC-2.)
        logger.exception("[route] {}: {}", doc_id, e)
        await ctx.followup.send("❌ Konnte die Routing-Karte nicht laden — Details siehe Log.")


@bot.slash_command(
    name="votes",
    description="Show the Gate-2 voting card for a doc whose engines disagreed (no-merge)",
)
@require_role
async def votes_cmd(ctx, doc_id: Option(str, "Document id", required=True)):
    """Posts the Gate-2 voting card for a document that went through a no-merge
    decision (#300/#313). At high engine disagreement the pipeline selects by
    model-MATCH score — a prior on fit-to-source, not output quality (on BAT_664
    the 0.20-scored engine read better than the 0.80 one) — so the reading is a
    human call. #313's recording half stored the candidates as ``paths``; this
    surfaces them for a vote. A vote overrides the auto-pick through the existing
    voting → apply_path_choice path, and the message id is persisted so the buttons
    survive a bot restart (#150).

    Pull-based on purpose: the headless grouped pipeline runs in a worker thread,
    and posting an interactive button view from off-loop is the risky path. The
    historian sees the no-merge on the live board (#289) and runs this.
    """
    await ctx.defer()
    try:
        import path_compare
        import persistent_views
        import ingest
        from runstate import RunState
        # Three distinct states, and they must not be conflated (#351). load_or_new
        # invents an empty state for any string, so an unknown doc_id used to be
        # reported as "die Engines waren sich einig" — a positive claim about a run
        # that never happened, which sends the historian looking in the wrong place.
        if not RunState.exists(doc_id):
            hint = ""
            near = RunState.suggest_doc_ids(doc_id)
            if near:
                hint = "\nMeintest du: " + ", ".join(f"`{n}`" for n in near) + "?"
            await ctx.followup.send(
                f"❓ Kein Lauf mit der ID `{doc_id}` gefunden.{hint}")
            return
        state = RunState.load_or_new(doc_id)
        paths = state.artifacts.get("paths") or {}
        if len([t for t in paths.values() if (t or "").strip()]) < 2:
            await ctx.followup.send(
                f"Keine Abstimmung für `{doc_id}` — die Engines waren sich einig "
                f"(oder der Lauf lief ohne No-Merge-Entscheidung durch)."
            )
            return
        runners = ingest.build_stage_runners(state) if config.AUTO_RESUME_AFTER_GATE else None
        view = path_compare.build_view(state, paths, runners=runners)
        msg = await ctx.followup.send(path_compare.render_vote_card(state, paths), view=view)
        if msg is not None:
            persistent_views.store_message_id(state, "gate2", msg.id)
    except Exception as e:
        # SEC-10 (#581): see /route — never post a raw load error into the channel.
        logger.exception("[votes] {}: {}", doc_id, e)
        await ctx.followup.send("❌ Konnte die Abstimmungskarte nicht laden — Details siehe Log.")


@bot.slash_command(
    name="votes_queue",
    description="Offene Gate-2-Abstimmungen, nach Informationsgehalt sortiert")
@require_role
async def votes_queue_cmd(
    ctx,
    anzahl: Option(int, "Wie viele Seiten zeigen (Standard 10)", required=False),
):
    """Which pending Gate-2 pages are worth a vote (#398).

    Read-only over the run states, the preference log and the vote log — no new
    storage. The ranking and its explanation live in `votes_queue`, which renders
    a list of messages, so the command is a loop over sends and the output is
    testable without a Discord client (as with `/find` and `atr_status`).

    Each entry names `/votes <doc_id>` rather than a jump link: the gate card's
    message id is on the run state, but its channel and guild are not, and a
    fabricated link would look like a working one.
    """
    import votes_queue

    await ctx.defer(ephemeral=True)
    queue = votes_queue.build_queue(int(anzahl or votes_queue.TOP_N))
    for message in votes_queue.format_queue(queue):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(
    name="campaign",
    description="Stimm-Kampagne auf einem Bestand starten (Budget in Karten)")
@admin_only
async def campaign_cmd(
    ctx,
    bestand: Option(str, "doc_id-Präfix, z. B. BAT_664 — leer = ganzes Korpus",
                    required=False),
    budget: Option(int, "Wie viele Karten posten (Standard 10)", required=False),
):
    """A bounded vote campaign on one holding (#399).

    Admin-gated because it posts up to `MAX_BUDGET` cards and opens a thread.
    Every Discord touch is handed to `campaign.post_cards` as a seam, so the
    budget, the spacing and the bookkeeping are the same code the offline tests
    exercise.

    The plan is shown before anything is posted: a budget in *cards* can be many
    more decisions, because one Gate-2 card carries every page of its document.
    """
    import campaign as camp
    import ingest
    import path_compare
    import persistent_views

    await ctx.defer()
    the_plan = camp.plan(bestand or "", budget or camp.DEFAULT_BUDGET)
    for message in camp.format_plan(the_plan):
        await ctx.followup.send(message)
    if not the_plan.cards:
        return

    async def _open_thread(name):
        return await ctx.channel.create_thread(
            name=f"Abstimmung {bestand or 'Korpus'} · {name}"[:100],
            type=discord.ChannelType.public_thread)

    async def _post(thread, state, paths, text):
        runners = (ingest.build_stage_runners(state)
                   if config.AUTO_RESUME_AFTER_GATE else None)
        view = path_compare.build_view(state, paths, runners=runners)
        target = thread or ctx.followup
        msg = await target.send(text, view=view)
        if msg is not None:
            # Persisted so the buttons survive a bot restart (#150) — a campaign
            # is answered over hours, which is longer than a bot uptime.
            persistent_views.store_message_id(state, "gate2", msg.id)

    started = await camp.post_cards(
        the_plan, post=_post, open_thread=_open_thread,
        started_by=str(getattr(getattr(ctx, "author", None), "id", "") or ""),
        channel_id=int(getattr(getattr(ctx, "channel", None), "id", 0) or 0) or None)
    await ctx.followup.send(
        f"🎯 Kampagne `{started.campaign_id}` läuft — Bericht mit "
        f"`/campaign_status`.")


@bot.slash_command(
    name="campaign_status",
    description="Abdeckungsbericht einer Stimm-Kampagne")
@require_role
async def campaign_status_cmd(
    ctx,
    bestand: Option(str, "Bestand der Kampagne (leer = neueste)", required=False),
    beenden: Option(bool, "Kampagne abschliessen und Zahlen einfrieren",
                    required=False),
):
    """The campaign's coverage report (#399).

    Completion is derived from the preference log through G1's `decided_pages`,
    never from a counter this command keeps — a second source of truth about
    whether a page was voted would drift the first time a card was answered
    outside the thread.
    """
    import campaign as camp

    await ctx.defer(ephemeral=True)
    found = camp.latest(bestand or None)
    if found is None:
        await ctx.followup.send(
            "Keine Kampagne gefunden — `/campaign <bestand>` startet eine.",
            ephemeral=True)
        return
    if beenden:
        camp.end(found)
    prog = camp.progress(found)
    for message in camp.format_report(found, prog):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(
    name="votes_stats",
    description="Was die Gate-2-Stimmen bisher gemessen haben")
@require_role
async def votes_stats_cmd(
    ctx,
    bereich: Option(str, "Welcher Report", required=False,
                    choices=["abdeckung", "auswahl", "stärke", "routing",
                             "alles"]),
):
    """The vote-derived measurements, on demand (#369).

    All four reports existed — #154's routing stats, #333's coverage, #334's
    selection agreement, #335's engine strength — and no command surfaced any of
    them; only `/agent_e` posted the routing embed as a side effect of running a
    whole meta report. #369's decision ("not on the card, but available to
    whoever deliberately asks") needs the second half to exist, which is this.

    `stärke` is the one report that must never be pushed: showing which engine
    leads before the next vote would make #334 and #335 measure the display
    rather than the historian.
    """
    import routing_report

    await ctx.defer(ephemeral=True)
    want = (bereich or "alles").lower()
    reports = {
        "abdeckung": routing_report.format_coverage_stats,
        "auswahl": routing_report.format_selection_stats,
        "stärke": routing_report.format_strength_stats,
        "routing": routing_report.format_routing_stats,
    }
    chosen = list(reports) if want == "alles" else [want]
    for name in chosen:
        render = reports.get(name)
        if render is None:
            await ctx.followup.send(f"❓ Unbekannter Bereich `{name}`.",
                                    ephemeral=True)
            continue
        try:
            text = render()
        except Exception as e:                      # noqa: BLE001
            text = f"❌ `{name}` nicht verfügbar: {e}"
        # Discord caps a message at 2000 chars and these reports grow with the
        # bucket count, so a long one is split rather than silently truncated by
        # the API.
        for chunk in _chunks(text):
            await ctx.followup.send(chunk, ephemeral=True)


def _chunks(text: str, limit: int = 1900) -> list[str]:
    """Split on line boundaries so a report never loses its tail to Discord's
    2000-char cap. A single over-long line is cut, because the alternative is
    sending nothing."""
    out: list[str] = []
    current = ""
    for line in (text or "").splitlines() or [""]:
        line = line if len(line) <= limit else line[:limit - 1] + "…"
        if len(current) + len(line) + 1 > limit:
            out.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    out.append(current)
    return [c for c in out if c.strip()] or [""]


@bot.slash_command(
    name="reprocess",
    description="Re-process a document after correcting criteria or stages",
)
@require_role
async def reprocess_cmd(
    ctx,
    doc_id: Option(str, "Document id to reprocess", required=True),
    changes: Option(
        str,
        "field:value pairs or stage names (e.g. century:14 agent_a)",
        required=False,
        default="",
    ),
):
    """Reprocess a document: field:value pairs invalidate criteria; bare names force stage dirty."""
    await ctx.defer()
    from utils import document_id
    bad = document_id.slug_violation(doc_id)
    if bad:
        await ctx.followup.send(f"❌ Ungültige Dokument-ID `{doc_id}` — {bad}.")
        return
    from runstate import _INVALIDATION
    fields: list[str] = []
    stages: list[str] = []
    bad: list[str] = []
    STAGES = ("model_select", "kraken", "vlm", "reconcile",
              "agent_a", "agent_b", "agent_c", "agent_d", "agent_e")
    for token in (changes or "").strip().split():
        if not token:
            continue
        if ":" in token:
            field, _sep, _val = token.partition(":")
            if field in _INVALIDATION:
                fields.append(field)
            else:
                bad.append(token)
        else:
            if token in STAGES:
                stages.append(token)
            else:
                bad.append(token)
    if bad:
        await ctx.followup.send(
            f"❌ Unbekannte Felder/Stages: {', '.join(bad)}\n"
            f"Gültige Felder: {sorted(_INVALIDATION)}\n"
            f"Gültige Stages: {STAGES}"
        )
        return
    if not fields and not stages:
        await ctx.followup.send(
            "⚠️ Nichts zu tun — gib field:value Paare oder Stage-Namen an.\n"
            "Beispiele: `century:14 script:miniscule` · `agent_a` · `century:14 agent_b`"
        )
        return
    try:
        import ingest as _ingest
        result = await _run_blocking(ctx, _ingest.reprocess, doc_id,
                                     fields=fields or None,
                                     stages=stages or None)
        if result is None:
            return
        ran = result.get("ran", [])
        skipped = result.get("skipped", [])
        errors = result.get("errors", [])
        parts = []
        if ran:
            parts.append(f"✅ Gelaufen: {', '.join(ran)}")
        if skipped:
            parts.append(f"⏭️ Übersprungen: {', '.join(skipped)}")
        if errors:
            parts.append(f"❌ Fehler: {', '.join(errors)}")
        await ctx.followup.send("\n".join(parts) or "✅ Nichts zu tun.")
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(name="run", description="Run the full A→B→C pipeline on a file")
@require_role
async def run_pipeline(
    ctx,
    filename: Option(str, "Filename in data/hot_folder/", required=True),
):
    await ctx.defer()
    fp = (config.HOT_FOLDER / filename).resolve()
    if not fp.is_relative_to(config.HOT_FOLDER.resolve()):
        await ctx.followup.send(f"❌ Ungültiger Pfad: {filename} — Zugriff ausserhalb des erlaubten Ordners.")
        return
    if not fp.exists():
        await ctx.followup.send(f"❌ File not found: {filename}")
        return
    try:
        result = await _run_with_board(ctx, ctx.channel, run_full_pipeline, fp)
        if result is None:
            return
        doc_id = result.get("doc_id", Path(filename).stem)
        msg = (
            f"✅ Pipeline fertig für `{filename}`\n"
            f"Doc-ID: `{doc_id}`\n"
            f"QA-Score: {result.get('a_meta', {}).get('qa_score', '?')}\n"
            f"Entitäten: {len(result.get('entities', {}).get('entities', []))}\n"
            f"Errors: {len(result.get('errors', []))}\n"
            f"→ Gate 1: `/route {doc_id}`"
        )
        await ctx.followup.send(msg)
    except Exception as e:
        logger.exception("Pipeline error")
        await _report_error(ctx, e)


@bot.slash_command(name="run_agent_a", description="Run Agent A (HTR) only")
@require_role
async def run_agent_a_cmd(
    ctx,
    filename: Option(str, "Filename in data/hot_folder/", required=True),
):
    await ctx.defer()
    fp = (config.HOT_FOLDER / filename).resolve()
    if not fp.is_relative_to(config.HOT_FOLDER.resolve()):
        await ctx.followup.send(f"❌ Ungültiger Pfad: {filename} — Zugriff ausserhalb des erlaubten Ordners.")
        return
    if not fp.exists():
        await ctx.followup.send(f"❌ File not found: {filename}")
        return
    try:
        result = await _run_blocking(ctx, run_agent_a, fp)
        if result is None:
            return
        await ctx.followup.send(
            f"✅ Agent A fertig\n"
            f"QA: {result.get('qa_score', 0):.2f} \n"
            f"File: {result.get('path','')}"
        )
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(name="hotfolder", description="Process all files in the hot folder")
@require_role
async def hotfolder(ctx):
    await ctx.defer()
    if await _cooldown_block(ctx, "hotfolder"):      # SEC-11 (#582)
        return
    try:
        results = await _run_blocking(ctx, run_hot_folder)
        if results is None:
            return
        ok = [r for r in results if "error" not in r]
        errs = [r for r in results if "error" in r]
        msg = f"✅ Verarbeitet: {len(ok)} Dateien"
        if errs:
            msg += f"\n❌ Fehler: {len(errs)}"
        await ctx.followup.send(msg)
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(
    name="env_reload",
    description="Admin: .env-Dateien neu lesen, ohne Neustart — und gleich prüfen")
@admin_only
async def env_reload_cmd(ctx):
    """Re-read the `.env` files and show whether it worked (#589).

    `config.py` reads them at import, so a corrected password did not reach a
    running bot — the restart was half the fix on 2026-10-09. This does the
    other half and then runs the preflight, so success is *shown* rather than
    claimed: a reload that reports success and changed nothing is the defect
    this command exists to avoid, and `config.reload_env` documents the
    measurement that makes it possible at all.

    Key **names** only, never values: this posts into a channel.
    """
    from utils import switchdrive
    import webdav_probe

    await ctx.defer(ephemeral=True)
    result = await _run_blocking(ctx, config.reload_env)
    if result is None:
        return
    lines = ["🔄 **`.env` neu gelesen**"]
    lines.append(f"Geändert: {', '.join(f'`{k}`' for k in result['changed'])}"
                 if result["changed"] else "Geändert: nichts")
    if result["unchanged"]:
        lines.append(f"Unverändert: {len(result['unchanged'])} Key(s)")
    if result["from_environment"]:
        lines.append(
            "_Aus der Prozess-Umgebung und daher **nicht** von hier aus "
            "änderbar: " + ", ".join(f"`{k}`" for k in result["from_environment"])
            + " — dort gewinnt die Umgebung über jede `.env`-Datei (#106)._")
    await ctx.followup.send("\n".join(lines), ephemeral=True)

    found = await _run_blocking(ctx, switchdrive.preflight, None)
    if found is None:
        return
    for message in webdav_probe.format_probe(found):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(
    name="pull_preflight",
    description="SwitchDrive getrennt prüfen: Konfiguration, Host, Zugangsdaten, Pfad")
@require_role
async def pull_preflight_cmd(
    ctx,
    folder: Option(str, "Ordner, z. B. missiven (leer = nur bis zur Wurzel)",
                   required=False, default=None),
):
    """Which of the four layers is actually broken (#563).

    Read-only; downloads nothing. The auth layer asks about the WebDAV **root**,
    not the target folder, because a PROPFIND on the target answers 401 before
    it looks at the path — so a dead app password and a missing folder used to
    arrive as the same sentence.
    """
    from utils import switchdrive

    await ctx.defer(ephemeral=True)
    found = await _run_blocking(ctx, switchdrive.preflight, folder)
    if found is None:
        return
    import webdav_probe
    for message in webdav_probe.format_probe(found):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(name="pull", description="Pull a SwitchDrive folder into the hot folder and process it")
@require_role
async def pull_cmd(
    ctx,
    folder: Option(str, "SwitchDrive folder (relative to your SwitchDrive root)", required=False, default=None),
    recursive: Option(bool, "Descend into subfolders", required=False, default=False),
):
    await ctx.defer()
    from utils import switchdrive
    if not switchdrive.is_configured():
        await ctx.followup.send(
            "❌ SwitchDrive not configured — set SWITCHDRIVE_USER / SWITCHDRIVE_PASS "
            "(app password) in .env.gpustack."
        )
        return
    remote = folder or config.SWITCHDRIVE_REMOTE_DIR
    # Skip already-processed folders (unless re-processing is explicitly requested via
    # /pull_folder). This matches pull_folder_cmd dedup behaviour.
    already = switchdrive.load_processed()
    if remote in already:
        await ctx.followup.send(
            f"⏭️ `{remote}` already processed — use /pull_folder with `reprocess:=true` "
            "to re-pull it."
        )
        return
    try:
        files = await _run_blocking(ctx, switchdrive.pull_folder, remote, config.HOT_FOLDER, recursive)
        if files is None:
            return
        if not files:
            await ctx.followup.send(f"📂 No images/PDFs found in SwitchDrive `{remote}`.")
            return
        await ctx.followup.send(f"⬇️ Pulled {len(files)} file(s) from `{remote}` — processing…")
        results = await _run_blocking(ctx, run_hot_folder)
        if results is None:
            return
        ok = [r for r in results if "error" not in r]
        errs = [r for r in results if "error" in r]
        msg = f"✅ Verarbeitet: {len(ok)} Dateien"
        if errs:
            msg += f"\n❌ Fehler: {len(errs)}"
        await ctx.followup.send(msg)
        # Mark as processed so subsequent /pull calls skip this folder (dedup).
        switchdrive.mark_processed(remote)
    except Exception as e:
        logger.exception("pull error")
        # Translate the status into the thing to change (#563). A bare
        # `received 401 (Unauthorized)` sends the reader looking at the network,
        # the endpoint and the folder before the credentials — that order cost
        # an afternoon on 2026-10-09.
        from utils import switchdrive as _sd
        _why = _sd.explain(e, remote or "")
        await ctx.followup.send(
            f"❌ Error: {e}" + (f"\n\n{_why}\n_Details: `/pull_preflight`._"
                               if _why else ""))


@bot.slash_command(
    name="pull_folder",
    description="Process each SwitchDrive subfolder as ONE multi-page document",
)
@require_role
async def pull_folder_cmd(
    ctx,
    folder: Option(str, "Parent folder on SwitchDrive (default: hot folder)", required=False, default=None),
    reprocess: Option(bool, "Reprocess orders already done", required=False, default=False),
):
    await ctx.defer()
    from utils import switchdrive
    import ingest

    if not switchdrive.is_configured():
        await ctx.followup.send(
            "❌ SwitchDrive not configured — set SWITCHDRIVE_USER / SWITCHDRIVE_PASS in .env.gpustack."
        )
        return

    parent = folder or config.SWITCHDRIVE_REMOTE_DIR

    try:
        # Ingestion logic lives in ingest.run_switchdrive_orders (#33) so any UI
        # is a thin shell; the bot only renders the result.
        res = await _run_blocking(ctx, ingest.run_switchdrive_orders, parent, reprocess)
        if res is None:
            return
        msg = (
            f"✅ Orders verarbeitet: {len(res['done'])}\n"
            f"⏭️ Übersprungen (bereits erledigt): {len(res['skipped'])}\n"
        )
        if res["empty"]:
            msg += f"📂 Leer (keine Bilder): {len(res['empty'])}\n"
        if res["errors"]:
            msg += f"❌ Fehler: {len(res['errors'])}\n"
        if res["done"]:
            msg += "\n• " + "\n• ".join(res["done"][:10])
        if res["errors"]:
            msg += "\n⚠️ " + "; ".join(res["errors"][:5])
        await ctx.followup.send(msg)
    except Exception as e:
        logger.exception("pull_folder error")
        # Translate the status into the thing to change (#563). A bare
        # `received 401 (Unauthorized)` sends the reader looking at the network,
        # the endpoint and the folder before the credentials — that order cost
        # an afternoon on 2026-10-09.
        from utils import switchdrive as _sd
        _why = _sd.explain(e, folder or "")
        await ctx.followup.send(
            f"❌ Error: {e}" + (f"\n\n{_why}\n_Details: `/pull_preflight`._"
                               if _why else ""))


@bot.slash_command(name="agent_d", description="Run Agent D corpus analysis")
@require_role
async def agent_d_cmd(
    ctx,
    corpus_name: Option(str, "Corpus name", required=False, default="default"),
):
    await ctx.defer()
    if await _cooldown_block(ctx, "agent_d"):        # SEC-11 (#582)
        return
    try:
        from agents import corpus_analysis
        try:
            corpus_analysis.corpus_out_dir(corpus_name)
        except ValueError:
            await ctx.followup.send(f"❌ Ungültiger Korpusname `{corpus_name}`.")
            return
        result = await _run_blocking(ctx, run_agent_d, corpus_name)
        if result is None:
            return
        msg = (
            f"✅ Agent D fertig (`{corpus_name}`)\n"
            f"Dokumente: {result.get('doc_count', 0)}\n"
            f"Tokens: {result.get('stats', {}).get('total_tokens', 0)}"
        )
        if result.get("voyant_url"):
            # SEC-12 (#583): the corpus text was uploaded to Voyant and this link is
            # publicly shareable — say so where it is posted.
            msg += (f"\nVoyant: {result['voyant_url']}"
                    "\n⚠️ Dieser Link ist öffentlich teilbar — der Korpustext liegt auf Voyant.")
        await ctx.followup.send(msg)
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(name="agent_e", description="Run Agent E — meta report")
@require_role
async def agent_e_cmd(ctx):
    await ctx.defer()
    if await _cooldown_block(ctx, "agent_e"):        # SEC-11 (#582)
        return
    try:
        result = await _run_blocking(ctx, run_agent_e)
        if result is None:
            return
        msg = (
            f"✅ Agent E fertig\n"
            f"Dateien: {result.get('token_usage',{}).get('total_files',0)}\n"
            f"Geschätzte Tokens: {result.get('token_usage',{}).get('estimated_tokens',0):,}"
        )
        await ctx.followup.send(msg)
        # Also send routing stats if available (HITL-4b, #154)
        embed = routing_stats_embed()
        if embed is not None:
            await ctx.followup.send(embed=embed)
    except Exception as e:
        await _report_error(ctx, e)


@bot.slash_command(name="progress", description="Show phase progress")
async def progress(ctx):
    await ctx.defer()
    lines = ["**Phase Status**\n"]
    for phase in range(10):
        lines.append(f"Phase {phase}: {_job_status(phase)}")
    await ctx.followup.send("\n".join(lines))


# ── ATR training server (#414) ───────────────────────────────────────────────
#
# Read-only. The bot has the queue's view, the machines have theirs, and the
# two drift apart with nothing to surface the gap. Actions — cancelling a job,
# killing a process — are a separate decision with their own confirm flow.
#
# All four are ephemeral: they are operational chatter, and a channel that fills
# with them stops being read.

async def _atr(ctx, coro, formatter, *args):
    """Await one gateway call and post its formatted answer, or say why not."""
    import atr_status
    try:
        payload = await coro
    except atr_status.AtrStatusError as exc:
        # AtrStatusError messages are curated (SEC-6 validation, 401, and the
        # generic gateway messages from _get) — safe to show.
        await ctx.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception as exc:                        # pragma: no cover — defensive
        # SEC-14 (#585): an arbitrary exception is not safe to echo.
        await _report_error(ctx, exc, ephemeral=True)
        return
    await ctx.followup.send(formatter(payload, *args), ephemeral=True)


@bot.slash_command(
    name="find",
    description="Stellen im Korpus finden — nach Bedeutung, nicht nach Wortlaut")
@require_role
async def find_cmd(
    ctx,
    query: Option(str, "Wonach suchen? Natürliche Sprache.", required=True),
    typ: Option(str, "Entitätstyp", required=False,
                choices=["CARE_ACTOR", "CARE_ACTION", "SOCIAL_GROUP", "ROLE"]),
    bestand: Option(str, "Nur in diesem Bestand", required=False),
    seite: Option(int, "Seite der Trefferliste", required=False),
):
    """Semantic search over the passage index (#397).

    The work is in `passage_find`, which renders a list of messages — so the
    command is a loop over sends and the rendering is testable without a
    Discord client, as with `atr_status.format_gpu_views`.
    """
    import passage_find

    await ctx.defer(ephemeral=True)
    found = passage_find.find(query, entity_type=typ or None,
                              bestand=bestand or None,
                              page=int(seite or 1))
    for message in passage_find.format_found(found):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(name="atr_jobs", description="Training jobs on asteraix")
@require_role
async def atr_jobs_cmd(ctx):
    import atr_status
    await ctx.defer(ephemeral=True)
    # summary: this view shows status, stage and id, not the submitted request
    # object of every job (serving-atr-inference#107).
    await _atr(ctx, atr_status.jobs(summary=True), atr_status.format_jobs)


@bot.slash_command(name="atr_job", description="One training job in detail")
@require_role
async def atr_job_cmd(ctx, job_id: Option(str, "Job id", required=True)):
    import atr_status
    await ctx.defer(ephemeral=True)
    await _atr(ctx, atr_status.job(job_id), atr_status.format_job)


@bot.slash_command(
    name="atr_gpu",
    description="GPU memory on both ATR machines, and what holds it")
@require_role
async def atr_gpu_cmd(ctx):
    # Both machines since the 16.09. split (#439): idhefix serves, asteraix
    # trains. Each is asked on its own, so one that is down does not hide the
    # other — gpu_views() never raises, it files the failure under its section.
    import atr_status
    await ctx.defer(ephemeral=True)
    views = await atr_status.gpu_views()
    for message in atr_status.format_gpu_views(views):
        await ctx.followup.send(message, ephemeral=True)


@bot.slash_command(name="atr_progress", description="Tail of a training job's log")
@require_role
async def atr_progress_cmd(ctx, job_id: Option(str, "Job id", required=True)):
    import atr_status
    await ctx.defer(ephemeral=True)
    await _atr(ctx, atr_status.log(job_id), atr_status.format_log, job_id)


# ── Boot ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Bot ready as {bot.user}")
    # Re-bind HITL gate views so clicks on pre-restart messages still work (#150).
    try:
        import persistent_views
        persistent_views.register_persistent_views(bot)
    except Exception as e:
        logger.warning(f"[persist] view registration failed: {e}")
    # P3-3: back-online confirmation after a self-restart
    try:
        import updater
        marker = updater.read_marker()
        if marker:
            chan = bot.get_channel(int(marker.channel_id))
            if chan:
                try:
                    msg = await chan.fetch_message(int(marker.message_id))
                    await msg.edit(
                        content=(
                            f"✅ Back online on `{marker.target_sha}` "
                            f"(updated by {marker.requester})"
                        )
                    )
                except Exception:
                    await chan.send(
                        f"✅ Back online on `{marker.target_sha}` "
                        f"(updated by {marker.requester})"
                    )
            updater.clear_marker()
            logger.info(f"[update] cleared back-online marker for {marker.requester}")
    except Exception as e:
        logger.warning(f"[update] on_ready marker check failed: {e}")

    # Start hot-folder watch + background queue processor (#227).
    try:
        _ensure_hot_watch()
        asyncio.create_task(_process_hot_queue())
    except Exception as e:
        logger.warning(f"[hot-watch] start failed: {e}")

    # Announce training outcomes and unexplained GPU memory (#418). Off unless a
    # channel was chosen; see ENABLE_ATR_WATCH.
    if config.ENABLE_ATR_WATCH:
        try:
            asyncio.create_task(_atr_watch_loop())
        except Exception as e:
            logger.warning(f"[atr-watch] start failed: {e}")

    # Say when the mailbox credentials break, instead of letting somebody find
    # out through a failed pull (#589). Off unless a channel was chosen.
    if config.ENABLE_CREDENTIAL_WATCH:
        try:
            asyncio.create_task(_credential_watch_loop())
        except Exception as e:
            logger.warning(f"[credwatch] start failed: {e}")


# ── Update command (P3-3, #248) ───────────────────────────────────────────────

import hmac
import hashlib
import time as _time
from discord import ButtonStyle
from discord.ui import button as _button, View, Button

# Pending update confirmations: token → {channel_id, message_id, requester, target_sha, stage}
_PENDING_UPDATES: dict[str, dict] = {}
_UPDATE_TOKEN_TTL = 300  # 5 minutes to confirm


def _make_token() -> str:
    """Short-lived HMAC token for update Confirm buttons."""
    secret = config.DISCORD_BOT_TOKEN.encode()
    ts = str(_time.time_ns())
    mac = hmac.new(secret, ts.encode(), hashlib.sha256).hexdigest()
    return mac[:16]


async def _do_apply_update(
    token: str,
    channel_id: int,
    message_id: int,
    requester: str,
    target_sha: str,
) -> None:
    """Run apply_update off the event loop; called from the job queue.

    On success: edits the original message → "⏳ Restarting…", writes the
    marker, logs, then sys.exit(0) so systemd (P3-2) restarts the bot.
    On failure: edits to the rollback message, stays running.
    """
    import updater as _updater_mod
    import sys as _sys

    # Phase 1: pulling
    try:
        chan = bot.get_channel(channel_id)
        msg = await chan.fetch_message(message_id)
        await msg.edit(content="⏳ Pulling changes…")
    except Exception:
        pass

    result = _updater_mod.apply_update()

    if result.get("ok"):
        from_sha = result.get("from_sha", "?")
        to_sha = result.get("to_sha", "?")
        try:
            chan = bot.get_channel(channel_id)
            msg = await chan.fetch_message(message_id)
            await msg.edit(content=f"⏳ Restarting on `{to_sha}`…")
        except Exception:
            pass
        logger.info(f"[update] {requester}: {from_sha} → {to_sha}")
        _updater_mod.write_marker(
            str(channel_id), str(message_id), requester, to_sha
        )
        # Remove from pending BEFORE exit so on_ready doesn't double-post
        _PENDING_UPDATES.pop(token, None)
        _sys.exit(0)
    else:
        stage = result.get("stage", "unknown")
        error = result.get("error", "")
        pre_sha = result.get("pre_sha", "?")
        try:
            chan = bot.get_channel(channel_id)
            msg = await chan.fetch_message(message_id)
            await msg.edit(
                content=(
                    f"❌ Update failed at `{stage}` — rolled back to `{pre_sha}`\n"
                    f"Error: {error[:200]}"
                )
            )
        except Exception:
            pass
        logger.warning(f"[update] {requester}: failed at {stage}: {error[:200]}")
        _PENDING_UPDATES.pop(token, None)


class _ConfirmView(View):
    """Confirm / Cancel for a pending update."""

    def __init__(self, token: str, requester: str, target_sha: str, *, timeout: float = _UPDATE_TOKEN_TTL):
        super().__init__(timeout=timeout)
        self.token = token
        self.requester = requester
        self.target_sha = target_sha

    async def interaction_check(self, interaction, /) -> bool:
        if str(interaction.user.id) != self.requester:
            await interaction.response.send_message(
                "⛔ Nur derjenige, der /update aufgerufen hat, kann bestätigen.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="✅ Confirm", style=ButtonStyle.success, custom_id="update:confirm")
    async def confirm(self, button: Button, interaction: Interaction):
        await interaction.response.defer(thinking=True, ephemeral=False)
        info = _PENDING_UPDATES.get(self.token)
        if info is None:
            await interaction.followup.send(
                "⚠️ Diese Anfrage ist abgelaufen — bitte /update erneut aufrufen.",
                ephemeral=True,
            )
            return

        # Check queue busy
        if not _job_queue.empty():
            await interaction.followup.send(
                "⛔ Die Job-Warteschlange ist noch aktiv — ein Neustart würde "
                "einen laufenden /run unterbrechen. Bitte später erneut versuchen.",
                ephemeral=True,
            )
            return

        # Enqueue the update (run off event loop so it can sys.exit)
        info["stage"] = "queued"
        await _job_queue.put((
            _do_apply_update,
            (
                self.token,
                int(info["channel_id"]),
                int(info["message_id"]),
                info["requester"],
                self.target_sha,
            ),
            {},
            bot.loop.create_future(),
        ))

    @discord.ui.button(label="✖ Cancel", style=ButtonStyle.secondary, custom_id="update:cancel")
    async def cancel(self, button: Button, interaction: Interaction):
        info = _PENDING_UPDATES.pop(self.token, {})
        try:
            await interaction.response.edit_message(
                content=f"❌ Update abgebrochen — bleibe auf `{info.get('from_sha', '?')}`.",
                view=None,
            )
        except Exception:
            await interaction.response.edit_message(
                content="❌ Update abgebrochen.",
                view=None,
            )


@bot.slash_command(name="update", description="Admin: check for and apply bot updates")
@deploy_admin_only
async def update_cmd(ctx):
    """Check for updates; show commit list with Confirm/Cancel if behind main."""
    await ctx.defer()

    import updater

    status = await _run_blocking(ctx, updater.fetch_status)
    if not status.get("ok"):
        await ctx.followup.send(f"❌ Status-Check fehlgeschlagen: {status.get('error', '?')}")
        return

    if status["ahead"] == 0:
        sha = status["current_sha"][:12]
        await ctx.followup.send(f"✅ Bereits auf dem neusten Stand — `{sha}`.")
        return

    # Build commit list
    lines = [
        f"📦 `{status['current_sha'][:12]}` → `{status['target_sha'][:12]}` — "
        f"{status['ahead']} commit(s):"
    ]
    for c in status["commits"]:
        lines.append(f"  • `{c['sha'][:7]}` {c['subject'][:72]}")
    lines.append("")
    lines.append("Bestätigen klicken zum Aktualisieren — der Bot wird anschliessend neu gestartet.")

    token = _make_token()
    target_sha = status["target_sha"][:12]
    requester_id = str(ctx.author.id)

    _PENDING_UPDATES[token] = {
        "channel_id": str(ctx.channel.id),
        "message_id": "?",  # filled after send
        "requester": requester_id,
        "target_sha": target_sha,
        "from_sha": status["current_sha"][:12],
    }

    view = _ConfirmView(token=token, requester=requester_id, target_sha=target_sha)
    msg = await ctx.followup.send("\n".join(lines), view=view)

    # Store actual message id for on_ready marker (P3-3 step 4)
    _PENDING_UPDATES[token]["message_id"] = str(msg.id)


# ── /mcp_propose (#229): probe an MCP source → reviewed PR (never hot-load) ────
_PENDING_PROPOSALS: dict = {}


class _McpProposeView(View):
    """Confirm → open a PR for a probed MCP source (the running federation is
    never touched; only a reviewable PR is created)."""

    def __init__(self, token: str, requester: str, *, timeout: float = _UPDATE_TOKEN_TTL):
        super().__init__(timeout=timeout)
        self.token = token
        self.requester = requester

    async def interaction_check(self, interaction, /) -> bool:
        if str(interaction.user.id) != self.requester:
            await interaction.response.send_message(
                "⛔ Nur wer /mcp_propose aufgerufen hat, kann bestätigen.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="✅ PR erstellen", style=ButtonStyle.success,
                       custom_id="mcp_propose:confirm")
    async def confirm(self, button: Button, interaction: Interaction):
        await interaction.response.defer(thinking=True)
        info = _PENDING_PROPOSALS.pop(self.token, None)
        if info is None:
            await interaction.followup.send(
                "⚠️ Anfrage abgelaufen — /mcp_propose erneut aufrufen.", ephemeral=True)
            return
        import mcp_propose
        # PR creation is blocking HTTP → run off the event loop.
        result = await _run_blocking(None, mcp_propose.propose,
                                     info["name"], info["url"], info["report"])
        if result.get("ok"):
            await interaction.followup.send(
                f"✅ PR erstellt: {result['pr_url']}\nBitte reviewen, mergen und deployen.")
        else:
            await interaction.followup.send(result.get("error") or "❌ Vorschlag fehlgeschlagen.")

    @discord.ui.button(label="✖ Abbrechen", style=ButtonStyle.secondary,
                       custom_id="mcp_propose:cancel")
    async def cancel(self, button: Button, interaction: Interaction):
        _PENDING_PROPOSALS.pop(self.token, None)
        await interaction.response.edit_message(content="❌ Vorschlag abgebrochen.", view=None)


@bot.slash_command(
    name="mcp_propose",
    description="Probe an MCP source and open a PR to add it (reviewed, never hot-loaded)",
)
@require_role
async def mcp_propose_cmd(
    ctx,
    name: Option(str, "Short source key, e.g. 'ssrq'", required=True),
    url: Option(str, "MCP endpoint URL (https)", required=True),
):
    # Thin shell (#33): probe (async I/O), show the report, and only open the PR
    # on an explicit Confirm — never hot-load into the running federation.
    await ctx.defer()
    try:
        from utils import mcp_probe
        import mcp_propose
        # SEC-7 (#578): scheme + SSRF host check BEFORE the probe connects, so a
        # URL pointing at an internal/private target is refused and never probed
        # with the bot's credentials.
        refusal = mcp_probe.url_guard(url)
        if refusal:
            await ctx.followup.send(refusal)
            return
        report = await mcp_probe.probe(url)
        err = mcp_propose.check_guardrails(name, url, report)
        if err:
            await ctx.followup.send(err)
            return
        token = _make_token()
        requester_id = str(ctx.author.id)
        _PENDING_PROPOSALS[token] = {"name": name, "url": url, "report": report,
                                     "requester": requester_id}
        view = _McpProposeView(token=token, requester=requester_id)
        await ctx.followup.send(mcp_propose.format_report(name, url, report), view=view)
    except Exception as e:
        # SEC-7/SEC-14: log the detail, don't echo a raw exception (which can carry
        # an internal host/IP) into the channel.
        logger.warning("[mcp_propose] probe/propose failed: {}", e)
        await ctx.followup.send("❌ Die Probe ist fehlgeschlagen — Details siehe Log.")


def main() -> None:
    # Ensure all data directories exist before starting.
    # Called once here (single entry point) rather than at module import
    # so the package is importable without side-effects.
    config.ensure_dirs()
    missing = config.check_config()
    if missing:
        print(f"Missing config keys: {missing}")
    # Fail-closed gate (#572): without a configured role, gated commands are
    # refused to everyone but Discord server admins. Warn loudly at startup so a
    # shared/open-server deployment is restricted to a role on purpose.
    if not config.REQUIRED_DISCORD_ROLE_ID:
        logger.warning(
            "[auth] REQUIRED_DISCORD_ROLE_ID is not set — gated commands are "
            "refused to all but Discord server admins. Set it to grant a role.")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()

