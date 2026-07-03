"""Discord bot for the Agentic Historian project.

Provides slash commands to trigger and monitor the A→B→C pipeline
directly from this Discord channel.
"""

import asyncio
import logging
from pathlib import Path

import config
from orchestrator import run_full_pipeline, run_full_pipeline_group, run_agent_a, run_agent_b, run_agent_c, run_agent_d, run_agent_e, run_hot_folder
from discord import Intents, Option
from discord.ext import commands

from loguru import logger

# Load configuration (discords token from .env)
config.ensure_dirs()

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
# stops answering heartbeats and other commands). We instead run blocking work in
# a worker thread via asyncio.to_thread, and allow only one job per user at a time.

_active_runs: set[int] = set()


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


async def _run_blocking(ctx, func, *args, **kwargs):
    """Run a blocking orchestrator call off the event loop, one job per user.

    Returns the function result, or None if the user already has a job running
    (in which case a rejection message has been sent).
    """
    uid = ctx.author.id
    if uid in _active_runs:
        await ctx.followup.send(
            "⏳ Du hast bereits einen laufenden Job. Bitte warte, bis er fertig ist."
        )
        return None
    _active_runs.add(uid)
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    finally:
        _active_runs.discard(uid)


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.slash_command(name="status", description="Overall pipeline status")
async def status(ctx):
    await ctx.defer()
    from reporter import generate_report
    report = generate_report()
    await ctx.followup.send(report)


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
        msg = (
            f"✅ Pipeline fertig für `{filename}`\n"
            f"QA-Score: {result.get('transcription_qa', '?')}\n"
            f"Entitäten: {len(result.get('entities', {}).get('entities', []))}\n"
            f"Errors: {len(result.get('errors', []))}"
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
    import shutil

    if not switchdrive.is_configured():
        await ctx.followup.send(
            "❌ SwitchDrive not configured — set SWITCHDRIVE_USER / SWITCHDRIVE_PASS in .env.gpustack."
        )
        return

    parent = folder or config.SWITCHDRIVE_REMOTE_DIR

    def _work():
        # Each immediate subfolder = one order. If there are none, treat the parent
        # itself as a single order (loose images directly in it).
        orders = switchdrive.list_subdirs(parent) or [parent]
        already = set() if reprocess else switchdrive.load_processed()
        res = {"done": [], "skipped": [], "empty": [], "errors": []}
        for order in orders:
            order_id = order.strip("/").replace("/", "__")
            if order_id in already:
                res["skipped"].append(order_id)
                continue
            staging = config.HOT_FOLDER / "_orders" / order_id
            try:
                files = switchdrive.pull_folder(order, staging, recursive=True)
                if not files:
                    res["empty"].append(order_id)
                    continue
                doc_id = Path(order.rstrip("/")).name or order_id
                run_full_pipeline_group(doc_id, files)
                switchdrive.mark_processed(order_id)
                res["done"].append(f"{doc_id} ({len(files)}p)")
            except Exception as e:
                logger.exception(f"pull_folder error for {order_id}")
                res["errors"].append(f"{order_id}: {e}")
            finally:
                shutil.rmtree(staging, ignore_errors=True)
        return res

    try:
        res = await _run_blocking(ctx, _work)
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


def main() -> None:
    missing = config.check_config()
    if missing:
        print(f"Missing config keys: {missing}")
    bot.run(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()

