import discord
from discord.ext import tasks, commands
import os, json, random, logging
from datetime import datetime
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
]

DATA_DIR = Path(__file__).parent / "data"
PRESENCE_FILE = "AURA.PRESENCE.v2.json"
HOURLIES_FILE = "AURA.HOURLIES.v2.json"
JOKES_FILE = "jokes.json"

AUTOPOST_MAP_FILE = DATA_DIR / "autopost_map.json"
GUILD_FLAGS_FILE = DATA_DIR / "guild_flags.json"

REMINDERS_FILE = "reminders.json"

# ────────────── AUTPOST TIMING ──────────────
POST_INTERVAL_SECONDS = 90 * 60   # 90 minutes
QUIET_SECONDS = 30 * 60           # 30 minutes

# ────────────── HELPERS ──────────────
def _load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def _load_items_from_json(filename: str):
    fp = DATA_DIR / filename
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "items" in obj:
            return [it["text"] for it in obj["items"] if isinstance(it, dict)]
        if isinstance(obj, list):
            return [str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in obj]
    except Exception as e:
        logger.warning(f"Failed loading {filename}: {e}")
    return []

def load_lines_or_default(file, fallback):
    lines = _load_items_from_json(file)
    return lines if lines else fallback

def split_joke(line: str):
    parts = line.split("||", 1)
    return parts[0].strip(), parts[1].strip() if len(parts) == 2 else ""

def format_joke(line: str):
    q, a = split_joke(line)
    return f"**Q:** {q} →\n**A:** ||{a}||" if a else q

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
        self.jokes_pool = []

        self.last_channel_activity = {}
        self.last_hourly_post = None

        self.rotation_index = 0
        self.last_reset_date = None

        self.guild_silent_state = {}
        self.booted_at = None

    async def setup_hook(self):
        for ext in INITIAL_EXTENSIONS:
            await self.load_extension(ext)

    def reset_daily(self):
        today = datetime.utcnow().date()
        if self.last_reset_date != today:
            random.shuffle(self.presence_pool)
            random.shuffle(self.hourly_pool)
            random.shuffle(self.jokes_pool)
            self.last_reset_date = today

    def next_hourly(self):
        self.reset_daily()
        return random.choice(self.hourly_pool)

    def next_joke(self):
        self.reset_daily()
        return format_joke(random.choice(self.jokes_pool))

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
bot.jokes_pool = load_lines_or_default(
    JOKES_FILE,
    ["Why the clover smiled||It felt lucky"]
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

    now = datetime.utcnow()
    bot.booted_at = now
    bot.rotation_index = 0
    bot.last_hourly_post = now
    bot.guild_silent_state = {}

    if not autopost_loop.is_running():
        autopost_loop.start()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if isinstance(message.channel, (discord.TextChannel, discord.Thread)):
        cid = message.channel.id if isinstance(message.channel, discord.TextChannel) else message.channel.parent_id
        if cid:
            bot.last_channel_activity[cid] = datetime.utcnow()
    await bot.process_commands(message)

# ────────────── AUTPOST LOOP ──────────────
@tasks.loop(minutes=1)
async def autopost_loop():
    now = datetime.utcnow()

    if bot.last_hourly_post and (now - bot.last_hourly_post).total_seconds() < POST_INTERVAL_SECONDS:
        return

    ap_map = _load_json(AUTOPOST_MAP_FILE, {})
    flags = _load_json(GUILD_FLAGS_FILE, {})

    posted_any = False

    for guild in bot.guilds:
        gid = str(guild.id)
        silent_now = bool(flags.get(gid, {}).get("silent", False))
        silent_prev = bot.guild_silent_state.get(gid, None)

        bot.guild_silent_state[gid] = silent_now

        if silent_prev is True and silent_now is False:
            bot.last_hourly_post = now
            return

        if silent_now:
            continue

        channel_ids = ap_map.get(gid, [])
        if not channel_ids:
            continue

        channel_ids = list(channel_ids)

        for i, cid in enumerate(channel_ids):
            channel = bot.get_channel(int(cid))
            if not channel:
                continue

            last_activity = bot.last_channel_activity.get(int(cid))
            if last_activity and (now - last_activity).total_seconds() < QUIET_SECONDS:
                continue

            assign_index = (i + bot.rotation_index) % 2
            try:
                if assign_index == 0:
                    await channel.send(bot.next_joke())
                else:
                    await channel.send(bot.next_hourly())
                posted_any = True
            except Exception:
                continue

    if posted_any:
        bot.rotation_index += 1
        bot.last_hourly_post = now

# ────────────── ENTRY ──────────────
if __name__ == "__main__":
    keep_alive()
    bot.run(os.getenv("DISCORD_TOKEN"))
