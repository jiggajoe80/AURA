# =========================
# FILE: cogs/archive_forward.py
# =========================
import discord
from discord import app_commands
from discord.ext import commands
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any

MAX_RESUME_ATTEMPTS = 3
PROGRESS_INTERVAL = 100  # messages

class ArchiveForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # local, in-cog state only
        self._runs: Dict[str, Dict[str, Any]] = {}

    # ---------- helpers ----------
    async def _log(self, log_channel: discord.TextChannel, msg: str):
        await log_channel.send(msg)

    def _run_key(self, source_id: int, webhook_url: str) -> str:
        return f"{source_id}:{webhook_url}"

    def _is_image(self, att: discord.Attachment) -> bool:
        return att.content_type and att.content_type.startswith("image/")

    def _sanitize_text(self, content: Optional[str]) -> str:
        return (content or "").strip()

    async def _post_via_webhook(self, webhook_url: str, content: str, image_urls: list[str]):
        webhook = discord.Webhook.from_url(webhook_url, client=self.bot.http._HTTPClient__session)
        if image_urls:
            # text first, then images (one message per original message)
            files = []
            for url in image_urls:
                files.append(discord.File(fp=await self._fetch_bytes(url), filename="image"))
            await webhook.send(content=content or None, files=files, wait=True)
        else:
            await webhook.send(content=content or None, wait=True)

    async def _fetch_bytes(self, url: str) -> bytes:
        async with self.bot.http._HTTPClient__session.get(url) as resp:
            return await resp.read()

    # ---------- command ----------
    @app_commands.command(
        name="archiveforward",
        description="Archive a channel to a destination via webhook (manual, confirmed)."
    )
    @app_commands.describe(
        source_channel="Source channel to archive",
        destination_webhook="Destination webhook URL",
        log_channel="Logging channel",
        override="Override if this source was archived before"
    )
    async def archiveforward(
        self,
        interaction: discord.Interaction,
        source_channel: discord.TextChannel,
        destination_webhook: str,
        log_channel: discord.TextChannel,
        override: Optional[bool] = False,
    ):
        # runtime validation only
        missing = []
        if not source_channel:
            missing.append("source_channel")
        if not destination_webhook:
            missing.append("destination_webhook")
        if not log_channel:
            missing.append("log_channel")
        if missing:
            await interaction.response.send_message(
                f"Missing runtime input(s): {', '.join(missing)}",
                ephemeral=True
            )
            return

        run_key = self._run_key(source_channel.id, destination_webhook)
        state = self._runs.get(run_key)

        if state and not override:
            await interaction.response.send_message(
                "Hard block: this source appears already archived. Re-run with override=true to proceed.",
                ephemeral=True
            )
            return

        # dry-run summary
        await interaction.response.send_message(
            f"Dry run:\n"
            f"Source: {source_channel.mention}\n"
            f"Destination: webhook\n"
            f"Logging: {log_channel.mention}\n\n"
            f"Confirm to begin archival.",
            ephemeral=True
        )

        # confirmation button
        view = discord.ui.View(timeout=60)

        async def _confirm(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True)
            await self._execute_archive(
                interaction=inter,
                source_channel=source_channel,
                destination_webhook=destination_webhook,
                log_channel=log_channel,
                run_key=run_key
            )

        view.add_item(
            discord.ui.Button(
                label="CONFIRM ARCHIVE",
                style=discord.ButtonStyle.danger,
                custom_id="archive_confirm"
            )
        )

        async def on_interaction_check(inter: discord.Interaction):
            return inter.user.id == interaction.user.id

        view.interaction_check = on_interaction_check

        async def on_timeout():
            pass

        view.on_timeout = on_timeout

        # bind callback
        view.children[0].callback = _confirm
        await interaction.followup.send("Awaiting confirmation.", view=view, ephemeral=True)

    # ---------- execution ----------
    async def _execute_archive(
        self,
        interaction: discord.Interaction,
        source_channel: discord.TextChannel,
        destination_webhook: str,
        log_channel: discord.TextChannel,
        run_key: str,
    ):
        # init state
        self._runs[run_key] = {
            "attempts": 0,
            "last_message_id": None,
            "completed": False,
        }

        await self._log(log_channel, "ArchiveForward START")
        attempts = 0

        while attempts < MAX_RESUME_ATTEMPTS:
            attempts += 1
            self._runs[run_key]["attempts"] = attempts
            try:
                count = 0
                async for msg in source_channel.history(oldest_first=True, limit=None):
                    # exclusions
                    if msg.author.bot and msg.type != discord.MessageType.default:
                        continue
                    if msg.type != discord.MessageType.default:
                        continue
                    if msg.stickers:
                        continue
                    if msg.thread:
                        continue

                    text = self._sanitize_text(msg.content)
                    images = [att.url for att in msg.attachments if self._is_image(att)]

                    if not text and not images:
                        continue

                    await self._post_via_webhook(
                        destination_webhook,
                        content=text,
                        image_urls=images
                    )

                    self._runs[run_key]["last_message_id"] = msg.id
                    count += 1

                    if count % PROGRESS_INTERVAL == 0:
                        await self._log(
                            log_channel,
                            f"ArchiveForward progress: {count} messages forwarded"
                        )

                self._runs[run_key]["completed"] = True
                await self._log(log_channel, "ArchiveForward COMPLETE")
                await interaction.followup.send("Archive complete.", ephemeral=True)
                return

            except Exception as e:
                await self._log(
                    log_channel,
                    f"ArchiveForward ERROR attempt {attempts}: {type(e).__name__}"
                )
                if attempts >= MAX_RESUME_ATTEMPTS:
                    await self._log(log_channel, "ArchiveForward ABORTED after max retries")
                    await interaction.followup.send("Archive aborted after failures.", ephemeral=True)
                    return
                await asyncio.sleep(5)

async def setup(bot: commands.Bot):
    await bot.add_cog(ArchiveForward(bot))
