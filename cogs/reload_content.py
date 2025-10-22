# cogs/reload_content.py
import os, json
from pathlib import Path
import discord
from discord import app_commands
from discord.ext import commands
import importlib

# We import your running main module so we can update its globals
# (PRESENCE_LINES / HOURLY_LINES) and refresh the bot's pools.
core = importlib.import_module("main")  # main.py is already loaded by Render

DATA_DIR = Path(__file__).parents[1] / "data"

def _load_items_from_json(filename: str):
    fp = DATA_DIR / filename
    with fp.open("r", encoding="utf-8") as f:
        obj = json.load(f)
    items = obj.get("items", [])
    if not isinstance(items, list):
        raise ValueError("items[] must be a list")
    texts = [it["text"] for it in items if isinstance(it, dict) and isinstance(it.get("text"), str)]
    if not texts:
        raise ValueError("no valid 'text' strings in items[]")
    return texts

class ReloadContent(commands.Cog):
    """Reload presence/hourly content from JSON without redeploying."""

    def __init__(self, bot: discord.Client):
        self.bot = bot

    # ---- helpers ----
    async def _admin_gate(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        if not perms or not perms.administrator:
            await interaction.response.send_message("Admins only.", ephemeral=True)
            return False
        return True

    # ---- /presence_reload ----
    @app_commands.command(name="presence_reload", description="Reload presence lines from JSON (admins only).")
    async def presence_reload(self, interaction: discord.Interaction):
        if not await self._admin_gate(interaction): return
        filename = os.getenv("AURA_PRESENCE_FILE", "AURA.PRESENCE.v2.json")
        try:
            lines = _load_items_from_json(filename)
            # update globals in main.py
            core.PRESENCE_LINES = lines
            # refresh today's pools on the running bot
            bot = self.bot
            bot.used_presence_today = []
            bot.presence_pool = lines.copy()
            await interaction.response.send_message(f"Presence reloaded: {len(lines)} lines.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Reload failed: {e}", ephemeral=True)

    # ---- /hourly_reload ----
    @app_commands.command(name="hourly_reload", description="Reload hourly lines from JSON (admins only).")
    async def hourly_reload(self, interaction: discord.Interaction):
        if not await self._admin_gate(interaction): return
        filename = os.getenv("AURA_HOURLIES_FILE", "AURA.HOURLIES.v2.json")
        try:
            lines = _load_items_from_json(filename)
            # update globals in main.py
            core.HOURLY_LINES = lines
            # refresh today's pools on the running bot
            bot = self.bot
            bot.used_hourly_today = []
            bot.hourly_pool = lines.copy()
            await interaction.response.send_message(f"Hourly reloaded: {len(lines)} lines.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Reload failed: {e}", ephemeral=True)

    # ---- /content_status ----
    @app_commands.command(name="content_status", description="Show how many presence/hourly lines are active.")
    async def content_status(self, interaction: discord.Interaction):
        pres = len(getattr(core, "PRESENCE_LINES", []))
        hour = len(getattr(core, "HOURLY_LINES", []))
        await interaction.response.send_message(
            f"Presence: **{pres}** lines\nHourly: **{hour}** lines",
            ephemeral=True
        )

async def setup(bot: discord.Client):
    await bot.add_cog(ReloadContent(bot))

