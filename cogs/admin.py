import discord
from discord import app_commands
from discord.ext import commands
import json
from pathlib import Path
from typing import List

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
AUTOPOST_MAP_FILE = DATA_DIR / "autopost_map.json"
GUILD_FLAGS_FILE = DATA_DIR / "guild_flags.json"

def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def _save_json(p: Path, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")

class AdminCog(commands.Cog):
    """Admin utilities: silent mode + autopost targets."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _get_maps(self):
        return (
            _load_json(AUTOPOST_MAP_FILE, {}),
            _load_json(GUILD_FLAGS_FILE, {})
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(name="admin", description="Admin controls for Aura")
    async def admin(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "Use subcommands: `/admin silent on|off`, `/admin autopost set`, `/admin status`",
            ephemeral=True
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(name="admin_status", description="Show this guild's Aura config")
    async def admin_status(self, interaction: discord.Interaction):
        ap_map, flags = self._get_maps()
        gid = str(interaction.guild_id)
        silent = flags.get(gid, {}).get("silent", False)
        ch_ids = ap_map.get(gid, [])
        mentions = []
        for cid in ch_ids:
            ch = interaction.guild.get_channel(int(cid))
            if ch:
                mentions.append(ch.mention)
        await interaction.response.send_message(
            f"Guild: **{interaction.guild.name}**\n"
            f"Silent: **{silent}**\n"
            f"Autopost channels: **{', '.join(mentions) if mentions else 'not set'}**",
            ephemeral=True
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(
        name="admin_silent",
        description="Toggle silent mode for this guild"
    )
    @app_commands.describe(state="on or off")
    async def admin_silent(self, interaction: discord.Interaction, state: str):
        state = state.lower().strip()
        if state not in ("on", "off"):
            await interaction.response.send_message("Use `on` or `off`.", ephemeral=True)
            return

        _, flags = self._get_maps()
        gid = str(interaction.guild_id)
        flags.setdefault(gid, {})["silent"] = (state == "on")
        _save_json(GUILD_FLAGS_FILE, flags)

        await interaction.response.send_message(
            f"Silent mode set to **{state}** for this guild.",
            ephemeral=True
        )

    @app_commands.default_permissions(administrator=True)
    @app_commands.guild_only()
    @app_commands.command(
        name="admin_autopost_set",
        description="Set one or more hourly autopost channels"
    )
    @app_commands.describe(
        channels="Select one or more channels for hourly autoposts"
    )
    async def admin_autopost_set(
        self,
        interaction: discord.Interaction,
        channels: app_commands.Transform[
            List[discord.TextChannel],
            app_commands.ChannelTransformer
        ],
    ):
        ap_map, _ = self._get_maps()
        gid = str(interaction.guild_id)

        unique_ids = []
        for ch in channels:
            cid = str(ch.id)
            if cid not in unique_ids:
                unique_ids.append(cid)

        ap_map[gid] = unique_ids
        _save_json(AUTOPOST_MAP_FILE, ap_map)

        mentions = ", ".join(ch.mention for ch in channels)

        await interaction.response.send_message(
            f"Autopost channels set to: {mentions}",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
