"""Discord bot skeleton for the Agentic Historian project.

This file sets up a minimal bot using discord.py. It registers a single
slash command `/status` that replies with a simple acknowledgment. The
full command set will be added later as the implementation progresses.
"""

import os
import logging
from discord import Intents
from discord.ext import commands

# Load configuration from environment (e.g., DISCORD_TOKEN)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

logging.basicConfig(level=logging.INFO)

intents = Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Bot is ready.")


@bot.slash_command(name="status", description="Check the bot status")
async def status(ctx):
    await ctx.respond("Agentic Historian bot is online and ready.")


def main() -> None:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN not set in environment")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()

