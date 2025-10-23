# cogs/reload_content.py
import os
import json
import discord
from discord import app_commands
from discord.ext import commands

PRESENCE_ENV = "AURA_PRESENCE_FILE"
HOURLIES_ENV = "AURA_HOURLIES_FILE"

def _load_text_lines(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("items", [])
        # allow either [{ "text": "..."}, ...] or ["...", "..."]
        out: list[str] = []
        for it in items:
            if isinstance(it, dict) and "text" in it:
                out.append(str(it["text"]))
            elif isinstance(it, str):
                out.append(it)
        return [s.strip() for s in out if s and s.strip()]
    except Exception:
        return []

class ReloadContent(commands.Cog):
    """Admin-only slash commands to reload presence/hourlies JSON at runtime."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="content_status", description="Show counts loaded from JSON.")
    async def content_status(self, interaction: discord.Interaction):
        presence_count = len(getattr(self.bot, "presence_pool", []))
        hourly_count = len(getattr(self.bot, "hourly_pool", []))
        msg = (
            f"**Presence lines:** {presence_count}\n"
            f"**Hourly lines:** {hourly_count}"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="presence_reload", description="Reload presence pool from JSON.")
    async def presence_reload(self, interaction: discord.Interaction):
        path = os.getenv(PRESENCE_ENV, "data/AURA.PRESENCE.v2.json")
        lines = _load_text_lines(path)
        self.bot.presence_pool = lines
        self.bot.used_presence_today = []
        await interaction.response.send_message(
            f"Presence reloaded: {len(lines)} lines.", ephemeral=True
        )

    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.command(name="hourly_reload", description="Reload hourly pool from JSON.")
    async def hourly_reload(self, interaction: discord.Interaction):
        path = os.getenv(HOURLIES_ENV, "data/AURA.HOURLIES.v2.json")
        lines = _load_text_lines(path)
        self.bot.hourly_pool = lines
        self.bot.used_hourly_today = []
        await interaction.response.send_message(
            f"Hourlies reloaded: {len(lines)} lines.", ephemeral=True
        )

    @presence_reload.error
    @hourly_reload.error
    async def admin_error(self, interaction: discord.Interaction, error: Exception):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("Admin only.", ephemeral=True)
        else:
            await interaction.response.send_message("Error.", ephemeral=True)

async def setup(bot: commands.Bot):
    # NOTE: discord.py 2.x extension entrypoint
    await bot.add_cog(ReloadContent(bot))
