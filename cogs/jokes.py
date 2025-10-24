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


def _normalize_jokes(raw):
    """
    Accept any of these shapes and normalize into list[dict] with fields:
      {"setup": "...", "punchline": "..."}  OR  {"text": "..."}
    Supported inputs:
      - ["Q || A", "single liner", ...]
      - [{"text": "Q || A"}, {"text": "single liner"}]
      - [{"setup": "...", "punchline": "..."}, ...]
      - {"items": [ <any of the above> ]}
    """
    # unwrap {"items": [...]}
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]

    norm = []
    if isinstance(raw, list):
        for item in raw:
            # string entry
            if isinstance(item, str):
                if "||" in item:
                    setup, punch = [s.strip() for s in item.split("||", 1)]
                    norm.append({"setup": setup, "punchline": punch})
                else:
                    norm.append({"text": item.strip()})
                continue

            # object entry
            if isinstance(item, dict):
                if "setup" in item and "punchline" in item:
                    norm.append({"setup": item["setup"].strip(),
                                 "punchline": item["punchline"].strip()})
                elif "text" in item and isinstance(item["text"], str):
                    txt = item["text"].strip()
                    if "||" in txt:
                        setup, punch = [s.strip() for s in txt.split("||", 1)]
                        norm.append({"setup": setup, "punchline": punch})
                    else:
                        norm.append({"text": txt})
                # ignore unknown shapes silently
    return norm


class JokesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.jokes = []
        self._load()

    def _load(self):
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

    @app_commands.command(name="joke", description="Tell a quick joke.")
    async def joke(self, interaction: discord.Interaction):
        if not self.jokes:
            await interaction.response.send_message("No jokes loaded yet.", ephemeral=True)
            return

        choice = random.choice(self.jokes)
        if "setup" in choice and "punchline" in choice:
            msg = f"**Q:** {choice['setup']}\n**A:** {choice['punchline']}"
        else:
            msg = choice["text"]

        await interaction.response.send_message(msg)

    @app_commands.command(name="joke_status", description="(Admin) Show joke pool size.")
    @app_commands.default_permissions(manage_guild=True)
    async def joke_status(self, interaction: discord.Interaction):
        await interaction.response.send_message(f"Jokes loaded: **{len(self.jokes)}**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(JokesCog(bot))
