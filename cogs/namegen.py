import json, random, os
from pathlib import Path
from discord import app_commands, Interaction
from discord.ext import commands

DATA_FILE = Path(__file__).parent.parent / "data" / "namegen.json"

def _load_bank():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # safe defaults if file missing
    return {
        "themes": {
            "fantasy": {"prefix": ["Ar", "Bel", "Cal", "Dor", "Eld"], "core": ["wyn", "thor", "var", "mir", "riel"], "suffix": ["ion", "a", "os", "en", "yth"]},
            "clover":  {"prefix": ["Luck", "Clover", "Juja", "Green", "Thorn"], "core": ["shine", "leaf", "coil", "glint", "veil"], "suffix": ["_V1", "ix", "ling", "hart", "ward"]},
            "villain": {"prefix": ["Night", "Grim", "Viper", "Iron", "Hex"], "core": ["fang", "shade", "coil", "knell", "bane"], "suffix": ["lord", "ix", "helm", "wrath", "mark"]}
        },
        "default_theme": "clover"
    }

def _make_name(parts: dict) -> str:
    return f"{random.choice(parts['prefix'])}{random.choice(parts['core'])}{random.choice(parts['suffix'])}"

class NameGen(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bank = _load_bank()

    @app_commands.command(name="namegen", description="Generate fun names by theme.")
    @app_commands.describe(theme="fantasy, clover, villain…", count="How many (1-10)")
    async def namegen(self, itx: Interaction, theme: str | None = None, count: int = 1):
        themes = self.bank.get("themes", {})
        key = (theme or self.bank.get("default_theme") or next(iter(themes), "clover")).lower()
        if key not in themes:  # graceful fallback
            key = self.bank.get("default_theme", "clover")
        count = max(1, min(10, count))
        names = [ _make_name(themes[key]) for _ in range(count) ]
        await itx.response.send_message(f"**Theme:** `{key}`\n" + "\n".join(f"• {n}" for n in names), ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(NameGen(bot))
