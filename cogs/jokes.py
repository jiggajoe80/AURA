# =========================
# FILE: cogs/jokes.py
# =========================
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
    s = s.strip()
    while s.endswith("||") or s.endswith("|"):
        s = s[:-1].rstrip("|").rstrip()
    return s


def _normalize_jokes(raw):
    if isinstance(raw, dict) and "items" in raw:
        raw = raw["items"]

    norm = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                txt = _clean_text(item)
                if "||" in txt:
                    setup, punch = [_clean_text(x) for x in txt.split("||", 1)]
                    norm.append({"setup": setup, "punchline": punch})
                else:
                    norm.append({"text": txt})
                continue

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


def render_joke(choice: dict) -> str:
    if "setup" in choice and "punchline" in choice:
        return f"**Q:** {choice['setup']} →\n**A:** ||{choice['punchline']}||"
    return choice.get("text", "")


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
        except Exception as e:
            logger.exception("Failed to load jokes: %s", e)
            self.jokes = []

    def get_random_joke(self) -> str:
        if not self.jokes:
            return ""
        return render_joke(random.choice(self.jokes))

    @app_commands.command(name="joke", description="Tell a random Aura joke.")
    async def joke(self, interaction: discord.Interaction):
        msg = self.get_random_joke()
        if not msg:
            await interaction.response.send_message("No jokes loaded yet.", ephemeral=True)
            return
        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(JokesCog(bot))
