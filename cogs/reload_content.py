# cogs/reload_content.py
from __future__ import annotations

import os
import json
from pathlib import Path

import discord
from discord.ext import commands
from discord import app_commands


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PRESENCE_FILE = os.getenv("AURA_PRESENCE_FILE", "AURA.PRESENCE.v2.json")
HOURLIES_FILE = os.getenv("AURA_HOURLIES_FILE", "AURA.HOURLIES.v2.json")


def _load_json_lines(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # supports {"presence":[...]} or just ["..."]
        if isinstance(data, dict):
            # pick first list-like value
            for v in data.values():
                if isinstance(v, list):
                    return [str(x).strip() for x in v if str(x).strip()]
            return []
        return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        return []


class ReloadContent(commands.Cog):
    """Reload presence/hourly JSON files without restarting."""

    def __init__(self, bot: commands.Bot | discord.Client):
        self.bot = bot

    #
    # Commands
    #
    @app_commands.command(name="content_status", description="Show how many presence/hourly lines are loaded.")
    async def content_status(self, interaction: discord.Interaction):
        pres = len(getattr(self.bot, "presence_pool", []))
        hours = len(getattr(self.bot, "hourly_pool", []))
        msg = f"Presence: **{pres}** lines\nHourlies: **{hours}** lines"
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="presence_reload", description="Reload presence JSON (admin only).")
    @app_commands.checks.has_permissions(administrator=True)
    async def presence_reload(self, interaction: discord.Interaction):
        path = DATA_DIR / PRESENCE_FILE
        lines = _load_json_lines(path)
        if lines:
            self.bot.presence_pool = lines
            await interaction.response.send_message(
                f"Reloaded **{len(lines)}** presence lines from `{PRESENCE_FILE}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Failed to load presence from `{PRESENCE_FILE}`.", ephemeral=True
            )

    @app_commands.command(name="hourly_reload", description="Reload hourly JSON (admin only).")
    @app_commands.checks.has_permissions(administrator=True)
    async def hourly_reload(self, interaction: discord.Interaction):
        path = DATA_DIR / HOURLIES_FILE
        lines = _load_json_lines(path)
        if lines:
            self.bot.hourly_pool = lines
            # Reset “used today” so the fresh set can be used immediately if you want
            if hasattr(self.bot, "used_hourly_today"):
                self.bot.used_hourly_today = []
            await interaction.response.send_message(
                f"Reloaded **{len(lines)}** hourly lines from `{HOURLIES_FILE}`.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Failed to load hourlies from `{HOURLIES_FILE}`.", ephemeral=True
            )


async def setup(bot: commands.Bot | discord.Client):
    """Attach cog AND explicitly add app_commands to the tree."""
    cog = ReloadContent(bot)
    await bot.add_cog(cog)

    # Explicitly register the slash commands on the bot's CommandTree.
    # This is the key piece that makes them show up.
    bot.tree.add_command(cog.content_status)
    bot.tree.add_command(cog.presence_reload)
    bot.tree.add_command(cog.hourly_reload)

    # Optional log so you see it in Render
    logger = getattr(bot, "logger", None)
    if logger:
        logger.info("Reload commands registered on tree.")
