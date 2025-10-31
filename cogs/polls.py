from discord import app_commands, Interaction, TextStyle
from discord.ext import commands
import discord

EMOJI_SET = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]

class Polls(commands.Cog):
    def __init__(self, bot): 
        self.bot = bot

    @app_commands.command(name="poll", description="Create a quick reaction poll.")
    @app_commands.describe(question="Your question", options="Options separated by ; e.g. 'Yes;No;Maybe'")
    async def poll(self, itx: Interaction, question: str, options: str):
        opts = [o.strip() for o in options.split(";") if o.strip()]
        if not 2 <= len(opts) <= 10:
            return await itx.response.send_message("Provide between 2 and 10 options separated by `;`.", ephemeral=True)

        await itx.response.defer()
        lines = [f"**{question}**"] + [f"{EMOJI_SET[i]} {opt}" for i, opt in enumerate(opts)]
        msg = await itx.channel.send("\n".join(lines))
        for i in range(len(opts)):
            try: await msg.add_reaction(EMOJI_SET[i])
            except: pass
        try:
            await itx.followup.send("Poll posted.", ephemeral=True)
        except: 
            pass

async def setup(bot): 
    await bot.add_cog(Polls(bot))
