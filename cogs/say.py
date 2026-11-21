import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
import datetime
import logging

logger = logging.getLogger("Aura")

class DeliveryRejected(Exception):
    pass


async def send_public_once(bot, target, message, mentions):
    # discord.py 2.4 retries message POSTs after some ambiguous failures.
    # A dedicated single POST keeps this command from repeating that request.
    headers = {"Authorization": f"Bot {bot.http.token}", "User-Agent": bot.http.user_agent}
    payload = {"content": message, "allowed_mentions": mentions.to_dict()}
    url = f"https://discord.com/api/v10/channels/{target.id}/messages"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        async with session.post(url, headers=headers, json=payload, allow_redirects=False) as response:
            if 400 <= response.status < 500:
                raise DeliveryRejected
            if response.status != 200:
                raise ValueError("Unconfirmed Discord delivery")
            return int((await response.json())["id"])


class Say(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="say", description="Make Aura speak a message as the bot.")
    @app_commands.guild_only()
    @app_commands.describe(message="What should Aura say?", channel="Optional target channel")
    async def say(self, interaction: discord.Interaction, message: str, channel: discord.TextChannel = None):
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        audit_id = self.bot.settings.get(interaction.guild_id)["say_log_channel_id"]
        log_channel = self.bot.get_channel(int(audit_id)) if audit_id else None
        if (not isinstance(log_channel, discord.TextChannel) or log_channel.guild.id != interaction.guild_id
                or not isinstance(target, (discord.TextChannel, discord.Thread))
                or target.guild.id != interaction.guild_id or target.id == log_channel.id):
            await interaction.followup.send("A valid audit destination and target in this server are required.", ephemeral=True)
            return
        mentions = discord.AllowedMentions.none()
        embed = discord.Embed(
            title="🗣️ Aura said something",
            description=f"**Message:** {message}\n**Channel:** {target.mention}\n**By:** {interaction.user.mention}",
            color=discord.Color.green(), timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Pending delivery")
        try:
            audit = await log_channel.send(embed=embed, allowed_mentions=mentions)
        except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError):
            logger.warning("Say pending audit failed in guild %s", interaction.guild_id)
            await interaction.followup.send("Audit could not be confirmed. Aura did not send the message.", ephemeral=True)
            return
        try:
            sent_id = await send_public_once(self.bot, target, message, mentions)
        except (DeliveryRejected, aiohttp.ClientError, TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
            uncertain = not isinstance(exc, DeliveryRejected)
            embed.set_footer(text="Delivery uncertain" if uncertain else "Delivery failed")
            audit_update_failed = False
            try:
                await audit.edit(embed=embed, allowed_mentions=mentions)
            except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError):
                audit_update_failed = True
                logger.warning("Say failure audit update failed in guild %s", interaction.guild_id)
            outcome = ("Delivery uncertain. Check the target before retrying." if uncertain
                       else "Discord rejected the message.")
            if audit_update_failed:
                outcome += " The audit is still pending."
            await interaction.followup.send(outcome, ephemeral=True)
            return
        embed.set_footer(text=f"Sent | message {sent_id}")
        try:
            await audit.edit(embed=embed, allowed_mentions=mentions)
        except (discord.HTTPException, aiohttp.ClientError, TimeoutError, OSError):
            logger.warning("Say sent but audit finalization failed in guild %s message %s", interaction.guild_id, sent_id)
            await interaction.followup.send(f"Aura spoke in {target.mention}. The audit is still pending. Do not resend.", ephemeral=True)
        else:
            await interaction.followup.send(f"Aura spoke in {target.mention}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Say(bot))
