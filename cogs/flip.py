# =========================
# FILE: cogs/flip.py
# =========================
import random
import discord
from discord import app_commands
from discord.ext import commands

class FlipCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="flip", description="Flip a coin.")
    async def flip(self, interaction: discord.Interaction):
        result = "HEADS 🪙" if random.choice([True, False]) else "TAILS 🪙"
        await interaction.response.send_message(
            f"The coin lands on…\n{result}"
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(FlipCog(bot))
