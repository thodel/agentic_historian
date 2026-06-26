"""Discord bot for the Agentic Historian project.

Provides slash commands to trigger and monitor the A→B→C pipeline
directly from this Discord channel.
"""

import logging
from pathlib import Path

import config
from orchestrator import run_full_pipeline, run_agent_a, run_agent_b, run_agent_c, run_agent_d, run_agent_e, run_hot_folder
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


# ── Commands ─────────────────────────────────────────────────────────────────

@bot.slash_command(name="status", description="Overall pipeline status")
async def status(ctx):
    await ctx.defer()
    from reporter import generate_report
    report = generate_report()
    await ctx.followup.send(report)


@bot.slash_command(name="run", description="Run the full A→B→C pipeline on a file")
async def run_pipeline(
    ctx,
    filename: Option(str, "Filename in data/hot_folder/", required=True),
):
    await ctx.defer()
    fp = config.HOT_FOLDER / filename
    if not fp.exists():
        await ctx.followup.send(f"❌ File not found: {filename}")
        return
    try:
        result = run_full_pipeline(fp)
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
async def run_agent_a_cmd(
    ctx,
    filename: Option(str, "Filename in data/hot_folder/", required=True),
):
    await ctx.defer()
    fp = config.HOT_FOLDER / filename
    if not fp.exists():
        await ctx.followup.send(f"❌ File not found: {filename}")
        return
    try:
        result = run_agent_a(fp)
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
        results = run_hot_folder()
        ok = [r for r in results if "error" not in r]
        errs = [r for r in results if "error" in r]
        msg = f"✅ Verarbeitet: {len(ok)} Dateien"
        if errs:
            msg += f"\n❌ Fehler: {len(errs)}"
        await ctx.followup.send(msg)
    except Exception as e:
        await ctx.followup.send(f"❌ Error: {e}")


@bot.slash_command(name="agent_d", description="Run Agent D corpus analysis")
async def agent_d_cmd(
    ctx,
    corpus_name: Option(str, "Corpus name", required=False, default="default"),
):
    await ctx.defer()
    try:
        result = run_agent_d(corpus_name)
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
        result = run_agent_e()
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
    bot.run(config.DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

