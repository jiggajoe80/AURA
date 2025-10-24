import discord
from discord import app_commands
from discord.ext import tasks
import os
from dotenv import load_dotenv
import json
import random
from datetime import datetime, timedelta
import logging
import re
import asyncio
from pathlib import Path

# --- added imports for keep-alive ---
from flask import Flask
from threading import Thread
# ------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('Aura')
# --- Aura modular cogs (leave empty until we add Phase 2 modules) ---
INITIAL_EXTENSIONS = [
    "cogs.timezones",   # /time
    "cogs.auto_reply",  # auto-reply module
    "cogs.jokes",       # /joke
    "cogs.events",      # /event
]

# ===== JSON DATA LOADING (Phase 1) =====
DATA_DIR = Path(__file__).parent / "data"
PRESENCE_FILE = os.getenv("AURA_PRESENCE_FILE", "AURA.PRESENCE.v2.json")
HOURLIES_FILE = os.getenv("AURA_HOURLIES_FILE", "AURA.HOURLIES.v2.json")

def _load_items_from_json(filename: str):
    """
    Expect shape:
    {
      "type": "...",
      "version": "v2",
      "meta": {...},
      "items": [ {"id":"...", "text":"..."}, ... ]
    }
    Returns [text, text, ...]
    """
    fp = DATA_DIR / filename
    try:
        with fp.open("r", encoding="utf-8") as f:
            obj = json.load(f)
        items = obj.get("items", [])
        texts = [it["text"] for it in items if isinstance(it, dict) and isinstance(it.get("text"), str)]
        if not texts:
            raise ValueError("no usable 'text' entries in items[]")
        logger.info(f"Loaded {len(texts)} lines from {fp.name}")
        return texts
    except Exception as e:
        logger.warning(f"[WARN] Failed to load {fp.name}: {e}")
        return []

def load_presence_lines():
    lines = _load_items_from_json(PRESENCE_FILE)
    if not lines:
        lines = [
            "watching the quiet threads",
            "calm loop, steady soul",
            "carrying calm in a mug ☘️"
        ]
    return lines

def load_hourly_lines():
    lines = _load_items_from_json(HOURLIES_FILE)
    if not lines:
        lines = [
            "🍀 Clover check-in: unclench your shoulders.",
            "Cozy reminder: tiny progress counts.",
            "You’re allowed to ask for help."
        ]
    return lines
# ======================================

# --- keep-alive webserver (for Render/Koyeb health checks) ---
app = Flask('')

@app.route('/')
def home():
    return "Aura is awake ☘️"

@app.route('/health')
def health():
    return "ok", 200

def _run():
    # Honor platform PORT if provided; default to 8000
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=_run, daemon=True)
    t.start()
# --- end keep-alive ---

LOG_CHANNEL_ID = 1427716795615285329
AUTOPOST_CHANNEL_ID = 1399840085536407602
REMINDERS_FILE = 'reminders.json'

# Phase 1: load from JSON instead of hardcoding
PRESENCE_LINES = load_presence_lines()
HOURLY_LINES   = load_hourly_lines()

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

from discord.ext import commands

class AuraBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.reminders = []
        self.presence_pool = []
        self.hourly_pool = []
        self.used_presence_today = []
        self.used_hourly_today = []
        self.last_reset_date = None
        self.last_channel_activity = {}
        self.last_hourly_post = datetime.utcnow() - timedelta(hours=2)
        self.cooldowns = {}
    
    async def setup_hook(self):
        # Load cogs (you can leave INITIAL_EXTENSIONS empty for now)
        for ext in INITIAL_EXTENSIONS:
            try:
                await self.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                # Not fatal if a cog is missing during rollout; we just log it.
                logger.exception(f"Failed to load {ext}: {e}")

        # Sync slash commands once at startup
        try:
            await self.tree.sync()
            logger.info("Slash commands synced.")
        except Exception as e:
            logger.exception(f"Failed to sync slash commands: {e}")

    def load_reminders(self):
        try:
            if os.path.exists(REMINDERS_FILE):
                with open(REMINDERS_FILE, 'r', encoding="utf-8") as f:
                    data = json.load(f)
                    self.reminders = [
                        {
                            'user_id': r['user_id'],
                            'channel_id': r['channel_id'],
                            'message': r['message'],
                            'time': datetime.fromisoformat(r['time'])
                        }
                        for r in data
                    ]
                    logger.info(f"Loaded {len(self.reminders)} reminders")
        except Exception as e:
            logger.error(f"Error loading reminders: {e}")

    def save_reminders(self):
        try:
            data = [
                {
                    'user_id': r['user_id'],
                    'channel_id': r['channel_id'],
                    'message': r['message'],
                    'time': r['time'].isoformat()
                }
                for r in self.reminders
            ]
            with open(REMINDERS_FILE, 'w', encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving reminders: {e}")

    def parse_time_delay(self, time_str):
        time_str = time_str.strip().lower()
        patterns = [
            (r'^(\d+)\s*s(?:ec(?:ond)?s?)?$', 1),
            (r'^(\d+)\s*m(?:in(?:ute)?s?)?$', 60),
            (r'^(\d+)\s*h(?:our)?s?$', 3600),
            (r'^(\d+)\s*d(?:ay)?s?$', 86400),
            (r'^(\d+)$', 60),  # plain number = minutes
        ]
        for pattern, multiplier in patterns:
            match = re.match(pattern, time_str)
            if match:
                value = int(match.group(1))
                total_seconds = value * multiplier
                if total_seconds <= 0 or total_seconds > 86400:
                    return None
                return total_seconds
        return None

    def reset_daily_pools(self):
        now_utc = datetime.utcnow()
        current_date = now_utc.date()
        if self.last_reset_date != current_date:
            self.used_presence_today = []
            self.used_hourly_today = []
            self.presence_pool = PRESENCE_LINES.copy()
            self.hourly_pool = HOURLY_LINES.copy()
            random.shuffle(self.presence_pool)
            random.shuffle(self.hourly_pool)
            self.last_reset_date = current_date
            logger.info(f"Daily pools reset at UTC midnight: {current_date}")

    def get_next_presence(self):
        self.reset_daily_pools()
        available = [p for p in self.presence_pool if p not in self.used_presence_today]
        if not available:
            self.used_presence_today = []
            available = self.presence_pool.copy()
        if available:
            choice = random.choice(available)
            self.used_presence_today.append(choice)
            return choice
        return "watching the quiet threads"

    def get_next_hourly(self):
        self.reset_daily_pools()
        available = [h for h in self.hourly_pool if h not in self.used_hourly_today]
        if not available:
            self.used_hourly_today = []
            available = self.hourly_pool.copy()
        if available:
            choice = random.choice(available)
            self.used_hourly_today.append(choice)
            return choice
        return "🍀 Clover check-in: unclench your shoulders."

    def check_cooldown(self, user_id, command_name):
        key = f"{user_id}_{command_name}"
        now = datetime.utcnow()
        if key in self.cooldowns and now < self.cooldowns[key]:
            remaining = (self.cooldowns[key] - now).total_seconds()
            return False, remaining
        self.cooldowns[key] = now + timedelta(seconds=5)
        return True, 0

bot = AuraBot()

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Bot is in {len(bot.guilds)} guilds')
    
    bot.load_reminders()
    bot.reset_daily_pools()
    
    presence_text = bot.get_next_presence()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=presence_text
        )
    )
    
    if not check_reminders.is_running():
        check_reminders.start()
    if not rotate_presence.is_running():
        rotate_presence.start()
    if not check_hourly_post.is_running():
        check_hourly_post.start()

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    # record activity for idle gating
    bot.last_channel_activity[message.channel.id] = datetime.utcnow()

    content_lower = message.content.lower()
    if 'good bot' in content_lower or 'thanks aura' in content_lower:
        await message.add_reaction('💜')
    elif 'aura' in content_lower and any(word in content_lower for word in ['hello', 'hi', 'hey']):
        await message.add_reaction('👋')

@bot.tree.command(name='hello', description='Say hello to Aura!')
async def hello(interaction: discord.Interaction):
    greetings = [
        f"Hello {interaction.user.mention}! 👋 How can I help you today?",
        f"Hey there {interaction.user.mention}! 😊",
        f"Hi {interaction.user.mention}! Nice to see you! ✨"
    ]
    await interaction.response.send_message(random.choice(greetings))

@bot.tree.command(name='ping', description='Check if the bot is responsive')
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f'🏓 Pong! Latency: {latency}ms')

@bot.tree.command(name='remind', description='Set a reminder')
@app_commands.describe(
    delay='Time delay (e.g., 30s, 10m, 2h, 1d, or plain minutes)',
    message='What to remind you about'
)
async def remind(interaction: discord.Interaction, delay: str, message: str):
    can_use, remaining = bot.check_cooldown(interaction.user.id, 'remind')
    if not can_use:
        await interaction.response.send_message(
            f"⏰ Please wait {remaining:.1f} more seconds before using this command again.",
            ephemeral=True
        )
        return
    
    seconds = bot.parse_time_delay(delay)
    if seconds is None:
        await interaction.response.send_message(
            "⏰ Invalid time format! Use: 30s, 10m, 2h, 1d, or plain minutes (1-1440)",
            ephemeral=True
        )
        return
    
    reminder_time = datetime.utcnow() + timedelta(seconds=seconds)
    bot.reminders.append({
        'user_id': interaction.user.id,
        'channel_id': interaction.channel_id,
        'message': message,
        'time': reminder_time
    })
    bot.save_reminders()
    
    if seconds >= 3600:
        time_str = f"{seconds // 3600}h"
    elif seconds >= 60:
        time_str = f"{seconds // 60}m"
    else:
        time_str = f"{seconds}s"
    
    await interaction.response.send_message(f"⏰ Got it! I'll remind you in {time_str}: '{message}'")

@bot.tree.command(name='quote', description='Get a random inspirational quote')
async def quote(interaction: discord.Interaction):
    quotes = [
        "✨ 'The only way to do great work is to love what you do.' - Steve Jobs",
        "💫 'Believe you can and you're halfway there.' - Theodore Roosevelt",
        "🌟 'It always seems impossible until it's done.' - Nelson Mandela",
        "🌈 'The future belongs to those who believe in the beauty of their dreams.' - Eleanor Roosevelt",
        "💜 'Success is not final, failure is not fatal: it is the courage to continue that counts.' - Winston Churchill",
        "🎵 'Be yourself; everyone else is already taken.' - Oscar Wilde",
        "✨ 'You miss 100% of the shots you don't take.' - Wayne Gretzky",
        "🌟 'The best time to plant a tree was 20 years ago. The second best time is now.' - Chinese Proverb",
        "💫 'Don't watch the clock; do what it does. Keep going.' - Sam Levenson",
        "🌈 'Everything you've ever wanted is on the other side of fear.' - George Addair"
    ]
    await interaction.response.send_message(random.choice(quotes))

@bot.tree.command(name='about', description='Learn about Aura')
async def about(interaction: discord.Interaction):
    embed = discord.Embed(
        title="About Aura 💜",
        description="A friendly bot here to help your server!",
        color=discord.Color.purple()
    )
    embed.add_field(name="Features", value="• Reminders\n• Slash commands\n• Auto presence\n• Gentle hourly messages\n• Friendly vibes", inline=False)
    embed.add_field(name="Commands", value="Use `/` to see all commands", inline=False)
    embed.set_footer(text=f"Made with 💜 | Serving {len(bot.guilds)} servers")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='say', description='Make Aura say something')
@app_commands.describe(message='What Aura should say')
async def say(interaction: discord.Interaction, message: str):
    can_use, remaining = bot.check_cooldown(interaction.user.id, 'say')
    if not can_use:
        await interaction.response.send_message(
            f"💬 Please wait {remaining:.1f} more seconds before using this command again.",
            ephemeral=True
        )
        return
    
    mass_mention_pattern = r'@(everyone|here)|<@&\d+>'
    if re.search(mass_mention_pattern, message):
        await interaction.response.send_message(
            "❌ Mass mentions (@everyone, @here, role pings) are not allowed in /say commands.",
            ephemeral=True
        )
        return
    
    if isinstance(interaction.channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
        await interaction.response.send_message("✅ Message sent!", ephemeral=True)
        await interaction.channel.send(message)
        
        try:
            log_channel = bot.get_channel(LOG_CHANNEL_ID)
            if log_channel and isinstance(log_channel, (discord.TextChannel, discord.Thread)):
                log_embed = discord.Embed(
                    title="📝 /say Command Used",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                log_embed.add_field(name="User", value=f"{interaction.user.mention} ({interaction.user})", inline=False)
                
                channel_name = getattr(interaction.channel, 'name', 'DM')
                log_embed.add_field(name="Channel", value=f"<#{interaction.channel_id}> ({channel_name})", inline=False)
                log_embed.add_field(name="Message", value=message, inline=False)
                log_embed.set_footer(text=f"User ID: {interaction.user.id}")
                await log_channel.send(embed=log_embed)
        except Exception as e:
            logger.error(f"Error logging /say command: {e}")
    else:
        await interaction.response.send_message("❌ This command can only be used in text channels!", ephemeral=True)

@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.utcnow()
    completed_reminders = []
    for reminder in bot.reminders:
        if now >= reminder['time']:
            try:
                channel = bot.get_channel(reminder['channel_id'])
                if channel and isinstance(channel, (discord.TextChannel, discord.Thread, discord.DMChannel)):
                    user = await bot.fetch_user(reminder['user_id'])
                    await channel.send(f"⏰ {user.mention} Reminder: {reminder['message']}")
                completed_reminders.append(reminder)
            except Exception as e:
                logger.error(f"Error sending reminder: {e}")
                completed_reminders.append(reminder)
    for reminder in completed_reminders:
        bot.reminders.remove(reminder)
    if completed_reminders:
        bot.save_reminders()

@tasks.loop(hours=1)
async def rotate_presence():
    presence_text = bot.get_next_presence()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=presence_text
        )
    )
    logger.info(f"Presence rotated to: {presence_text}")

@tasks.loop(minutes=1)
async def check_hourly_post():
    """
    Minute-by-minute gate:
      - Channel must be idle >= 30 minutes
      - Last Aura post must be >= 60 minutes ago
    (Phase 4 will alternate hourly text vs jokes; for now it's text only.)
    """
    now = datetime.utcnow()
    channel = bot.get_channel(AUTOPOST_CHANNEL_ID)
    if not channel:
        return
    
    last_activity = bot.last_channel_activity.get(AUTOPOST_CHANNEL_ID)
    inactive_duration = (now - last_activity).total_seconds() if last_activity else float('inf')
    time_since_last_post = (now - bot.last_hourly_post).total_seconds() if bot.last_hourly_post else float('inf')
    
    if inactive_duration >= 1800 and time_since_last_post >= 3600:
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                hourly_message = bot.get_next_hourly()
                await channel.send(hourly_message)
                bot.last_hourly_post = now
                logger.info(f"Posted hourly message: {hourly_message}")
            except Exception as e:
                logger.error(f"Error posting hourly message: {e}")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    elif isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(f"❌ This command is on cooldown. Try again in {error.retry_after:.2f} seconds.", ephemeral=True)
    else:
        logger.error(f"Command error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Oops! Something went wrong. Please try again.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Oops! Something went wrong. Please try again.", ephemeral=True)

if __name__ == '__main__':
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        logger.error("No DISCORD_TOKEN found in environment variables!")
        print("ERROR: Please set your DISCORD_TOKEN in the .env file")
    else:
        try:
            # start the keep-alive server before connecting to Discord
            keep_alive()
            bot.run(token)
        except discord.LoginFailure:
            logger.error("Invalid Discord token!")
            print("ERROR: Invalid DISCORD_TOKEN. Please check your token in the .env file")
        except Exception as e:
            logger.error(f"Error starting bot: {e}")
            print(f"ERROR: {e}")
