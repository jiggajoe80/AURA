import discord
from discord import app_commands
from discord.ext import tasks, commands
import os, json, random, asyncio, logging
from datetime import datetime, timedelta, timezone
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
    "cogs.auto_reply",
    "cogs.jokes",
    "cogs.events",
    "cogs.fortunes",
    "cogs.say",
    "cogs.timezones",
    "cogs.remind"
]

DATA_DIR = Path(__file__).parent / "data"
PRESENCE_FILE = "AURA.PRESENCE.v2.json"
HOURLIES_FILE = "AURA.HOURLIES.v2.json"
JOKES_FILE = "jokes.json"  # ← use the file you actually committed

LOG_CHANNEL_ID = 1427716795615285329
AUTOPOST_CHANNEL_ID = 1399840085536407602
REMINDERS_FILE = "reminders.json"

# ────────────── HELPERS ──────────────
def _load_items_from_json(filename: str):
    fp = DATA_DIR / filename
    try:
        obj = json.loads(fp.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and "items" in obj:
            return [it["text"] for it in obj["items"] if isinstance(it, dict) and "text" in it]
        if isinstance(obj, list):
            return [str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in obj]
    except Exception as e:
        logger.warning(f"[WARN] Failed to load {filename}: {e}")
    return []

def load_lines_or_default(file, fallback):
    lines = _load_items_from_json(file)
    if not lines:
        lines = fallback
    logger.info(f"Loaded {len(lines)} lines from {file}")
    return lines

# ---- joke formatting (used by hourly posts) ---------------------------------
def _split_joke_clean(line: str) -> tuple[str, str]:
    """
    Accepts lines that may already include spoiler bars like '||punch||'.
    Returns (setup, punchline) with any stray trailing pipes removed so we
    don't end up doubling bars when we wrap again.
    """
    raw = str(line).strip()
    parts = raw.split("||", 1)
    if len(parts) == 2:
        setup = parts[0].strip().rstrip("|").rstrip()
        punch = parts[1].strip()
        while punch.endswith("||") or punch.endswith("|"):
            punch = punch[:-1].rstrip("|").rstrip()
        return setup, punch
    return raw, ""

def _format_joke(line: str) -> str:
    q, a = _split_joke_clean(line)
    return f"**Q:** {q} →\n**A:** ||{a}||" if a else q
# -----------------------------------------------------------------------------

# ────────────── KEEP ALIVE ──────────────
app = Flask("")

@app.route("/")
def home(): return "Aura is awake ☘️"

@app.route("/health")
def health(): return "ok", 200

def _run():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    Thread(target=_run, daemon=True).start()

# ────────────── CORE BOT ──────────────
intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

class AuraBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.reminders = []
        self.presence_pool = []
        self.hourly_pool = []
        self.jokes_pool = []
        self.used_presence_today = []
        self.used_hourly_today = []
        self.used_jokes_today = []
        self._hourly_flip = False
        self.last_reset_date = None
        self.last_channel_activity = {}
        self.last_hourly_post = datetime.utcnow() - timedelta(hours=2)
        self.cooldowns = {}
        self._hourly_enabled = True  # one simple flag you can toggle later if needed

    async def setup_hook(self):
        # load cogs
        for ext in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.exception(f"Failed to load {ext}: {e}")
        # sync commands
        try:
            await self.tree.sync()
            logger.info("Slash commands synced.")
        except Exception as e:
            logger.exception(f"Failed to sync slash commands: {e}")

    # ───── JSON LOADERS ─────
    def reset_daily_pools(self):
        now_utc = datetime.utcnow().date()
        if self.last_reset_date != now_utc:
            self.used_presence_today = []
            self.used_hourly_today = []
            self.used_jokes_today = []

            self.presence_pool = PRESENCE_LINES.copy()
            self.hourly_pool   = HOURLY_LINES.copy()
            self.jokes_pool    = JOKES_LINES.copy()

            random.shuffle(self.presence_pool)
            random.shuffle(self.hourly_pool)
            random.shuffle(self.jokes_pool)

            self.last_reset_date = now_utc
            logger.info(f"Daily pools reset at UTC midnight: {now_utc}")

    def get_next_presence(self) -> str:
        self.reset_daily_pools()
        available = [p for p in self.presence_pool if p not in self.used_presence_today] or self.presence_pool.copy()
        choice = random.choice(available)
        self.used_presence_today.append(choice)
        return choice

    def get_next_hourly(self) -> str:
        self.reset_daily_pools()
        available = [h for h in self.hourly_pool if h not in self.used_hourly_today] or self.hourly_pool.copy()
        choice = random.choice(available)
        self.used_hourly_today.append(choice)
        return choice

    def get_next_joke(self) -> str | None:
        """Pick a joke, track usage, and return it formatted with spoilered punchline."""
        self.reset_daily_pools()
        if not self.jokes_pool:
            return None
        # de-dupe-of-the-day
        candidates = [j for j in self.jokes_pool if j not in self.used_jokes_today] or self.jokes_pool
        raw = random.choice(candidates)
        line = raw.get("text") if isinstance(raw, dict) else str(raw)
        self.used_jokes_today.append(line)
        return _format_joke(line)

    def check_cooldown(self, user_id, command_name):
        key = f"{user_id}_{command_name}"
        now = datetime.utcnow()
        if key in self.cooldowns and now < self.cooldowns[key]:
            return False, (self.cooldowns[key] - now).total_seconds()
        self.cooldowns[key] = now + timedelta(seconds=5)
        return True, 0

   # ----- Reminder Save/Load -----
def load_reminders(self):
    try:
        if os.path.exists(REMINDERS_FILE):
            data = json.loads(open(REMINDERS_FILE, "r", encoding="utf-8").read())
            self.reminders = []
            for r in data:
                t = datetime.fromisoformat(r["time"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                else:
                    t = t.astimezone(timezone.utc)
                self.reminders.append({
                    "user_id": r["user_id"],
                    "channel_id": r["channel_id"],
                    "message": r["message"],
                    "time": t
                })
            logger.info(f"Loaded {len(self.reminders)} reminders")
    except Exception as e:
        logger.error(f"Error loading reminders: {e}")

    def save_reminders(self):
        try:
            json.dump(
                [
                    {
                        "user_id": r["user_id"],
                        "channel_id": r["channel_id"],
                        "message": r["message"],
                        "time": r["time"].isoformat()
                    } for r in self.reminders
                ],
                open(REMINDERS_FILE, "w", encoding="utf-8"),
                indent=2,
                ensure_ascii=False
            )
        except Exception as e:
            logger.error(f"Error saving reminders: {e}")

bot = AuraBot()

# ────────────── JSON POOLS INIT ──────────────
PRESENCE_LINES = load_lines_or_default(
    PRESENCE_FILE,
    ["watching the quiet threads", "calm loop, steady soul", "carrying calm in a mug ☘️"]
)
HOURLY_LINES = load_lines_or_default(
    HOURLIES_FILE,
    ["🍀 Clover check-in: unclench your shoulders.", "Cozy reminder: tiny progress counts.", "You’re allowed to ask for help."]
)
JOKES_LINES = load_lines_or_default(
    JOKES_FILE,
    ["Why did the clover smile?||Because it felt lucky!"]
)
bot.jokes_pool = JOKES_LINES.copy()

# ────────────── EVENTS ──────────────
@bot.event
async def on_ready():
    logger.info(f"{bot.user} has connected to Discord!")
    bot.load_reminders()
    bot.reset_daily_pools()

    presence_text = bot.get_next_presence()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=presence_text))

    if not check_reminders.is_running(): check_reminders.start()
    if not rotate_presence.is_running(): rotate_presence.start()
    if not check_hourly_post.is_running(): check_hourly_post.start()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    bot.last_channel_activity[message.channel.id] = datetime.utcnow()
    text = message.content.lower()
    if "good bot" in text or "thanks aura" in text:
        await message.add_reaction("💜")
    elif "aura" in text and any(w in text for w in ["hello", "hi", "hey"]):
        await message.add_reaction("👋")

# ────────────── COMMANDS ──────────────
@bot.tree.command(name="ping", description="Check latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency*1000)}ms")

# ────────────── TASKS ──────────────
from datetime import datetime, timedelta, timezone
# ... (rest of imports)

@tasks.loop(seconds=30)
async def check_reminders():
    # Aware "now" in UTC
    now = datetime.now(timezone.utc)
    done = []

    for r in bot.reminders:
        try:
            when = r["time"]

            # Normalize possible string or naive datetimes
            if isinstance(when, str):
                try:
                    when = datetime.fromisoformat(when)
                except Exception:
                    when = None
            if when is None:
                done.append(r)
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            else:
                when = when.astimezone(timezone.utc)

            # Not yet time
            if now < when:
                continue

            # Resolve user and channel
            try:
                user = await bot.fetch_user(r["user_id"])
            except Exception as e:
                logger.error(f"Reminder fetch_user fail: {e}")
                user = None

            ch = bot.get_channel(r["channel_id"])
            if ch is None:
                try:
                    ch = await bot.fetch_channel(r["channel_id"])
                except Exception as e:
                    logger.error(f"Reminder fetch_channel fail: {e}")
                    ch = None

            text = f"⏰ {user.mention if user else ''} Reminder: {r['message']}".strip()

            sent = False
            if ch and isinstance(ch, (discord.TextChannel, discord.Thread)):
                try:
                    await ch.send(text)
                    sent = True
                except Exception as e:
                    logger.error(f"Reminder channel send fail: {e}")

            if not sent and user:
                try:
                    await user.send(text)
                    sent = True
                except Exception as e:
                    logger.error(f"Reminder DM send fail: {e}")

            done.append(r)

        except Exception as e:
            logger.error(f"Reminder processing error: {e}")
            done.append(r)

    for r in done:
        try:
            bot.reminders.remove(r)
        except ValueError:
            pass
    if done:
        bot.save_reminders()

@tasks.loop(hours=1)
async def rotate_presence():
    text = bot.get_next_presence()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=text))
    logger.info(f"Presence rotated to: {text}")

@tasks.loop(minutes=1)
async def check_hourly_post():
    """
    Post once per hour (if the channel has been quiet for 30+ minutes),
    alternating between a joke and an hourly prompt.
    Jokes include a spoilered punchline so they match /joke style.
    """
    if not bot._hourly_enabled:
        return

    now = datetime.utcnow()
    channel = bot.get_channel(AUTOPOST_CHANNEL_ID)
    if not channel:
        return

    last_activity = bot.last_channel_activity.get(AUTOPOST_CHANNEL_ID)
    inactive_seconds = (now - last_activity).total_seconds() if last_activity else float("inf")
    since_last = (now - bot.last_hourly_post).total_seconds() if bot.last_hourly_post else float("inf")

    # Only post if: quiet ≥ 30m and last post ≥ 60m
    if inactive_seconds >= 1800 and since_last >= 3600:
        try:
            bot._hourly_flip = not bot._hourly_flip  # flip branch: joke <-> hourly

            message: str | None = None
            if bot._hourly_flip and bot.jokes_pool:
                message = bot.get_next_joke()  # formatted with spoiler

            if not message:  # fallback to hourly text
                message = bot.get_next_hourly()

            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                await channel.send(message)

            bot.last_hourly_post = now
            logger.info(f"Posted {'joke' if bot._hourly_flip else 'hourly'}: {message}")
        except Exception as e:
            logger.error(f"Hourly post error: {e}")

# ────────────── ENTRY ──────────────
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("No DISCORD_TOKEN in environment!")
        print("ERROR: Missing DISCORD_TOKEN in .env")
    else:
        keep_alive()
        bot.run(token)
