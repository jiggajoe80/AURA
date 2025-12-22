# =========================
# FILE: cogs/profile.py
# =========================
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime

class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="Show a snapshot of a Discord user profile.")
    @app_commands.describe(user="User mention or Discord user ID")
    async def profile(self, interaction: discord.Interaction, user: str):
        target = None

        # Try mention resolution
        if interaction.data and interaction.data.get("resolved", {}).get("users"):
            users = interaction.data["resolved"]["users"]
            if users:
                uid = next(iter(users.keys()))
                target = await self.bot.fetch_user(int(uid))

        # Try raw ID
        if target is None:
            try:
                target = await self.bot.fetch_user(int(user))
            except Exception:
                pass

        if target is None:
            await interaction.response.send_message("User not found.", ephemeral=True)
            return

        badges = []
        flags = target.public_flags
        if flags:
            for name, value in flags:
                if value:
                    badges.append(name.replace("_", " ").title())

        badge_text = ", ".join(badges) if badges else "None"

        created = target.created_at.strftime("%B %d, %Y")

        embed = discord.Embed(color=discord.Color.blurple())
        embed.description = f"{target.mention}\n\n" \
            f"**🆔 User ID**\n{target.id}\n\n" \
            f"**👤 Username**\n{target.name}\n\n" \
            f"**🏷️ Display Name**\n{target.global_name or target.name}\n\n" \
            f"**📅 Account Created**\n{created}\n\n" \
            f"**🏅 Badges**\n{badge_text}"

        embed.set_thumbnail(url=target.display_avatar.url)

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(ProfileCog(bot))
