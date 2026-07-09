"""Discord bot for the Agentic Historian project.

Provides slash commands to trigger and monitor the A→B→C pipeline
directly from this Discord channel.
"""

import asyncio
import logging
import threading
from pathlib import Path

from watchdog.events import FileSystemEventHandler

import config
from orchestrator import run_full_pipeline, run_agent_a, run_agent_b, run_agent_c, run_agent_d, run_agent_e, run_hot_folder
from discord import Intents, Option
from discord.ext import commands

from loguru import logger

logging.basicConfig(level=logging.INFO)
logger.configure(extra={"extra": {}})

intents = Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


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


# ── Role-based access control (#105) ────────────────────────────────────────
def require_role(func):
    """Decorator: reject non-guild users and users without the configured role."""
    async def wrapper(ctx, *args, **kwargs):
        if not ctx.guild:
            await ctx.respond("❌ Dieser Befehl funktioniert nur in einem Discord-Server.", ephemeral=True)
            return
        allowed_role_id = getattr(config, "REQUIRED_DISCORD_ROLE_ID", None)
        if allowed_role_id:
            author_role_ids = {role.id for role in ctx.author.roles}
            if allowed_role_id not in author_role_ids:
                await ctx.respond("⛔ Du hast nicht die erforderliche Rolle für diesen Befehl.", ephemeral=True)
                return
        return await func(ctx, *args, **kwargs)
    # Preserve command metadata so py-cord registers it correctly
    import functools
    return functools.wraps(func)(wrapper)


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
                await _run_blocking(None, run_full_pipeline, path)
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
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(
    name="route",
    description="Show the Gate-1 routing card for a document (correct metadata, re-route HTR)",
)
async def route_cmd(ctx, doc_id: Option(str, "Document id", required=True)):
    await ctx.defer()
    try:
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
        await ctx.followup.send(f"❌ Error: {e}")


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
        await ctx.followup.send(f"❌ Fehler: {e}")


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
        result = await _run_blocking(ctx, run_full_pipeline, fp)
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
        await ctx.followup.send(f"❌ Error: {e}")


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
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(name="hotfolder", description="Process all files in the hot folder")
async def hotfolder(ctx):
    await ctx.defer()
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
        await ctx.followup.send(f"❌ Error: {e}")


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
        await ctx.followup.send(f"❌ Error: {e}")


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
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(name="agent_d", description="Run Agent D corpus analysis")
async def agent_d_cmd(
    ctx,
    corpus_name: Option(str, "Corpus name", required=False, default="default"),
):
    await ctx.defer()
    try:
        result = await _run_blocking(ctx, run_agent_d, corpus_name)
        if result is None:
            return
        msg = (
            f"✅ Agent D fertig (`{corpus_name}`)\n"
            f"Dokumente: {result.get('doc_count', 0)}\n"
            f"Tokens: {result.get('stats', {}).get('total_tokens', 0)}"
        )
        if result.get("voyant_url"):
            msg += f"\nVoyant: {result['voyant_url']}"
        await ctx.followup.send(msg)
    except Exception as e:
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(name="agent_e", description="Run Agent E — meta report")
async def agent_e_cmd(ctx):
    await ctx.defer()
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
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(name="progress", description="Show phase progress")
async def progress(ctx):
    await ctx.defer()
    lines = ["**Phase Status**\n"]
    for phase in range(10):
        lines.append(f"Phase {phase}: {_job_status(phase)}")
    await ctx.followup.send("\n".join(lines))


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
    # Start hot-folder watch + background queue processor (#227).
    try:
        _ensure_hot_watch()
        asyncio.create_task(_process_hot_queue())
    except Exception as e:
        logger.warning(f"[hot-watch] start failed: {e}")


def main() -> None:
    # Ensure all data directories exist before starting.
    # Called once here (single entry point) rather than at module import
    # so the package is importable without side-effects.
    config.ensure_dirs()
    missing = config.check_config()
    if missing:
        print(f"Missing config keys: {missing}")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()

