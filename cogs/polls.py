from discord import app_commands, Interaction
from discord.ext import commands
import discord

EMOJI = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]

def _split_fallback(text: str) -> list[str]:
    if not text: return []
    parts = [p.strip() for sep in (";", ",") for p in text.split(sep)]
    # the loop above double-splits; keep the first split only
    if ";" in text: parts = [p.strip() for p in text.split(";")]
    elif "," in text: parts = [p.strip() for p in text.split(",")]
    return [p for p in parts if p]

class Polls(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="poll", description="Create a quick reaction poll (up to 6 options).")
    @app_commands.describe(
        question="Your question",
        option1="Option 1",
        option2="Option 2",
        option3="Option 3",
        option4="Option 4",
        option5="Option 5",
        option6="Option 6",
        options="OR paste a single list: 'Yes;No;Maybe' or 'Yes, No, Maybe'"
    )
    async def poll(self, itx: Interaction,
                   question: str,
                   option1: str | None = None,
                   option2: str | None = None,
                   option3: str | None = None,
                   option4: str | None = None,
                   option5: str | None = None,
                   option6: str | None = None,
                   options: str | None = None):
        # Build options from individual fields first
        fields = [o for o in [option1, option2, option3, option4, option5, option6] if o]
        # Or fallback to a single text list
        if not fields and options:
            fields = _split_fallback(options)

        if not 2 <= len(fields) <= 6:
            return await itx.response.send_message(
                "Provide 2–6 options using separate fields **or** a single list like `Yes;No;Maybe`.",
                ephemeral=True
            )

        await itx.response.defer()
        lines = [f"**{question}**"] + [f"{EMOJI[i]} {opt}" for i, opt in enumerate(fields)]
        msg = await itx.channel.send("\n".join(lines))
        for i in range(len(fields)):
            try: await msg.add_reaction(EMOJI[i])
            except: pass
        try: await itx.followup.send("Poll posted.", ephemeral=True)
        except: pass

async def setup(bot): await bot.add_cog(Polls(bot))
