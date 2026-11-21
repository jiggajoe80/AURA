# =========================
# FILE: main.py
# =========================
from guild_settings import Settings
import discord
from discord.ext import tasks, commands
import os, json, random, logging
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ────────────── CONFIG ──────────────
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("Aura")

INITIAL_EXTENSIONS = [
    "cogs.admin",
    "cogs.auto_reply",
    "cogs.jokes",
    "cogs.events",
    "cogs.fortunes",
    "cogs.say",
    "cogs.emoji",
    "cogs.emoji_diag",
    "cogs.emoji_ids",
    "cogs.quote",
    "cogs.polls",
    "cogs.timezones",
    "cogs.flip",
    "cogs.profile",
    "cogs.archive_forward",
]

DATA_DIR = Path(__file__).parent / "data"
PRESENCE_FILE = "AURA.PRESENCE.v2.json"
HOURLIES_FILE = "AURA.HOURLIES.v2.json"

QUIET_SECONDS = 97 * 60  # 97 minutes

# ────────────── HELPERS ──────────────
def _load_items_from_json(filename: str):
    fp = DATA_DIR / filename
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "items" in obj:
            return [it.get("text", "") for it in obj["items"] if isinstance(it, dict)]
        if isinstance(obj, list):
            return [str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in obj]
    except Exception as e:
        logger.warning(f"Failed loading {filename}: {e}")
    return []

def load_lines_or_default(file, fallback):
    lines = _load_items_from_json(file)
    return lines if lines else fallback

# ────────────── KEEP ALIVE ──────────────
app = Flask("")

@app.route("/")
def home():
    return "Aura online"

def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

def keep_alive():
    Thread(target=run_web, daemon=True).start()

# ────────────── BOT ──────────────
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True

class AuraBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.presence_pool = []
        self.hourly_pool = []
        self.last_channel_activity = {}
        self.last_post_per_channel = {}
        self.rotation_index = 0
        self.last_reset_date = None
        self.settings = Settings()
        self.scheduled_posts_after = datetime.utcnow() + timedelta(seconds=QUIET_SECONDS)
        self.booted_at = None

    async def setup_hook(self):
        await self.settings.refresh()
        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)
        settings_refresh.start()

    def reset_daily(self):
        today = datetime.utcnow().date()
        if self.last_reset_date != today:
            random.shuffle(self.presence_pool)
            random.shuffle(self.hourly_pool)
            self.last_reset_date = today

    def next_hourly(self):
        self.reset_daily()
        return random.choice(self.hourly_pool)

bot = AuraBot()

# ────────────── LOAD DATA ──────────────
bot.presence_pool = load_lines_or_default(
    PRESENCE_FILE,
    ["quiet, steady, present"]
)
bot.hourly_pool = load_lines_or_default(
    HOURLIES_FILE,
    ["🍀 Clover check-in"]
)

# ────────────── EVENTS ──────────────
@bot.event
async def on_ready():
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=random.choice(bot.presence_pool)
        )
    )

    if bot.booted_at is None:
        try:
            synced = await bot.tree.sync()
            logger.info("Synced %s global commands", len(synced))
        except discord.HTTPException:
            logger.exception("Global command sync failed")
            return
        bot.booted_at = datetime.utcnow()

    if not autopost_loop.is_running():
        autopost_loop.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        cid = (
            message.channel.id
            if isinstance(message.channel, discord.TextChannel)
            else message.channel.parent_id
        )
        if cid:
            bot.last_channel_activity[cid] = datetime.utcnow()

    await bot.process_commands(message)

# ────────────── AUTPOST LOOP ──────────────
@tasks.loop(minutes=5)
async def settings_refresh():
    await bot.settings.refresh()


@tasks.loop(minutes=1)
async def autopost_loop():
    now = datetime.utcnow()
    if now < bot.scheduled_posts_after:
        return
    posted_any = False
    jokes_cog = bot.get_cog("JokesCog")

    for guild in bot.guilds:
        settings = bot.settings.get(guild.id)
        if settings["silent"]:
            continue

        channel_ids = settings["selected_channel_ids"]

        for i, cid in enumerate(channel_ids):
            try:
                cid_int = int(cid)
            except Exception:
                continue

            channel = bot.get_channel(cid_int)
            if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
                continue

            last_human = bot.last_channel_activity.get(cid_int)
            if last_human and (now - last_human).total_seconds() < QUIET_SECONDS:
                continue

            last_post = bot.last_post_per_channel.get(cid_int)
            if last_post and (now - last_post).total_seconds() < QUIET_SECONDS:
                continue

            assign_index = (i + bot.rotation_index) % 2

            try:
                if assign_index == 0 and jokes_cog:
                    msg = jokes_cog.get_random_joke()
                    if not msg:
                        continue
                    await channel.send(msg)
                else:
                    await channel.send(bot.next_hourly())

                bot.last_post_per_channel[cid_int] = datetime.utcnow()
                posted_any = True
            except discord.HTTPException:
                logger.warning('Autopost failed in guild %s channel %s', guild.id, cid_int)

    if posted_any:
        bot.rotation_index += 1

# ────────────── ENTRY ──────────────
if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("DISCORD_TOKEN"))
