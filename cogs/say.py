import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging

logger = logging.getLogger("Aura")

LOG_CHANNEL_ID = 1570078685107851334  # 📝│bb-say-logs

class Say(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="Make Aura speak a message as the bot.")
    @app_commands.describe(
        message="What should Aura say?",
        channel="Optional: choose a channel for Aura to speak in"
    )
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        target = channel or interaction.channel

        # Send the message as Aura
        await target.send(message)

        # Log it to your say log channel
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(
                title="🗣️ Aura said something",
                description=f"**Message:** {message}\n**Channel:** {target.mention}\n**By:** {interaction.user.mention}",
                color=discord.Color.green(),
                timestamp=datetime.datetime.utcnow()
            )
            try:
                await log_channel.send(embed=embed)
            except Exception as e:
                logger.warning("[say] audit log send failed: %s", e, exc_info=True)

        await interaction.response.send_message(f"✅ Aura spoke in {target.mention}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Say(bot))
