import os
import discord
from discord.ext import tasks
import requests
from icalendar import Calendar
from datetime import datetime, timedelta
import pytz

# ========= CONFIGURE THESE =========

# Your Discord bot token comes from an environment variable (DISCORD_TOKEN)
# On Render, you'll set DISCORD_TOKEN in the dashboard.
TOKEN = os.getenv("DISCORD_TOKEN")

# Channel where the bot will send alerts
CHANNEL_ID = 1441268899369713684  # <-- your alert channel ID (int)

# Role ping:
# If you want to ping everyone, use:
# ROLE_PING = "@everyone"
#
# If you want to ping a specific role (e.g. @Traders):
# - Turn on Developer Mode in Discord
# - Right-click the role -> Copy Role ID
# - Put it like: "<@&ROLE_ID_HERE>"
ROLE_PING = "@1441268539158822935"  # <-- or "<@&YOUR_ROLE_ID_HERE>"

# Forex Factory ICS URL
FOREX_ICS = "https://nfs.faireconomy.media/ff_calendar_thisweek.ics"

# Your timezone
DISPLAY_TZ = pytz.timezone("Asia/Kolkata")

# How often to refresh ICS data
CACHE_TTL_MINUTES = 15

# ===================================

intents = discord.Intents.default()
intents.message_content = True   # needed for !nextnews
client = discord.Client(intents=intents)

# Globals for caching
LAST_FETCH = None
CACHED_EVENTS = []


def get_red_news():
    """
    Fetch upcoming HIGH IMPACT (red folder) events from Forex Factory ICS feed.
    Uses in-memory cache to avoid hitting the server too often.
    Returns list of tuples: (event_time_utc, summary)
    """
    global LAST_FETCH, CACHED_EVENTS

    now_utc = datetime.now(pytz.UTC)

    # Use cache if it's still fresh
    if LAST_FETCH is not None and (now_utc - LAST_FETCH) < timedelta(minutes=CACHE_TTL_MINUTES):
        print(f"[DEBUG] Using cached events ({len(CACHED_EVENTS)} events). Last fetch {(now_utc - LAST_FETCH).seconds // 60} min ago.")
        return CACHED_EVENTS

    print("[DEBUG] Refreshing events from Forex Factory ICS...")

    try:
        response = requests.get(
            FOREX_ICS,
            timeout=10,
            headers={"User-Agent": "ForexNewsBot/1.0"}  # be polite
        )

        # Handle rate limit explicitly
        if response.status_code == 429:
            print("[WARN] Got 429 Too Many Requests from Forex Factory. Using cached events if available.")
            return CACHED_EVENTS

        response.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Could not fetch ICS: {e}")
        # On error, still try to use cached data
        if CACHED_EVENTS:
            print("[INFO] Falling back to cached events.")
            return CACHED_EVENTS
        return []

    try:
        cal = Calendar.from_ical(response.text)
    except Exception as e:
        print(f"[ERROR] Could not parse ICS: {e}")
        if CACHED_EVENTS:
            print("[INFO] Falling back to cached events.")
            return CACHED_EVENTS
        return []

    events = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue

        description = str(component.get("DESCRIPTION", ""))
        summary = str(component.get("SUMMARY", "Unknown event"))

        # Only high impact (red folder)
        # Match both common patterns: "Impact: High" and "High Impact Expected"
        if ("Impact: High" not in description) and ("High Impact Expected" not in description):
            continue

        start = component.get("DTSTART").dt

        if isinstance(start, datetime):
            event_time = start
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=pytz.UTC)
        else:
            event_time = datetime.combine(start, datetime.min.time()).replace(tzinfo=pytz.UTC)

        if event_time > now_utc:
            events.append((event_time, summary))

    # Update cache
    LAST_FETCH = now_utc
    CACHED_EVENTS = events

    print(f"[DEBUG] Fetched and cached {len(events)} upcoming high-impact events.")
    for i, (ev_time, ev_summary) in enumerate(events[:5]):
        lt = ev_time.astimezone(DISPLAY_TZ)
        print(f"   [EVENT {i+1}] {ev_summary} at {lt}")

    return events


@tasks.loop(seconds=60)
async def check_news():
    """
    Runs every 60 seconds.
    Alerts 10 minutes before high-impact news.
    """
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        print("[WARN] Channel not found. Check CHANNEL_ID.")
        return

    now_utc = datetime.now(pytz.UTC)
    events = get_red_news()

    for event_time_utc, summary in events:
        diff = event_time_utc - now_utc

        # Trigger when event is between 9 and 10 minutes away
        if timedelta(minutes=9) < diff <= timedelta(minutes=10):
            local_time = event_time_utc.astimezone(DISPLAY_TZ)
            time_str = local_time.strftime("%Y-%m-%d %H:%M %Z")

            msg = (
                f"{ROLE_PING}\n"
                "⚠️ **High-Impact News Incoming** ⚠️\n\n"
                "The market is about to heat up. Stay sharp.\n\n"
                f"📌 **Event:** `{summary}`\n"
                f"⏰ **Release Time:** `{time_str}`\n"
                "🕒 **Alert:** 10 minutes before\n\n"
                "📉 Volatility spike likely.\n"
                "📊 Manage your risk. Size correctly.\n"
                "💼 Trade like you’re managing prop capital."
            )

            await channel.send(msg)
            print(f"[INFO] 10-min alert sent for: {summary} at {time_str}")


@client.event
async def on_message(message):
    # Ignore own messages
    if message.author == client.user:
        return

    # Respond only in the alert channel
    if message.channel.id != CHANNEL_ID:
        return

    # Command: !nextnews
    if message.content.lower().startswith("!nextnews"):
        events = get_red_news()

        if not events:
            await message.channel.send("No upcoming high-impact news at the moment.")
            return

        # Sort and show next 3
        events = sorted(events, key=lambda e: e[0])[:3]

        lines = []
        for event_time_utc, summary in events:
            lt = event_time_utc.astimezone(DISPLAY_TZ)
            time_str = lt.strftime("%Y-%m-%d %H:%M %Z")
            lines.append(f"• **{summary}** at `{time_str}`")

        reply = (
            "📅 **Next high-impact (red folder) events:**\n"
            + "\n".join(lines) +
            "\n\nStay prepared. News doesn’t care about your stop loss."
        )
        await message.channel.send(reply)


@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    if TOKEN is None:
        print("❌ ERROR: DISCORD_TOKEN env var not set. Bot cannot run.")
    if not check_news.is_running():
        check_news.start()
        print("⏱️ Started checking Forex Factory news every minute.")


client.run(TOKEN)
