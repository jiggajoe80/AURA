import json, random
from pathlib import Path
from discord import app_commands, Interaction, Embed, Colour
from discord.ext import commands

DATA_FILE = Path(__file__).parent.parent / "data" / "quotes.json"

def _load_quotes():
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    # fallback set
    return [{"text":"Luck favors the prepared.", "author":"AURA"}]

class QuoteCog(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot
        self.quotes = _load_quotes()

    @app_commands.command(name="quote", description="Send a random quote.")
    @app_commands.describe(tag="Optional tag to filter, e.g., 'funny', 'daily'")
    async def quote(self, itx: Interaction, tag: str | None = None):
        pool = self.quotes
        if tag:
            pool = [q for q in self.quotes if tag.lower() in [t.lower() for t in q.get("tags", [])]]
            if not pool: pool = self.quotes
        q = random.choice(pool)
        e = Embed(description=q.get("text","…"), colour=Colour.green())
        if q.get("author"): e.set_footer(text=f"— {q['author']}")
        await itx.response.send_message(embed=e)

async def setup(bot): 
    await bot.add_cog(QuoteCog(bot))
