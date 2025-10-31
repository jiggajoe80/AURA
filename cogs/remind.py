# cogs/remind.py
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

EST = ZoneInfo("America/New_York")

# --- parsing helpers ----------------------------------------------------------
_UNIT_MAP = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "wk": 604800, "wks": 604800, "week": 604800, "weeks": 604800,
}

_time12 = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*$", re.I)
_time24 = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*$")
_date_dash = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2})(?::(\d{2}))?\s*([ap]m)?)?\s*$", re.I)
_date_slash_full = re.compile(r"^\s*(\d{4})/(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?)?\s*$", re.I)
_date_slash_short = re.compile(r"^\s*(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2})(?::(\d{2}))?\s*([ap]m)?)?\s*$", re.I)
_duration = re.compile(r"(?P<num>\d+)\s*(?P<unit>[a-zA-Z]+)")

def _parse_time_fragment(s: str):
    s = s.strip()
    m = _time12.match(s)
    if m:
        hh = int(m.group(1)); mm = int(m.group(2) or 0); ap = m.group(3).lower()
        if hh == 12: hh = 0
        if ap == "pm": hh += 12
        return hh, mm
    m = _time24.match(s)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None

def _parse_duration(s: str) -> timedelta | None:
    s = s.lower().strip()
    if s.startswith("in "): s = s[3:].strip()
    total = 0
    found = False
    for num, unit in _duration.findall(s):
        unit = unit.lower()
        if unit in _UNIT_MAP:
            total += int(num) * _UNIT_MAP[unit]
            found = True
    return timedelta(seconds=total) if found and total > 0 else None

def _parse_when(text: str, now_utc: datetime) -> datetime | None:
    """
    Returns an aware UTC datetime or None if unparseable.
    Priority: duration -> today/tomorrow -> explicit date.
    Default timezone for absolute inputs: America/New_York.
    """
    text = text.strip()
    # 1) duration
    dur = _parse_duration(text)
    if dur:
        return now_utc + dur

    # 2) today/tomorrow with time
    lower = text.lower()
    if lower.startswith("today"):
        frag = lower.replace("today", "", 1).replace("at", "", 1).strip()
        hhmm = _parse_time_fragment(frag)
        if hhmm:
            today_est = now_utc.astimezone(EST).date()
            dt_est = datetime(today_est.year, today_est.month, today_est.day, hhmm[0], hhmm[1], tzinfo=EST)
            return dt_est.astimezone(timezone.utc)
    if lower.startswith("tomorrow"):
        frag = lower.replace("tomorrow", "", 1).replace("at", "", 1).strip()
        hhmm = _parse_time_fragment(frag)
        if hhmm:
            base = now_utc.astimezone(EST).date() + timedelta(days=1)
            dt_est = datetime(base.year, base.month, base.day, hhmm[0], hhmm[1], tzinfo=EST)
            return dt_est.astimezone(timezone.utc)

    # 3) explicit dates
    for rx in (_date_dash, _date_slash_full):
        m = rx.match(text)
        if m:
            Y, M, D = int(m.group(1)), int(m.group(2)), int(m.group(3))
            hh = int(m.group(4) or 0); mm = int(m.group(5) or 0); ap = (m.group(6) or "").lower()
            if ap:
                if hh == 12: hh = 0
                if ap == "pm": hh += 12
            dt_est = datetime(Y, M, D, hh, mm, tzinfo=EST)
            return dt_est.astimezone(timezone.utc)

    m = _date_slash_short.match(text)
    if m:
        now_est = now_utc.astimezone(EST)
        Y = now_est.year
        M, D = int(m.group(1)), int(m.group(2))
        hh = int(m.group(3) or 0); mm = int(m.group(4) or 0); ap = (m.group(5) or "").lower()
        if ap:
            if hh == 12: hh = 0
            if ap == "pm": hh += 12
        dt_est = datetime(Y, M, D, hh, mm, tzinfo=EST)
        return dt_est.astimezone(timezone.utc)

    # 4) plain times like "3pm" or "15:30" => today
    hhmm = _parse_time_fragment(text)
    if hhmm:
        today_est = now_utc.astimezone(EST).date()
        dt_est = datetime(today_est.year, today_est.month, today_est.day, hhmm[0], hhmm[1], tzinfo=EST)
        # if time already passed today, assume tomorrow
        if dt_est.astimezone(timezone.utc) <= now_utc + timedelta(seconds=5):
            dt_est = dt_est + timedelta(days=1)
        return dt_est.astimezone(timezone.utc)

    return None
# -----------------------------------------------------------------------------

class RemindCog(commands.Cog):
    """Public reminders: /remind when:<text> about:<text>"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="remind", description="Set a reminder. Example: /remind when:'in 20m' about:'stretch'")
    @app_commands.describe(
        when="When should I remind you? e.g., 'in 10m', 'tomorrow 3pm', '2025-11-05 8:00pm'",
        about="What is the reminder about?"
    )
    async def remind(self, interaction: discord.Interaction, when: str, about: str):
        now_utc = datetime.now(timezone.utc)
        target_utc = _parse_when(when, now_utc)

        if not target_utc:
            return await interaction.response.send_message(
                "I couldn't parse that time. Try things like: `in 10m`, `in 2 hours`, `today 3pm`, `tomorrow 8:05am`, `2025-11-05 8:00pm`.",
                ephemeral=True
            )

        # guards
        if target_utc <= now_utc + timedelta(seconds=10):
            return await interaction.response.send_message("That time is too soon. Pick something at least 10 seconds from now.", ephemeral=True)
        if target_utc >= now_utc + timedelta(days=365):
            return await interaction.response.send_message("That time is too far in the future. Limit is 1 year.", ephemeral=True)

        # trim message
        about = about.strip()
        if len(about) > 200:
            about = about[:200] + "…"

        # store
        reminder = {
            "user_id": interaction.user.id,
            "channel_id": interaction.channel.id if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)) else interaction.user.id,
            "message": about,
            "time": target_utc
        }
        # Use bot’s storage helpers
        try:
            self.bot.reminders.append(reminder)
            # convert time to ISO for file persistence
            self.bot.save_reminders()
        except Exception:
            return await interaction.response.send_message("I couldn't save that reminder. Check logs.", ephemeral=True)

        # friendly confirm in EST + relative
        est = target_utc.astimezone(EST)
        delta = target_utc - now_utc
        hours = int(delta.total_seconds() // 3600)
        mins = int((delta.total_seconds() % 3600) // 60)
        await interaction.response.send_message(
            f"⏰ I'll remind you: **{about}**\n"
            f"• When: **{est.strftime('%A, %B %-d, %Y at %-I:%M%p')}** EST  • in ~{hours}h {mins}m",
            ephemeral=True
        )

async def setup(bot: commands.Bot):
    await bot.add_cog(RemindCog(bot))
