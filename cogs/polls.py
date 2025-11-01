# cogs/polls.py — Hardened natural poll parser
from discord import app_commands, Interaction
from discord.ext import commands
import re

EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]

def _extract_options(q: str) -> list[str]:
    q = q.strip().rstrip("?").strip()

    # Normalize weird dash types to commas
    q = re.sub(r"[–—−]", "-", q)  # replace en/em/minus with simple dash
    q = re.sub(r"\s*-\s*", ",", q)  # turn dash groups into commas for splitting
    q = re.sub(r"\s*\|\s*", ",", q)  # pipes to commas
    q = re.sub(r"\s*;\s*", ",", q)  # semicolons to commas

    # 1) Smart “or / vs” parsing
    m = re.search(r"(?:^|:)\s*([^:]+?)\s*(?:\b(?:or|vs)\b\s*[^:]+)+$", q, flags=re.IGNORECASE)
    if m:
        segment = m.group(0)
        parts = re.split(r"\b(?:or|vs)\b", segment, flags=re.IGNORECASE)
        opts = [p.replace(":", "").strip() for p in parts if p.strip()]
        if len(opts) >= 2:
            return opts

    # 2) Comma-separated fallback (after dash normalization)
    parts = re.split(r",", q)
    opts = [p.strip() for p in parts if p.strip()]
    if len(opts) >= 2:
        return opts

    # 3) Last resort: simple X or Y
    m2 = re.search(r"(.+?)\s+(?:or|vs)\s+(.+)$", q, flags=re.IGNORECASE)
    if m2:
        return [m2.group(1).strip(), m2.group(2).strip()]

    return []

class Polls(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="poll", description="Create a quick reaction poll.")
    @app_commands.describe(question="Type naturally: 'apples or bananas or oranges' or 'pizza - tacos - burgers'")
    async def poll(self, itx: Interaction, question: str):
        options = _extract_options(question)
        options = [o for o in options if o]
        if len(options) < 2:
            return await itx.response.send_message(
                "I couldn’t find clear options. Try: `Soup or salad?`, `Pie, Cake, Cookies`, or `Tacos - Burritos - Nachos`.",
                ephemeral=True
            )
        options = options[:6]

        title = f"**{question.rstrip('?')}?**"
        body = "\n".join(f"{EMOJI[i]} {opt}" for i, opt in enumerate(options))
        await itx.response.send_message(f"{title}\n{body}")
        msg = await itx.original_response()

        for i in range(len(options)):
            try:
                await msg.add_reaction(EMOJI[i])
            except:
                pass

async def setup(bot): await bot.add_cog(Polls(bot))
