# cogs/fortunes.py
import json
import random
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("Aura")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FORTUNES_FILE = DATA_DIR / "fortunes.json"


def _normalize_fortunes(raw):
    """
    Accepts either:
      [ "You will find a cookie.", "Luck favors the bold." ]
      or { "items": [ {"text": "..."}, ... ] }
    Returns a flat list of strings.
    """
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]

    fortunes = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                fortunes.append(item.strip())
            elif isinstance(item, dict) and "text" in item:
                fortunes.append(str(item["text"]).strip())
    return fortunes


class FortunesCog(commands.Cog):
    """🥠 fortune cookie generator"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.fortunes = []
        self._load()

    def _load(self):
        try:
            with open(FORTUNES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.fortunes = _normalize_fortunes(data)
            logger.info("Fortunes loaded: %d", len(self.fortunes))
        except FileNotFoundError:
            logger.warning("Fortunes file not found: %s", FORTUNES_FILE)
            self.fortunes = []
        except Exception as e:
            logger.exception("Failed to load fortunes: %s", e)
            self.fortunes = []

    # Public command
    @app_commands.command(name="fortune", description="Crack open a fortune cookie 🥠")
    async def fortune(self, interaction: discord.Interaction):
        if not self.fortunes:
            await interaction.response.send_message("No fortunes loaded yet.", ephemeral=True)
            return
        msg = random.choice(self.fortunes)
        await interaction.response.send_message(f"🥠 “{msg}”")

    # Admin diagnostic
    @app_commands.command(name="fortune_status", description="(Admin) Show fortune pool size.")
    @app_commands.default_permissions(manage_guild=True)
    async def fortune_status(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Fortunes loaded: **{len(self.fortunes)}**", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(FortunesCog(bot))
