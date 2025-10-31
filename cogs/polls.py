# cogs/polls.py
from discord import app_commands, Interaction
from discord.ext import commands
import re

EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]

def _smart_from_question(q: str) -> list[str]:
    # Detect "X or Y" or "A vs B" patterns (case-insensitive)
    m = re.search(r"\b(.+?)\s+(?:or|vs)\s+(.+?)\s*\??$", q, flags=re.IGNORECASE)
    if m:
        a, b = m.group(1).strip(), m.group(2).strip()
        # Avoid capturing the leading question text before a colon, e.g., "Best: soup or salad?"
        a = re.sub(r".*:\s*", "", a)
        return [a, b]
    return []

def _split_list(s: str | None) -> list[str]:
    if not s: return []
    if ";" in s: return [p.strip() for p in s.split(";") if p.strip()]
    if "," in s: return [p.strip() for p in s.split(",") if p.strip()]
    return [s.strip()] if s.strip() else []

class Polls(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="poll", description="Create a quick reaction poll (up to 6 options).")
    @app_commands.describe(
        question="Your question (e.g., 'Soup or salad?')",
        option1="Option 1", option2="Option 2", option3="Option 3",
        option4="Option 4", option5="Option 5", option6="Option 6",
        options="OR paste a list: 'Yes;No;Maybe' or 'Yes, No, Maybe'"
    )
    async def poll(self, itx: Interaction,
                   question: str,
                   option1: str | None = None, option2: str | None = None,
                   option3: str | None = None, option4: str | None = None,
                   option5: str | None = None, option6: str | None = None,
                   options: str | None = None):
        # Priority: explicit option fields > options list > smart parse
        fields = [o for o in [option1, option2, option3, option4, option5, option6] if o]
        if not fields:
            fields = _split_list(options)
        if not fields:
            fields = _smart_from_question(question)

        if not 2 <= len(fields) <= 6:
            return await itx.response.send_message(
                "Provide 2–6 options using fields, a list like `Yes;No;Maybe`, "
                "or phrase the question as `X or Y`.",
                ephemeral=True
            )

        await itx.response.defer()
        lines = [f"**{question.rstrip('?')}?**"] + [f"{EMOJI[i]} {opt}" for i, opt in enumerate(fields)]
        msg = await itx.channel.send("\n".join(lines))
        for i in range(len(fields)):
            try: await msg.add_reaction(EMOJI[i])
            except: pass
        try: await itx.followup.send("Poll posted.", ephemeral=True)
        except: pass

async def setup(bot): await bot.add_cog(Polls(bot))
