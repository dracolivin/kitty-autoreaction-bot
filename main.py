# DO NOT COPY CODE.
import discord
from discord.ext import commands, tasks
import sqlite3
import requests
import os
import asyncio
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
import keep_alive

# Load environment variables
load_dotenv()

# Start keep alive server for 24/7 operation
keep_alive.keep_alive()

# === DATABASE ===
conn = sqlite3.connect("config.db")
c = conn.cursor()

c.execute("CREATE TABLE IF NOT EXISTS auto_react (guild_id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 1, user_id INTEGER, channel_ids TEXT, emojis TEXT)")
c.execute("CREATE TABLE IF NOT EXISTS channel_emojis (guild_id INTEGER, channel_id INTEGER, emojis TEXT, PRIMARY KEY (guild_id, channel_id))")
c.execute("CREATE TABLE IF NOT EXISTS prefixes (guild_id INTEGER PRIMARY KEY, prefix TEXT DEFAULT '!')")
c.execute("CREATE TABLE IF NOT EXISTS command_cooldowns (user_id INTEGER, command TEXT, last_used TIMESTAMP, PRIMARY KEY (user_id, command))")
conn.commit()

# === UTILITIES ===

def get_prefix(bot, message):
    if not message.guild:
        return "!"  # Default for DMs
    c.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (message.guild.id,))
    row = c.fetchone()
    return row[0] if row else "!"

def get_guild_config(guild_id):
    c.execute("SELECT enabled, user_id, channel_ids, emojis FROM auto_react WHERE guild_id = ?", (guild_id,))
    row = c.fetchone()
    if row:
        enabled, user_id, channel_ids, emojis = row
        channels = [int(cid) for cid in channel_ids.split(",") if cid] if channel_ids else []
        emoji_list = emojis.split(",") if emojis else ["🔥", "💯", "👍"]
        return {"enabled": bool(enabled), "user_id": user_id, "channels": channels, "emojis": emoji_list}
    else:
        return {"enabled": False, "user_id": None, "channels": [], "emojis": ["🔥", "💯", "👍"]}

def update_guild_config(guild_id, enabled=None, user_id=None, channels=None, emojis=None):
    existing = get_guild_config(guild_id)
    if enabled is None: enabled = existing["enabled"]
    if user_id is None: user_id = existing["user_id"]
    if channels is None: channels = existing["channels"]
    if emojis is None: emojis = existing["emojis"]

    channel_str = ",".join(str(cid) for cid in channels)
    emoji_str = ",".join(emojis)
    c.execute("REPLACE INTO auto_react (guild_id, enabled, user_id, channel_ids, emojis) VALUES (?, ?, ?, ?, ?)",
              (guild_id, int(enabled), user_id, channel_str, emoji_str))
    conn.commit()

def get_channel_emojis(guild_id, channel_id):
    c.execute("SELECT emojis FROM channel_emojis WHERE guild_id = ? AND channel_id = ?", (guild_id, channel_id))
    row = c.fetchone()
    if row and row[0]:
        return row[0].split(",")
    return None

def set_channel_emojis(guild_id, channel_id, emojis):
    emoji_str = ",".join(emojis)
    c.execute("REPLACE INTO channel_emojis (guild_id, channel_id, emojis) VALUES (?, ?, ?)",
              (guild_id, channel_id, emoji_str))
    conn.commit()

def check_cooldown(user_id, command, cooldown_seconds=5):
    """Check if user is on cooldown for a command"""
    c.execute("SELECT last_used FROM command_cooldowns WHERE user_id = ? AND command = ?", (user_id, command))
    row = c.fetchone()
    
    if row:
        last_used = datetime.fromisoformat(row[0])
        if datetime.now() - last_used < timedelta(seconds=cooldown_seconds):
            remaining = cooldown_seconds - (datetime.now() - last_used).seconds
            return False, remaining
    
    # Update cooldown
    c.execute("REPLACE INTO command_cooldowns (user_id, command, last_used) VALUES (?, ?, ?)",
              (user_id, command, datetime.now().isoformat()))
    conn.commit()
    return True, 0

async def get_tenor_gif(query):
    """Fetch a random GIF from Tenor API"""
    api_key = os.getenv("TENOR_API_KEY", "")
    if not api_key:
        return None, "Tenor API key not configured"
    
    try:
        # Tenor API v2 endpoint
        url = f"https://tenor.googleapis.com/v2/search"
        params = {
            "q": query,
            "key": api_key,
            "limit": 20,
            "media_filter": "gif",
            "contentfilter": "medium"
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get("results"):
                # Pick a random GIF from results
                gif = random.choice(data["results"])
                gif_url = gif["media_formats"]["gif"]["url"]
                return gif_url, None
            else:
                return None, "No GIFs found for this action"
        else:
            return None, f"API Error: {response.status_code}"
            
    except requests.exceptions.Timeout:
        return None, "Request timed out"
    except requests.exceptions.RequestException as e:
        return None, f"Network error: {str(e)}"
    except Exception as e:
        return None, f"Unexpected error: {str(e)}"

# === BOT SETUP ===

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True
intents.guilds = True

bot = commands.Bot(command_prefix=get_prefix, intents=intents, help_command=None)

# === EVENTS ===

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} ({bot.user.id})")
    print(f"🎮 Bot is ready and serving {len(bot.guilds)} guilds!")
    
    # Start the keep alive task
    if not keep_alive.is_running():
        keep_alive.start()

@bot.event
async def on_message(message):
    if not message.guild or message.author.bot:
        return

    config = get_guild_config(message.guild.id)

    if not config["enabled"] or not config["user_id"] or message.author.id != config["user_id"]:
        await bot.process_commands(message)
        return

    if config["channels"] and message.channel.id not in config["channels"]:
        await bot.process_commands(message)
        return

    emojis = get_channel_emojis(message.guild.id, message.channel.id) or config["emojis"]

    for emoji in emojis:
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            print(f"Failed to react with {emoji}")

    await bot.process_commands(message)

@bot.event
async def on_command_error(ctx, error):
    prefix = get_prefix(bot, ctx.message)
    
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏰ Command is on cooldown. Try again in {error.retry_after:.1f} seconds.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have administrator permissions to use this command.")
    elif isinstance(error, commands.UserNotFound):
        await ctx.send(f"❌ User not found. Please mention a valid user. Example: `{prefix}hug @username`")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing required argument. Use `{prefix}help` to see command usage.")
    elif isinstance(error, commands.CommandNotFound):
        await ctx.send(f"❓ Command not found. Use `{prefix}help` to see all available commands.")
    else:
        print(f"Unexpected error: {error}")

# === INTERACTIVE GIF COMMANDS ===

async def handle_gif_command(ctx, target: discord.User, action_name: str, action_messages: list):
    """Generic handler for GIF commands"""
    # Check cooldown
    can_use, remaining = check_cooldown(ctx.author.id, action_name, 5)
    if not can_use:
        await ctx.send(f"⏰ You're doing that too fast! Wait {remaining} more seconds.")
        return
    
    # Validate target
    if target == ctx.author:
        await ctx.send(f"🤔 You can't {action_name} yourself!")
        return
    
    if target == bot.user:
        await ctx.send(f"😳 Hey! You can't {action_name} me!")
        return
    
    if target.bot:
        await ctx.send(f"🤖 Bots don't feel {action_name}s!")
        return
    
    # Send typing indicator
    async with ctx.typing():
        # Get GIF from Tenor
        gif_url, error = await get_tenor_gif(f"anime {action_name}")
        
        if error:
            await ctx.send(f"😔 Sorry, I couldn't find a {action_name} GIF right now. {error}")
            return
        
        # Create embed
        message = random.choice(action_messages).format(
            author=ctx.author.mention,
            target=target.mention
        )
        
        embed = discord.Embed(
            description=message,
            color=0xff69b4
        )
        embed.set_image(url=gif_url)
        embed.set_footer(text=f"Powered by Tenor • {action_name.title()} command")
        
        await ctx.send(embed=embed)

@bot.command()
async def hug(ctx, target: discord.User):
    """Give someone a warm hug! 🤗"""
    messages = [
        "{author} gives {target} a big warm hug! 🤗💕",
        "{author} hugs {target} tightly! 🫂❤️",
        "{author} wraps {target} in a cozy hug! 🤗✨",
        "{target} receives a loving hug from {author}! 💝🤗"
    ]
    await handle_gif_command(ctx, target, "hug", messages)

@bot.command()
async def kiss(ctx, target: discord.User):
    """Give someone a sweet kiss! 😘"""
    messages = [
        "{author} gives {target} a sweet kiss! 😘💋",
        "{author} kisses {target} gently! 💕😚",
        "{target} receives a loving kiss from {author}! 💖😘",
        "{author} plants a kiss on {target}! 💋✨"
    ]
    await handle_gif_command(ctx, target, "kiss", messages)

@bot.command()
async def slap(ctx, target: discord.User):
    """Slap someone (playfully)! 👋"""
    messages = [
        "{author} slaps {target}! 👋😤",
        "{author} gives {target} a firm slap! ✋💢",
        "{target} got slapped by {author}! 👋😵",
        "{author} slaps {target} across the face! 💥👋"
    ]
    await handle_gif_command(ctx, target, "slap", messages)

@bot.command()
async def punch(ctx, target: discord.User):
    """Throw a punch (playfully)! 👊"""
    messages = [
        "{author} punches {target}! 👊💥",
        "{author} throws a punch at {target}! 🥊😠",
        "{target} gets punched by {author}! 👊😵",
        "{author} delivers a powerful punch to {target}! 💥👊"
    ]
    await handle_gif_command(ctx, target, "punch", messages)

@bot.command()
async def kill(ctx, target: discord.User):
    """Eliminate someone (playfully)! ⚔️"""
    messages = [
        "{author} eliminates {target}! ⚔️💀",
        "{author} takes down {target}! 🗡️😵",
        "{target} has been defeated by {author}! ⚰️💀",
        "{author} delivers the final blow to {target}! ⚔️💥"
    ]
    await handle_gif_command(ctx, target, "kill", messages)

@bot.command()
async def fuck(ctx, target: discord.User):
    """Show intimate affection! 💕"""
    messages = [
        "{author} shows {target} some love! 💕🔥",
        "{author} gets intimate with {target}! 😏💋",
        "{target} receives passionate attention from {author}! 🔥❤️",
        "{author} and {target} share an intimate moment! 💕✨"
    ]
    await handle_gif_command(ctx, target, "fuck", messages)

@bot.command()
async def groom(ctx, target: discord.User):
    """Help someone look their best! ✨"""
    messages = [
        "{author} helps groom {target}! ✨💅",
        "{author} gives {target} a makeover! 💄✨",
        "{target} gets groomed by {author}! 🛁💇",
        "{author} helps {target} look fabulous! ✨👑"
    ]
    await handle_gif_command(ctx, target, "groom", messages)

# === EXISTING AUTO-REACT COMMANDS ===

@bot.command()
@commands.has_permissions(administrator=True)
async def autoreact(ctx, user: discord.User = None, *emojis):
    """Enable auto-react for a specific user"""
    if user is None:
        prefix = get_prefix(bot, ctx.message)
        return await ctx.send(f"❌ You must mention a user. Usage: {prefix}autoreact @User 😈 💀")

    emoji_list = list(emojis) if emojis else None
    update_guild_config(ctx.guild.id, enabled=True, user_id=user.id, emojis=emoji_list)

    response = f"✅ Auto-react enabled.\n👤 Target: {user.mention}"
    if emojis:
        response += f"\n✨ Emojis: {' '.join(emojis)}"
    await ctx.send(response)

@bot.command()
@commands.has_permissions(administrator=True)
async def autoreactoff(ctx):
    """Disable auto-react"""
    update_guild_config(ctx.guild.id, enabled=False)
    await ctx.send("🛑 Auto-react disabled.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setreactchannels(ctx, *channels: discord.TextChannel):
    """Set specific channels for auto-react"""
    if channels:
        ids = [ch.id for ch in channels]
        update_guild_config(ctx.guild.id, channels=ids)
        names = ", ".join(f"{ch.name}" for ch in channels)
        await ctx.send(f"✅ Will only react in: {names}")
    else:
        update_guild_config(ctx.guild.id, channels=[])
        await ctx.send("✅ Channel filter cleared. Will react in all channels.")

@bot.command()
@commands.has_permissions(administrator=True)
async def setchannelemojis(ctx, channel: discord.TextChannel, *emojis):
    """Set custom emojis for a specific channel"""
    if not emojis:
        return await ctx.send("❌ Please provide at least one emoji.")
    set_channel_emojis(ctx.guild.id, channel.id, emojis)
    await ctx.send(f"✅ Set emojis for {channel.mention} to: {' '.join(emojis)}")

@bot.command()
async def autoreactconfig(ctx):
    """View current auto-react configuration"""
    config = get_guild_config(ctx.guild.id)
    user = f"<@{config['user_id']}>" if config["user_id"] else "Not set"
    channels = [f"<#{cid}>" for cid in config["channels"]] if config["channels"] else ["All channels"]
    global_emojis = " ".join(config["emojis"])

    c.execute("SELECT channel_id, emojis FROM channel_emojis WHERE guild_id = ?", (ctx.guild.id,))
    per_channel = c.fetchall()
    emoji_overrides = "\n".join(
        f"• <#{cid}> → {' '.join(emoji_str.split(','))}" for cid, emoji_str in per_channel
    ) or "None"

    embed = discord.Embed(title="Auto-React Configuration", color=0x00ffcc)
    embed.add_field(name="Status", value="✅ Enabled" if config["enabled"] else "🛑 Disabled", inline=False)
    embed.add_field(name="Target User", value=user, inline=False)
    embed.add_field(name="Active Channels", value=", ".join(channels), inline=False)
    embed.add_field(name="Global Emojis", value=global_emojis, inline=False)
    embed.add_field(name="Per-Channel Emojis", value=emoji_overrides, inline=False)

    await ctx.send(embed=embed)

@bot.command()
async def prefixinfo(ctx):
    """Check current prefix for this server"""
    current_prefix = get_prefix(bot, ctx.message)
    await ctx.send(f"🔧 The current prefix for this server is: {current_prefix}")

@bot.command()
@commands.has_permissions(administrator=True)
async def setprefix(ctx, new_prefix: str):
    """Set a new prefix for this server"""
    if len(new_prefix) > 5:
        return await ctx.send("❌ Prefix too long (max 5 characters).")
    c.execute("REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)", (ctx.guild.id, new_prefix))
    conn.commit()
    await ctx.send(f"✅ Prefix updated to `{new_prefix}`. Try `{new_prefix}help_interactive` to see GIF commands!")

@bot.command()
async def help_interactive(ctx):
    """Show help for interactive GIF commands"""
    prefix = get_prefix(bot, ctx.message)
    
    embed = discord.Embed(
        title="🎭 Interactive GIF Commands",
        description="Express yourself with animated GIFs!",
        color=0xff69b4
    )
    
    commands_list = [
        (f"🤗 `{prefix}hug @user`", "Give someone a warm hug"),
        (f"😘 `{prefix}kiss @user`", "Give someone a sweet kiss"),
        (f"👋 `{prefix}slap @user`", "Slap someone (playfully)"),
        (f"👊 `{prefix}punch @user`", "Throw a punch (playfully)"),
        (f"⚔️ `{prefix}kill @user`", "Eliminate someone (playfully)"),
        (f"💕 `{prefix}fuck @user`", "Show intimate affection"),
        (f"✨ `{prefix}groom @user`", "Help someone look their best")
    ]
    
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.add_field(
        name="ℹ️ Note",
        value="• All commands have a 5-second cooldown\n• You cannot target yourself or bots\n• Powered by Tenor API",
        inline=False
    )
    
    await ctx.send(embed=embed)

@bot.command()
async def help(ctx):
    """Show all available commands with current server prefix"""
    prefix = get_prefix(bot, ctx.message)
    
    embed = discord.Embed(
        title="🤖 Discord Auto-React GIF Bot Commands",
        description=f"All commands for this server use prefix: `{prefix}`",
        color=0x7289da
    )
    
    # Interactive GIF Commands
    embed.add_field(
        name="🎭 Interactive GIF Commands",
        value=f"`{prefix}hug @user` - Give someone a warm hug\n"
              f"`{prefix}kiss @user` - Give someone a sweet kiss\n"
              f"`{prefix}slap @user` - Slap someone (playfully)\n"
              f"`{prefix}punch @user` - Throw a punch (playfully)\n"
              f"`{prefix}kill @user` - Eliminate someone (playfully)\n"
              f"`{prefix}fuck @user` - Show intimate affection\n"
              f"`{prefix}groom @user` - Help someone look their best",
        inline=False
    )
    
    # Auto-React Commands (Admin only)
    embed.add_field(
        name="⚡ Auto-React Commands (Admin Only)",
        value=f"`{prefix}autoreact @user emoji1 emoji2` - Enable auto-react\n"
              f"`{prefix}autoreactoff` - Disable auto-react\n"
              f"`{prefix}setreactchannels #channel1 #channel2` - Set channels\n"
              f"`{prefix}setchannelemojis #channel emoji1 emoji2` - Channel emojis\n"
              f"`{prefix}autoreactconfig` - View current config",
        inline=False
    )
    
    # Utility Commands
    embed.add_field(
        name="🔧 Utility Commands",
        value=f"`{prefix}prefixinfo` - Check current prefix\n"
              f"`{prefix}setprefix newprefix` - Change prefix (Admin)\n"
              f"`{prefix}help_interactive` - Show only GIF commands\n"
              f"`{prefix}help` - Show this help menu",
        inline=False
    )
    
    embed.add_field(
        name="ℹ️ Notes",
        value="• GIF commands have 5-second cooldowns\n"
              "• Admin commands require administrator permissions\n"
              "• Bot reacts to specified users automatically when enabled",
        inline=False
    )
    
    embed.set_footer(text="Powered by Discord.py • GIFs by Tenor API")
    await ctx.send(embed=embed)

# === KEEP ALIVE TASK ===
@tasks.loop(minutes=5)
async def keep_alive():
    """Keep the bot alive and log status"""
    print(f"🔄 Keep alive ping - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 Serving {len(bot.guilds)} guilds with {len(bot.users)} users")

# === RUN ===
if __name__ == "__main__":
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print("❌ DISCORD_BOT_TOKEN environment variable not found!")
        print("Please set your Discord bot token in the environment variables.")
        exit(1)
    
    try:
        bot.run(token)
    except discord.LoginFailure:
        print("❌ Failed to log in - Invalid token!")
    except Exception as e:
        print(f"❌ Bot crashed: {e}")
