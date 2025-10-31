import json, random
from pathlib import Path
from discord import app_commands, Interaction
from discord.ext import commands

DATA_FILE = Path(__file__).parent.parent / "data" / "namegen.json"

def _load_bank():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "default_theme": "clover",
        "themes": {
            "clover":  { "prefix": ["Luck","Clover","Juja","Green","Thorn"],
                         "core":   ["shine","leaf","coil","glint","veil"],
                         "suffix": ["_V1","ix","ling","hart","ward"] },
            "fantasy": { "prefix": ["Ar","Bel","Cal","Dor","Eld"],
                         "core":   ["wyn","thor","var","mir","riel"],
                         "suffix": ["ion","a","os","en","yth"] }
        }
    }

def _make(parts: dict) -> str:
    return f"{random.choice(parts['prefix'])}{random.choice(parts['core'])}{random.choice(parts['suffix'])}"

class NameGen(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bank = _load_bank()

    @app_commands.command(name="namegen", description="Generate names by theme (public by default).")
    @app_commands.describe(theme="Theme key from the bank (e.g., clover, fantasy)",
                           count="How many (1–10)",
                           private="If true, reply only to you")
    async def namegen(self, itx: Interaction, theme: str | None = None, count: int = 3, private: bool = False):
        themes = self.bank.get("themes", {})
        key = (theme or self.bank.get("default_theme") or next(iter(themes), "clover")).lower()
        if key not in themes:
            key = self.bank.get("default_theme", "clover")
        count = max(1, min(10, count))
        names = [ _make(themes[key]) for _ in range(count) ]
        text = f"**Theme:** `{key}`\n" + "\n".join(f"• {n}" for n in names)
        await itx.response.send_message(text, ephemeral=private)

async def setup(bot: commands.Bot):
    await bot.add_cog(NameGen(bot))
