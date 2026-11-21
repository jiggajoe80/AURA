import logging

import discord
from discord import app_commands
from discord.ext import commands

from guild_settings import SettingsError, valid_id


def guild_admin(callback):
    callback = app_commands.checks.has_permissions(administrator=True)(callback)
    callback = app_commands.default_permissions(administrator=True)(callback)
    return app_commands.guild_only()(callback)


class AdminCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _change(self, itx, edit, success):
        await itx.response.defer(ephemeral=True)
        try:
            await self.bot.settings.update(itx.guild_id, edit)
        except SettingsError as exc:
            logging.getLogger("Aura").warning("Guild settings update %s: %s", itx.guild_id, exc)
            await itx.followup.send("Settings change not confirmed. Check status before retrying.", ephemeral=True)
        else:
            await itx.followup.send(success, ephemeral=True)

    @app_commands.command(name="admin")
    @guild_admin
    async def admin(self, itx: discord.Interaction):
        await itx.response.send_message(
            "/admin_status, /admin_silent, /admin_autopost_set, /admin_autopost_remove, "
            "/admin_autopost_clear, /admin_emoji, /admin_say_audit. "
            "Use /admin_autopost_remove_id for an unavailable channel.", ephemeral=True)

    @app_commands.command(name="admin_status")
    @guild_admin
    async def status(self, itx: discord.Interaction):
        row = self.bot.settings.get(itx.guild_id)
        selected = [self._channel_label(itx.guild, value) for value in row["selected_channel_ids"]]
        header = (
            f"Settings: {'available' if self.bot.settings.available() else 'unavailable; safe defaults shown'}\n"
            f"Silent: {'ON' if row['silent'] else 'OFF'} | Emoji: {'ON' if row['emoji_enabled'] else 'OFF'}\n"
            f"Say audit: {self._channel_label(itx.guild, row['say_log_channel_id'])}\n"
            f"Emoji audit: {self._channel_label(itx.guild, row['log_channel_id'])}\nSelected channels:"
        )
        options = {"ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        pages = []
        page = header
        for label in selected or ["None"]:
            if len(page) + len(label) + 1 > 1900:
                pages.append(page)
                page = "Selected channels (continued):"
            page += "\n" + label
        pages.append(page)
        await itx.response.send_message(pages[0], **options)
        for page in pages[1:]:
            await itx.followup.send(page, **options)

    @staticmethod
    def _channel_label(guild, value):
        if value is None:
            return "Unset"
        channel = guild.get_channel(int(value))
        return channel.mention if channel else f"Unavailable channel ({value})"

    @app_commands.command(name="admin_silent")
    @guild_admin
    async def silent(self, itx: discord.Interaction, state: str):
        if state.lower().strip() not in ("on", "off"):
            await itx.response.send_message("Use on or off.", ephemeral=True)
            return
        await self._change(itx, lambda row: row.update(silent=state.lower().strip()=="on"), "Silent setting saved.")

    @app_commands.command(name="admin_autopost_set")
    @guild_admin
    async def add_channel(self, itx: discord.Interaction, channel: discord.TextChannel):
        if channel.guild.id != itx.guild_id:
            await itx.response.send_message("Choose a channel in this server.", ephemeral=True)
            return
        cid = str(channel.id)
        def edit(row):
            if cid not in row["selected_channel_ids"]:
                row["selected_channel_ids"].append(cid)
        await self._change(itx, edit, "Selected channels saved.")

    @app_commands.command(name="admin_autopost_remove")
    @guild_admin
    async def remove_channel(self, itx: discord.Interaction, channel: discord.TextChannel):
        if channel.guild.id != itx.guild_id:
            await itx.response.send_message("Choose a channel in this server.", ephemeral=True)
            return
        await self._remove_selected(itx, str(channel.id))

    @app_commands.command(name="admin_autopost_remove_id", description="Remove an unavailable channel by its ID.")
    @guild_admin
    async def remove_channel_id(self, itx: discord.Interaction, channel_id: str):
        if not valid_id(channel_id):
            await itx.response.send_message("Enter a valid channel ID.", ephemeral=True)
            return
        await self._remove_selected(itx, channel_id)

    async def _remove_selected(self, itx, channel_id):
        def edit(row):
            if channel_id in row["selected_channel_ids"]:
                row["selected_channel_ids"].remove(channel_id)
            else:
                raise SettingsError("Channel ID is not selected in this guild")
        await self._change(itx, edit, "Selected channel removed.")

    @app_commands.command(name="admin_autopost_clear")
    @guild_admin
    async def clear_channels(self, itx: discord.Interaction):
        expected = self.bot.settings.get(itx.guild_id)["selected_channel_ids"]
        def edit(row):
            if row["selected_channel_ids"] != expected:
                raise SettingsError("Channel list changed since last read")
            row["selected_channel_ids"] = []
        await self._change(itx, edit, "Selected channels cleared.")

    @app_commands.command(name="admin_emoji")
    @guild_admin
    async def emoji(self, itx: discord.Interaction, action: str):
        if action == "status":
            row = self.bot.settings.get(itx.guild_id)
            await itx.response.send_message(
                f"Emoji: {row['emoji_enabled']} | Selected IDs: {', '.join(row['selected_channel_ids']) or 'none'}",
                ephemeral=True)
        elif action in ("on", "off"):
            await self._change(itx, lambda row: row.update(emoji_enabled=action=="on"), "Emoji setting saved.")
        else:
            await itx.response.send_message("Use on, off or status.", ephemeral=True)

    @app_commands.command(name="admin_say_audit")
    @guild_admin
    async def say_audit(self, itx: discord.Interaction, action: str, channel: discord.TextChannel | None = None):
        if action == "show":
            value = self.bot.settings.get(itx.guild_id)["say_log_channel_id"]
            await itx.response.send_message(f"Say audit: {value or 'unset'}", ephemeral=True)
        elif action == "clear":
            await self._change(itx, lambda row: row.update(say_log_channel_id=None), "Say audit destination cleared.")
        elif action == "set" and channel and channel.guild.id == itx.guild_id:
            perms = channel.permissions_for(itx.guild.me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                await itx.response.send_message("Aura needs View Channel, Send Messages, and Embed Links there.", ephemeral=True)
                return
            await self._change(itx, lambda row: row.update(say_log_channel_id=str(channel.id)), "Say audit destination saved.")
        else:
            await itx.response.send_message("Use show, clear, or set with a channel in this server.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(AdminCog(bot))
