# cogs/jokes.py
import json
import random
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("Aura")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOKES_FILE = DATA_DIR / "jokes.json"


def _clean_text(s: str) -> str:
    """Strip stray delimiters like || and extra spaces."""
    s = s.strip()
    while s.endswith("||") or s.endswith("|"):
        s = s[:-1].rstrip("|").rstrip()
    return s


def _normalize_jokes(raw):
    """Normalize any supported format into a clean list of jokes."""
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]

    norm = []
    if isinstance(raw, list):
        for item in raw:
            # plain string line
            if isinstance(item, str):
                txt = _clean_text(item)
                if "||" in txt:
                    setup, punch = [_clean_text(x) for x in txt.split("||", 1)]
                    norm.append({"setup": setup, "punchline": punch})
                else:
                    norm.append({"text": txt})
                continue

            # object line
            if isinstance(item, dict):
                if "setup" in item and "punchline" in item:
                    norm.append({
                        "setup": _clean_text(item["setup"]),
                        "punchline": _clean_text(item["punchline"]),
                    })
                elif "text" in item and isinstance(item["text"], str):
                    txt = _clean_text(item["text"])
                    if "||" in txt:
                        setup, punch = [_clean_text(x) for x in txt.split("||", 1)]
                        norm.append({"setup": setup, "punchline": punch})
                    else:
                        norm.append({"text": txt})
    return norm


class JokesCog(commands.Cog):
    """Cog that delivers random jokes from data/jokes.json"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jokes = []
        self._load()

    def _load(self):
        """Load and normalize jokes from file."""
        try:
            with open(JOKES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.jokes = _normalize_jokes(data)
            logger.info("Jokes loaded: %d", len(self.jokes))
        except FileNotFoundError:
            logger.warning("Jokes file not found: %s", JOKES_FILE)
            self.jokes = []
        except Exception as e:
            logger.exception("Failed to load jokes: %s", e)
            self.jokes = []

    @app_commands.command(name="joke", description="Tell a random Aura joke.")
    async def joke(self, interaction: discord.Interaction):
        """Send a random joke."""
        if not self.jokes:
            await interaction.response.send_message("No jokes loaded yet.", ephemeral=True)
            return

        choice = random.choice(self.jokes)

        if "setup" in choice and "punchline" in choice:
            msg = f"**Q:** {choice['setup']} →\n**A:** ||{choice['punchline']}||"
        else:
            msg = choice["text"]

        await interaction.response.send_message(msg)

    @app_commands.command(name="joke_status", description="(Admin) Show joke pool size.")
    @app_commands.default_permissions(manage_guild=True)
    async def joke_status(self, interaction: discord.Interaction):
        """Admin diagnostic: shows how many jokes are loaded."""
        await interaction.response.send_message(
            f"Jokes loaded: **{len(self.jokes)}**", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(JokesCog(bot))
