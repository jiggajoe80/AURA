# =========================
# FILE: cogs/archive_forward.py
# =========================
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_FILE = DATA_DIR / "archive_forward_state.json"

MAX_RESUME_ATTEMPTS = 3
RESUME_DELAY_SECONDS = 12
PROGRESS_INTERVAL = 100
MAX_CONTENT_LEN = 1900
QUIET_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    _ensure_data_dir()
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"sources": {}}


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_data_dir()
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _webhook_fingerprint(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"sha256:{h[:10]}"


def _is_thread_channel(ch: discord.abc.GuildChannel) -> bool:
    return isinstance(ch, discord.Thread)


def _is_image_attachment(att: discord.Attachment) -> bool:
    if att.content_type and att.content_type.startswith("image/"):
        return True
    name = (att.filename or "").lower()
    return name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))


def _sanitize_content(s: Optional[str]) -> str:
    return (s or "").strip()


def _truncate(s: str) -> str:
    s = s.strip()
    if len(s) <= MAX_CONTENT_LEN:
        return s
    return s[: MAX_CONTENT_LEN - 1].rstrip() + "…"


class ArchiveForward(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._state = _load_state()

    async def _log(self, log_channel: discord.TextChannel, msg: str) -> None:
        await log_channel.send(msg, allowed_mentions=QUIET_ALLOWED_MENTIONS)

    def _get_source_state(self, source_id: int) -> Dict[str, Any]:
        sources = self._state.setdefault("sources", {})
        return sources.setdefault(str(source_id), {
            "source_channel_id": str(source_id),
            "completed": False,
            "last_message_id": None,
            "updated_at": None,
        })

    def _set_source_state(self, source_id: int, *, completed: Optional[bool] = None, last_message_id: Optional[int] = None) -> None:
        st = self._get_source_state(source_id)
        if completed is not None:
            st["completed"] = bool(completed)
        if last_message_id is not None:
            st["last_message_id"] = str(last_message_id)
        st["updated_at"] = _utc_now_iso()
        _save_state(self._state)

    async def _eligible_message_count(self, source: discord.TextChannel) -> int:
        count = 0
        async for msg in source.history(oldest_first=True, limit=None):
            if msg.type != discord.MessageType.default:
                continue
            if _is_thread_channel(msg.channel):
                continue

            text = _sanitize_content(msg.content)
            images = [att.url for att in msg.attachments if _is_image_attachment(att)]
            if not text and not images:
                continue

            count += 1
        return count

    async def _send_via_webhook(self, session: aiohttp.ClientSession, webhook_url: str, content: str) -> None:
        webhook = discord.Webhook.from_url(webhook_url, session=session)
        await webhook.send(
            content=content if content else None,
            wait=True,
            allowed_mentions=QUIET_ALLOWED_MENTIONS,
        )

    @app_commands.command(
        name="archiveforward",
        description="Forward a channel history to a destination via webhook (manual + confirmed).",
    )
    @app_commands.describe(
        source_channel="Source channel",
        destination_webhook="Destination webhook URL",
        log_channel="Log channel",
        override="Override hard block / reset and re-run source",
    )
    async def archiveforward(
        self,
        interaction: discord.Interaction,
        source_channel: discord.TextChannel,
        destination_webhook: str,
        log_channel: discord.TextChannel,
        override: Optional[bool] = False,
    ):
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

        source_id = source_channel.id
        st = self._get_source_state(source_id)

        if st.get("completed") and not override:
            await interaction.response.send_message(
                "Hard block: source channel already archived. Re-run with override=true to proceed.",
                ephemeral=True
            )
            return

        if override:
            self._set_source_state(source_id, completed=False, last_message_id=None)
            st = self._get_source_state(source_id)

        operator_id = interaction.user.id
        wh_fp = _webhook_fingerprint(destination_webhook)

        await interaction.response.defer(ephemeral=True)

        est_count = None
        try:
            est_count = await self._eligible_message_count(source_channel)
        except Exception:
            est_count = None

        last_id = st.get("last_message_id")
        resume_note = f"Resume after message_id {last_id}" if last_id else "Resume from beginning"

        dry = (
            "Dry run:\n"
            f"Operator: {operator_id}\n"
            f"Source channel ID: {source_id}\n"
            f"Destination webhook: {wh_fp}\n"
            f"Log channel ID: {log_channel.id}\n"
            f"Estimated eligible messages: {est_count if est_count is not None else 'unknown'}\n"
            f"{resume_note}\n\n"
            "Confirm to begin."
        )
        await interaction.followup.send(dry, ephemeral=True, allowed_mentions=QUIET_ALLOWED_MENTIONS)

        view = discord.ui.View(timeout=90)

        async def _confirm_cb(btn_inter: discord.Interaction):
            if btn_inter.user.id != operator_id:
                await btn_inter.response.send_message("Not authorized for this run.", ephemeral=True)
                return
            await btn_inter.response.defer(ephemeral=True)

            await self._log(
                log_channel,
                "ArchiveForward START\n"
                f"Operator: {operator_id}\n"
                f"Source channel ID: {source_id}\n"
                f"Destination webhook: {wh_fp}\n"
                f"Log channel ID: {log_channel.id}\n"
                f"Estimated eligible messages: {est_count if est_count is not None else 'unknown'}\n"
                f"Resume last_message_id: {last_id if last_id else 'None'}"
            )

            await self._execute_archive(
                interaction=btn_inter,
                source_channel=source_channel,
                destination_webhook=destination_webhook,
                log_channel=log_channel,
                operator_id=operator_id,
                webhook_fp=wh_fp,
            )

        async def _cancel_cb(btn_inter: discord.Interaction):
            if btn_inter.user.id != operator_id:
                await btn_inter.response.send_message("Not authorized for this run.", ephemeral=True)
                return
            await btn_inter.response.send_message("Cancelled.", ephemeral=True)

        confirm_btn = discord.ui.Button(label="CONFIRM ARCHIVE", style=discord.ButtonStyle.danger)
        cancel_btn = discord.ui.Button(label="CANCEL", style=discord.ButtonStyle.secondary)
        confirm_btn.callback = _confirm_cb
        cancel_btn.callback = _cancel_cb
        view.add_item(confirm_btn)
        view.add_item(cancel_btn)

        await interaction.followup.send("Awaiting confirmation.", view=view, ephemeral=True)

    async def _execute_archive(
        self,
        interaction: discord.Interaction,
        source_channel: discord.TextChannel,
        destination_webhook: str,
        log_channel: discord.TextChannel,
        operator_id: int,
        webhook_fp: str,
    ) -> None:
        source_id = source_channel.id

        attempts = 0
        forwarded = 0

        async with aiohttp.ClientSession() as session:
            while attempts < MAX_RESUME_ATTEMPTS:
                attempts += 1
                try:
                    st = self._get_source_state(source_id)
                    last_id_str = st.get("last_message_id")
                    after_obj = None
                    if last_id_str:
                        try:
                            after_obj = discord.Object(id=int(last_id_str))
                        except Exception:
                            after_obj = None

                    if attempts > 1:
                        await self._log(
                            log_channel,
                            f"ArchiveForward RESUME attempt {attempts}/{MAX_RESUME_ATTEMPTS} "
                            f"(after {last_id_str if last_id_str else 'None'})"
                        )

                    async for msg in source_channel.history(oldest_first=True, limit=None, after=after_obj):
                        if msg.type != discord.MessageType.default:
                            continue
                        if _is_thread_channel(msg.channel):
                            continue

                        text = _sanitize_content(msg.content)
                        images = [att.url for att in msg.attachments if _is_image_attachment(att)]

                        if not text and not images:
                            continue

                        parts: List[str] = []
                        if text:
                            parts.append(text)
                        if images:
                            parts.extend(images)

                        out = _truncate("\n".join(parts))

                        await self._send_via_webhook(session, destination_webhook, out)

                        forwarded += 1
                        self._set_source_state(source_id, last_message_id=msg.id)

                        if forwarded % PROGRESS_INTERVAL == 0:
                            st2 = self._get_source_state(source_id)
                            await self._log(
                                log_channel,
                                "ArchiveForward PROGRESS\n"
                                f"Operator: {operator_id}\n"
                                f"Source: {source_id}\n"
                                f"Webhook: {webhook_fp}\n"
                                f"Forwarded: {forwarded}\n"
                                f"Last message_id: {st2.get('last_message_id')}"
                            )

                    self._set_source_state(source_id, completed=True)
                    await self._log(
                        log_channel,
                        "ArchiveForward COMPLETE\n"
                        f"Operator: {operator_id}\n"
                        f"Source: {source_id}\n"
                        f"Webhook: {webhook_fp}\n"
                        f"Forwarded: {forwarded}\n"
                        f"Completed: true"
                    )
                    await interaction.followup.send("Archive complete.", ephemeral=True)
                    return

                except Exception as e:
                    await self._log(
                        log_channel,
                        "ArchiveForward ERROR\n"
                        f"Operator: {operator_id}\n"
                        f"Source: {source_id}\n"
                        f"Webhook: {webhook_fp}\n"
                        f"Attempt: {attempts}/{MAX_RESUME_ATTEMPTS}\n"
                        f"Error: {type(e).__name__}: {str(e)[:240]}"
                    )

                    if attempts >= MAX_RESUME_ATTEMPTS:
                        st3 = self._get_source_state(source_id)
                        await self._log(
                            log_channel,
                            "ArchiveForward ABORT\n"
                            f"Operator: {operator_id}\n"
                            f"Source: {source_id}\n"
                            f"Webhook: {webhook_fp}\n"
                            f"Forwarded: {forwarded}\n"
                            f"Last message_id: {st3.get('last_message_id')}\n"
                            "Reason: max resume attempts reached"
                        )
                        await interaction.followup.send("Archive aborted after failures.", ephemeral=True)
                        return

                    await asyncio.sleep(RESUME_DELAY_SECONDS)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArchiveForward(bot))
