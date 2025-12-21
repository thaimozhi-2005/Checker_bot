import os
import asyncio
import logging
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import TelegramError
from telegram.constants import ParseMode
import asyncpg
from aiohttp import web

# Configure logging for Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)  # Important for Render logs
    ]
)
logger = logging.getLogger(__name__)

# Database connection pool
db_pool = None
is_shutting_down = False

# Graceful shutdown handler
async def shutdown_handler():
    """Handle graceful shutdown"""
    global is_shutting_down
    is_shutting_down = True
    logger.info("🚨 Shutdown initiated...")

async def init_db():
    """Initialize database connection pool with Render optimizations"""
    global db_pool
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        logger.error("❌ DATABASE_URL environment variable is not set!")
        logger.info("Please set DATABASE_URL in Render environment variables")
        return False
    
    logger.info("🔗 Connecting to PostgreSQL database...")
    
    try:
        # Optimized for Render's free tier
        db_pool = await asyncpg.create_pool(
            database_url,
            min_size=1,  # Reduced for free tier
            max_size=5,  # Reduced for free tier
            command_timeout=30,
            timeout=10,
            server_settings={
                'statement_timeout': '30000',
                'idle_in_transaction_session_timeout': '10000'
            }
        )
        
        # Test connection
        async with db_pool.acquire() as conn:
            await conn.execute('SELECT 1')
            logger.info("✅ Database connection successful!")
        
        # Create tables if they don't exist
        async with db_pool.acquire() as conn:
            # Config table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            ''')
            
            # Admins table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS admins (
                    user_id BIGINT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT NOW()
                )
            ''')
            
            # Channels table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY,
                    channel_name TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT NOW(),
                    last_check TIMESTAMP,
                    status TEXT DEFAULT 'unknown'
                )
            ''')
            
            # Channel groups table
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS channel_groups (
                    group_name TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    added_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (group_name, channel_id)
                )
            ''')
            
            # Set default config
            defaults = [
                ('owner', '0'),
                ('check_interval', '3600'),
                ('test_message', '✅ Bot is active!'),
                ('delete_interval', '3'),
                ('bot_active', 'true'),
                ('broadcast_delay', '0.5')
            ]
            
            for key, value in defaults:
                await conn.execute('''
                    INSERT INTO config (key, value) VALUES ($1, $2)
                    ON CONFLICT (key) DO NOTHING
                ''', key, value)
            
            logger.info("✅ Database tables initialized")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        logger.error("Please check:")
        logger.error("1. DATABASE_URL is correct")
        logger.error("2. PostgreSQL database is running")
        logger.error("3. Database credentials are valid")
        return False

async def get_config(key: str) -> Optional[str]:
    """Get config value from database"""
    if not db_pool or is_shutting_down:
        return None
    
    try:
        async with db_pool.acquire() as conn:
            result = await conn.fetchval('SELECT value FROM config WHERE key = $1', key)
            return result
    except Exception as e:
        logger.error(f"Error getting config {key}: {e}")
        return None

async def set_config(key: str, value: str):
    """Set config value in database"""
    if not db_pool or is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ($1, $2)
                ON CONFLICT (key) DO UPDATE SET value = $2
            ''', key, value)
    except Exception as e:
        logger.error(f"Error setting config {key}: {e}")

async def get_owner() -> int:
    """Get owner ID"""
    val = await get_config('owner')
    return int(val) if val else 0

async def set_owner(user_id: int):
    """Set owner ID"""
    await set_config('owner', str(user_id))

async def is_admin(user_id: int) -> bool:
    """Check if user is admin or owner"""
    if is_shutting_down:
        return False
    
    owner = await get_owner()
    if user_id == owner:
        return True
    
    try:
        async with db_pool.acquire() as conn:
            result = await conn.fetchval(
                'SELECT user_id FROM admins WHERE user_id = $1', user_id
            )
            return result is not None
    except Exception:
        return False

async def add_admin(user_id: int):
    """Add admin to database"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute(
                'INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING',
                user_id
            )
    except Exception as e:
        logger.error(f"Error adding admin {user_id}: {e}")

async def remove_admin(user_id: int):
    """Remove admin from database"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM admins WHERE user_id = $1', user_id)
    except Exception as e:
        logger.error(f"Error removing admin {user_id}: {e}")

async def get_admins_count() -> int:
    """Get number of admins"""
    if not db_pool or is_shutting_down:
        return 0
    
    try:
        async with db_pool.acquire() as conn:
            count = await conn.fetchval('SELECT COUNT(*) FROM admins')
            return count or 0
    except Exception:
        return 0

async def get_all_admins() -> List[int]:
    """Get all admin IDs"""
    if not db_pool or is_shutting_down:
        return []
    
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch('SELECT user_id FROM admins')
            return [row['user_id'] for row in rows]
    except Exception:
        return []

async def add_channel(channel_id: str, channel_name: str):
    """Add channel to database"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO channels (channel_id, channel_name)
                VALUES ($1, $2)
                ON CONFLICT (channel_id) DO UPDATE SET channel_name = $2
            ''', channel_id, channel_name)
    except Exception as e:
        logger.error(f"Error adding channel {channel_name}: {e}")

async def remove_channel(channel_id: str):
    """Remove channel from database"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM channels WHERE channel_id = $1', channel_id)
            await conn.execute('DELETE FROM channel_groups WHERE channel_id = $1', channel_id)
    except Exception as e:
        logger.error(f"Error removing channel {channel_id}: {e}")

async def get_all_channels() -> Dict[str, str]:
    """Get all channels"""
    if not db_pool or is_shutting_down:
        return {}
    
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch('SELECT channel_id, channel_name FROM channels')
            return {row['channel_id']: row['channel_name'] for row in rows}
    except Exception:
        return {}

async def update_channel_status(channel_id: str, status: str):
    """Update channel status"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                UPDATE channels 
                SET status = $2, last_check = NOW()
                WHERE channel_id = $1
            ''', channel_id, status)
    except Exception as e:
        logger.error(f"Error updating channel status {channel_id}: {e}")

# Channel groups functions
async def add_channel_to_group(group_name: str, channel_id: str):
    """Add channel to a group"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                INSERT INTO channel_groups (group_name, channel_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
            ''', group_name, channel_id)
    except Exception as e:
        logger.error(f"Error adding to group {group_name}: {e}")

async def remove_channel_from_group(group_name: str, channel_id: str):
    """Remove channel from a group"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('''
                DELETE FROM channel_groups 
                WHERE group_name = $1 AND channel_id = $2
            ''', group_name, channel_id)
    except Exception as e:
        logger.error(f"Error removing from group {group_name}: {e}")

async def get_group_channels(group_name: str) -> List[str]:
    """Get all channels in a group"""
    if not db_pool or is_shutting_down:
        return []
    
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch('''
                SELECT channel_id FROM channel_groups 
                WHERE group_name = $1
            ''', group_name)
            return [row['channel_id'] for row in rows]
    except Exception:
        return []

async def get_all_groups() -> Dict[str, List[str]]:
    """Get all groups with their channels"""
    if not db_pool or is_shutting_down:
        return {}
    
    try:
        async with db_pool.acquire() as conn:
            rows = await conn.fetch('SELECT group_name, channel_id FROM channel_groups')
            groups = {}
            for row in rows:
                if row['group_name'] not in groups:
                    groups[row['group_name']] = []
                groups[row['group_name']].append(row['channel_id'])
            return groups
    except Exception:
        return {}

async def delete_group(group_name: str):
    """Delete entire group"""
    if is_shutting_down:
        return
    
    try:
        async with db_pool.acquire() as conn:
            await conn.execute('DELETE FROM channel_groups WHERE group_name = $1', group_name)
    except Exception as e:
        logger.error(f"Error deleting group {group_name}: {e}")

def parse_time_to_seconds(time_str: str) -> int:
    """Convert time string to seconds"""
    time_str = time_str.lower().strip()
    if not time_str:
        return 0
    
    try:
        if time_str.endswith('s'):
            return int(time_str[:-1])
        elif time_str.endswith('m'):
            return int(time_str[:-1]) * 60
        elif time_str.endswith('h'):
            return int(time_str[:-1]) * 3600
        elif time_str.endswith('d'):
            return int(time_str[:-1]) * 86400
        else:
            return int(time_str)
    except ValueError:
        return 0

def seconds_to_readable(seconds: int) -> str:
    """Convert seconds to readable format"""
    if seconds <= 0:
        return "0s"
    
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
    
    return " ".join(parts)

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    if is_shutting_down:
        await update.message.reply_text("⚠️ Bot is shutting down. Please try again later.")
        return
    
    user_id = update.effective_user.id
    version = os.getenv('BOT_VERSION', '2.0.0')
    owner = await get_owner()
    
    if owner == 0:
        await set_owner(user_id)
        await update.message.reply_text(
            f"🎉 *Welcome! You are now the Owner.*\n\n"
            f"🤖 Bot Version: {version}\n\n"
            "Use /help to see all available commands.",
            parse_mode='Markdown'
        )
    else:
        bot_active = await get_config('bot_active')
        status = "🟢 ACTIVE" if bot_active == 'true' else "🔴 INACTIVE"
        channels = await get_all_channels()
        admins_count = await get_admins_count()
        
        await update.message.reply_text(
            f"👋 *Channel Monitor Bot*\n\n"
            f"🤖 Version: {version}\n"
            f"Status: {status}\n"
            f"Owner: {owner}\n"
            f"Admins: {admins_count}\n"
            f"Channels: {len(channels)}\n\n"
            "Use /help to see all commands.",
            parse_mode='Markdown'
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    if is_shutting_down:
        return
    
    user_id = update.effective_user.id
    
    if await is_admin(user_id):
        help_text = (
            "📋 *Channel Monitor Bot Commands*\n\n"
            "👥 *Admin Management:*\n"
            "/add_admin <user_id> - Add admin\n"
            "/remove_admin <user_id> - Remove admin\n\n"
            "📢 *Channel Management:*\n"
            "/add_channel <@username or -100ID> <name> - Add channel\n"
            "/remove_channel <@username or ID> - Remove channel\n"
            "/list - Show all monitored channels\n\n"
            "📂 *Group Management:*\n"
            "/create_group <group_name> - Create channel group\n"
            "/add_to_group <group_name> <channel_id> - Add channel to group\n"
            "/remove_from_group <group_name> <channel_id> - Remove from group\n"
            "/list_groups - Show all groups\n"
            "/delete_group <group_name> - Delete group\n\n"
            "⚙️ *Configuration:*\n"
            "/time_period <time> - Set check interval\n"
            "   Examples: `30s`, `5m`, `1h`, `12h`, `1d`\n"
            "/test_message <text> - Set test message\n"
            "/delete_interval <time> - Set delete time\n"
            "/status - Show current settings\n\n"
            "🔧 *Operations:*\n"
            "/broadcast - Send to all channels (reply to message)\n"
            "/publish <group_name> - Send to group (reply to message)\n"
            "/usercount - Get user count across channels\n"
            "/on - Turn monitoring ON 🟢\n"
            "/off - Turn monitoring OFF 🔴\n"
            "/help - Show this help"
        )
    else:
        help_text = (
            "📋 *Channel Monitor Bot*\n\n"
            "Available commands:\n"
            "/start - Start the bot\n"
            "/help - Show this message"
        )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add admin command"""
    if is_shutting_down:
        return
    
    owner = await get_owner()
    if update.effective_user.id != owner:
        await update.message.reply_text("❌ Only the owner can add admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /add_admin <user_id>")
        return
    
    try:
        new_admin_id = int(context.args[0])
        if new_admin_id == owner:
            await update.message.reply_text("❌ Owner cannot be added as admin.")
            return
        await add_admin(new_admin_id)
        await update.message.reply_text(f"✅ Admin {new_admin_id} added!")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def remove_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove admin command"""
    if is_shutting_down:
        return
    
    owner = await get_owner()
    if update.effective_user.id != owner:
        await update.message.reply_text("❌ Only the owner can remove admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /remove_admin <user_id>")
        return
    
    try:
        admin_id = int(context.args[0])
        await remove_admin(admin_id)
        await update.message.reply_text(f"✅ Admin {admin_id} removed!")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def add_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add channel command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /add_channel <@username or -100ID> <name>\n\n"
            "Examples:\n"
            "/add_channel @mychannel My Channel\n"
            "/add_channel -1001234567890 Private Channel"
        )
        return
    
    channel_id = context.args[0]
    channel_name = ' '.join(context.args[1:])
    
    try:
        chat = await context.bot.get_chat(channel_id)
        actual_name = chat.title or channel_name
        
        await add_channel(channel_id, channel_name)
        channels = await get_all_channels()
        await update.message.reply_text(
            f"✅ Channel added!\n\n"
            f"ID: `{channel_id}`\n"
            f"Name: {channel_name}\n"
            f"Actual: {actual_name}\n"
            f"Total: {len(channels)}",
            parse_mode='Markdown'
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Warning: Could not verify access.\n"
            f"Make sure bot is admin in the channel.\n\n"
            f"Channel added anyway.\n\n"
            f"Error: {str(e)[:100]}"
        )
        await add_channel(channel_id, channel_name)

async def remove_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove channel command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /remove_channel <@username or ID>")
        return
    
    channel_id = context.args[0]
    channels = await get_all_channels()
    
    if channel_id in channels:
        await remove_channel(channel_id)
        await update.message.reply_text(f"✅ Channel '{channels[channel_id]}' removed!")
    else:
        await update.message.reply_text("❌ Channel not found.")

async def create_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create channel group"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /create_group <group_name>")
        return
    
    group_name = context.args[0]
    await update.message.reply_text(f"✅ Group '{group_name}' created!\n\nUse /add_to_group to add channels.")

async def add_to_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add channel to group"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /add_to_group <group_name> <channel_id>\n\n"
            "Example: /add_to_group premium @mychannel"
        )
        return
    
    group_name = context.args[0]
    channel_id = context.args[1]
    
    channels = await get_all_channels()
    if channel_id not in channels:
        await update.message.reply_text(f"❌ Channel {channel_id} not found. Add it first with /add_channel")
        return
    
    await add_channel_to_group(group_name, channel_id)
    await update.message.reply_text(f"✅ Channel {channels[channel_id]} added to group '{group_name}'!")

async def remove_from_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove channel from group"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /remove_from_group <group_name> <channel_id>")
        return
    
    group_name = context.args[0]
    channel_id = context.args[1]
    
    await remove_channel_from_group(group_name, channel_id)
    await update.message.reply_text(f"✅ Channel removed from group '{group_name}'!")

async def list_groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all groups"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    groups = await get_all_groups()
    channels = await get_all_channels()
    
    if not groups:
        await update.message.reply_text("📂 No groups created yet.\n\nUse /create_group to create one.")
        return
    
    msg = "📂 *Channel Groups:*\n\n"
    for group_name, channel_ids in groups.items():
        msg += f"📁 *{group_name}* ({len(channel_ids)} channels):\n"
        for ch_id in channel_ids:
            ch_name = channels.get(ch_id, "Unknown")
            msg += f"   • {ch_name}\n"
        msg += "\n"
    
    await update.message.reply_text(msg, parse_mode='Markdown')

async def delete_group_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete group"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /delete_group <group_name>")
        return
    
    group_name = context.args[0]
    await delete_group(group_name)
    await update.message.reply_text(f"✅ Group '{group_name}' deleted!")

async def time_period_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set time period command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /time_period <time>\n\n"
            "Examples: 30s, 5m, 1h, 12h, 1d, 2d"
        )
        return
    
    try:
        interval = parse_time_to_seconds(context.args[0])
        if interval < 30:
            await update.message.reply_text("❌ Minimum 30 seconds.")
            return
        
        await set_config('check_interval', str(interval))
        
        # Reschedule job
        jobs = context.job_queue.get_jobs_by_name('channel_check')
        for job in jobs:
            job.schedule_removal()
        
        context.job_queue.run_repeating(
            check_channel_status,
            interval=interval,
            first=10,
            name='channel_check'
        )
        
        readable = seconds_to_readable(interval)
        await update.message.reply_text(f"✅ Check interval set to {readable}!")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use: 30s, 5m, 1h, etc.")

async def test_message_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set test message command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /test_message <your message>")
        return
    
    message = ' '.join(context.args)
    await set_config('test_message', message)
    await update.message.reply_text(f"✅ Test message set to:\n\n{message}")

async def delete_interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set delete interval command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /delete_interval <time>\n\n"
            "Examples: 3s, 10s, 1m"
        )
        return
    
    try:
        interval = parse_time_to_seconds(context.args[0])
        if interval < 1:
            await update.message.reply_text("❌ Minimum 1 second.")
            return
        await set_config('delete_interval', str(interval))
        readable = seconds_to_readable(interval)
        await update.message.reply_text(f"✅ Delete interval set to {readable}!")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use: 3s, 10s, 1m, etc.")

async def list_channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List channels command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    channels = await get_all_channels()
    
    if not channels:
        await update.message.reply_text("📋 No channels being monitored.")
        return
    
    channels_list = []
    for idx, (ch_id, ch_name) in enumerate(channels.items(), 1):
        channels_list.append(f"{idx}. {ch_name}\n   ID: `{ch_id}`")
    
    msg = "📋 *Monitored Channels:*\n\n" + "\n\n".join(channels_list)
    await update.message.reply_text(msg, parse_mode='Markdown')

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_active = await get_config('bot_active')
    bot_status = "🟢 ACTIVE" if bot_active == 'true' else "🔴 INACTIVE"
    
    check_interval = int(await get_config('check_interval') or 3600)
    delete_interval = int(await get_config('delete_interval') or 3)
    test_message = await get_config('test_message') or '✅ Bot is active!'
    owner = await get_owner()
    admins_count = await get_admins_count()
    channels = await get_all_channels()
    groups = await get_all_groups()
    
    check_readable = seconds_to_readable(check_interval)
    delete_readable = seconds_to_readable(delete_interval)
    
    status_msg = (
        f"⚙️ *Bot Configuration:*\n\n"
        f"Status: {bot_status}\n"
        f"👤 Owner: {owner}\n"
        f"👥 Admins: {admins_count}\n"
        f"📢 Channels: {len(channels)}\n"
        f"📂 Groups: {len(groups)}\n"
        f"⏱ Check Interval: {check_readable}\n"
        f"🗑 Delete Interval: {delete_readable}\n"
        f"💬 Test Message: {test_message}"
    )
    await update.message.reply_text(status_msg, parse_mode='Markdown')

async def bot_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn bot off command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_active = await get_config('bot_active')
    if bot_active == 'false':
        await update.message.reply_text("ℹ️ Bot already inactive.")
        return
    
    await set_config('bot_active', 'false')
    await update.message.reply_text(
        "🔴 Bot monitoring turned OFF\n\n"
        "Use /on to resume."
    )
    logger.info("Bot turned OFF")

async def bot_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn bot on command"""
    if is_shutting_down:
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_active = await get_config('bot_active')
    if bot_active == 'true':
        await update.message.reply_text("ℹ️ Bot already active.")
        return
    
    await set_config('bot_active', 'true')
    check_interval = int(await get_config('check_interval') or 3600)
    await update.message.reply_text(
        "🟢 Bot monitoring turned ON\n\n"
        f"Check interval: {seconds_to_readable(check_interval)}"
    )
    logger.info("Bot turned ON")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast command"""
    if is_shutting_down:
        await update.message.reply_text("⚠️ Bot is shutting down. Please try again later.")
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    # Check if replying to a message
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Please reply to the message you want to broadcast.\n\n"
            "Usage: Reply to any message and type /broadcast"
        )
        return
    
    channels = await get_all_channels()
    if not channels:
        await update.message.reply_text("❌ No channels to broadcast to.")
        return
    
    broadcast_delay = float(await get_config('broadcast_delay') or 0.5)
    
    successful = 0
    failed = 0
    failed_channels = []
    
    status_msg = await update.message.reply_text("📡 Broadcasting... 0%")
    total_channels = len(channels)
    
    for idx, (channel_id, channel_name) in enumerate(channels.items(), 1):
        try:
            # Forward or copy the message with all formatting
            await update.message.reply_to_message.copy(chat_id=channel_id)
            successful += 1
            
            # Update progress every 10 channels or 10%
            if idx % 10 == 0 or idx == total_channels:
                progress = int((idx / total_channels) * 100)
                try:
                    await status_msg.edit_text(f"📡 Broadcasting... {progress}%")
                except:
                    pass
            
            if broadcast_delay > 0:
                await asyncio.sleep(broadcast_delay)
                
        except Exception as e:
            logger.error(f"Broadcast failed: {channel_name} - {e}")
            failed += 1
            failed_channels.append(f"{channel_name}: {str(e)[:50]}")
    
    report = (
        f"📊 *Broadcast Report:*\n\n"
        f"✅ Successful: {successful}\n"
        f"❌ Failed: {failed}\n"
        f"📢 Total: {total_channels}"
    )
    
    if failed_channels:
        report += "\n\n⚠️ *Failed Channels:*\n" + "\n".join([f"• {fc}" for fc in failed_channels[:5]])
        if len(failed_channels) > 5:
            report += f"\n... and {len(failed_channels) - 5} more"
    
    await status_msg.edit_text(report, parse_mode='Markdown')

async def publish_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Publish command - broadcast to specific group"""
    if is_shutting_down:
        await update.message.reply_text("⚠️ Bot is shutting down. Please try again later.")
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: Reply to a message and type /publish <group_name>\n\n"
            "Example: /publish premium"
        )
        return
    
    if not update.message.reply_to_message:
        await update.message.reply_text(
            "❌ Please reply to the message you want to publish.\n\n"
            "Usage: Reply to any message and type /publish <group_name>"
        )
        return
    
    group_name = context.args[0]
    channel_ids = await get_group_channels(group_name)
    
    if not channel_ids:
        await update.message.reply_text(
            f"❌ Group '{group_name}' not found or has no channels.\n\n"
            "Use /list_groups to see all groups."
        )
        return
    
    channels = await get_all_channels()
    broadcast_delay = float(await get_config('broadcast_delay') or 0.5)
    
    successful = 0
    failed = 0
    failed_channels = []
    
    status_msg = await update.message.reply_text(f"📡 Publishing to group '{group_name}'... 0%")
    total_channels = len(channel_ids)
    
    for idx, channel_id in enumerate(channel_ids, 1):
        channel_name = channels.get(channel_id, "Unknown")
        try:
            # Copy message with all formatting
            await update.message.reply_to_message.copy(chat_id=channel_id)
            successful += 1
            
            # Update progress
            if idx % 5 == 0 or idx == total_channels:
                progress = int((idx / total_channels) * 100)
                try:
                    await status_msg.edit_text(f"📡 Publishing to group '{group_name}'... {progress}%")
                except:
                    pass
            
            if broadcast_delay > 0:
                await asyncio.sleep(broadcast_delay)
                
        except Exception as e:
            logger.error(f"Publish failed: {channel_name} - {e}")
            failed += 1
            failed_channels.append(f"{channel_name}: {str(e)[:50]}")
    
    report = (
        f"📊 *Publish Report (Group: {group_name}):*\n\n"
        f"✅ Successful: {successful}\n"
        f"❌ Failed: {failed}\n"
        f"📢 Total: {total_channels}"
    )
    
    if failed_channels:
        report += "\n\n⚠️ *Failed Channels:*\n" + "\n".join([f"• {fc}" for fc in failed_channels[:5]])
        if len(failed_channels) > 5:
            report += f"\n... and {len(failed_channels) - 5} more"
    
    await status_msg.edit_text(report, parse_mode='Markdown')

async def usercount_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get user count across all channels"""
    if is_shutting_down:
        await update.message.reply_text("⚠️ Bot is shutting down. Please try again later.")
        return
    
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    channels = await get_all_channels()
    
    if not channels:
        await update.message.reply_text("❌ No channels to count users from.")
        return
    
    status_msg = await update.message.reply_text("🔄 Fetching user counts... 0%")
    
    channel_counts = {}
    failed_channels = []
    total_members = 0
    total_channels = len(channels)
    
    for idx, (channel_id, channel_name) in enumerate(channels.items(), 1):
        try:
            member_count = await context.bot.get_chat_member_count(channel_id)
            channel_counts[channel_name] = member_count
            total_members += member_count
            
            # Update progress every 5 channels
            if idx % 5 == 0:
                progress = int((idx / total_channels) * 100)
                try:
                    await status_msg.edit_text(f"🔄 Fetching user counts... {progress}%")
                except:
                    pass
            
            await asyncio.sleep(0.3)  # Rate limiting
            
        except Exception as e:
            logger.error(f"Failed to get count for {channel_name}: {e}")
            failed_channels.append(channel_name)
    
    # Create report
    report = "📊 *User Count Report:*\n\n"
    
    for ch_name, count in channel_counts.items():
        report += f"📢 {ch_name}: {count:,} members\n"
    
    report += f"\n📈 *Total Members:* {total_members:,}\n"
    report += f"📢 *Channels Counted:* {len(channel_counts)}/{len(channels)}\n\n"
    
    report += (
        "⚠️ *Note:* Unique user count (excluding duplicates) cannot be accurately "
        "calculated due to Telegram API limitations. The total shown includes "
        "users who may be in multiple channels.\n\n"
        "To get an estimate, assume 20-40% overlap for related channels."
    )
    
    if failed_channels:
        report += f"\n\n❌ *Failed channels:*\n" + "\n".join([f"• {fc}" for fc in failed_channels[:10]])
        if len(failed_channels) > 10:
            report += f"\n... and {len(failed_channels) - 10} more"
    
    await status_msg.edit_text(report, parse_mode='Markdown')

# ==================== BACKGROUND TASKS ====================

async def check_channel_status(context: ContextTypes.DEFAULT_TYPE):
    """Check channel status periodically"""
    if is_shutting_down:
        return
    
    bot_active = await get_config('bot_active')
    if bot_active != 'true':
        logger.debug("Bot is inactive, skipping channel check")
        return
    
    channels = await get_all_channels()
    if not channels:
        logger.debug("No channels to check")
        return
    
    logger.info(f"🔍 Checking {len(channels)} channels...")
    
    test_message = await get_config('test_message') or '✅ Bot is active!'
    delete_interval = int(await get_config('delete_interval') or 3)
    
    for channel_id, channel_name in channels.items():
        if is_shutting_down:
            break
            
        try:
            # Send test message
            message = await context.bot.send_message(
                chat_id=channel_id,
                text=test_message,
                parse_mode=ParseMode.HTML
            )
            
            await update_channel_status(channel_id, 'active')
            logger.debug(f"Channel OK: {channel_name}")
            
            # Schedule message deletion
            if delete_interval > 0:
                await asyncio.sleep(delete_interval)
                try:
                    await context.bot.delete_message(
                        chat_id=channel_id,
                        message_id=message.message_id
                    )
                    logger.debug(f"Test message deleted from: {channel_name}")
                except:
                    pass  # Ignore deletion errors
            
            await asyncio.sleep(1)  # Delay between checks
            
        except Exception as e:
            logger.warning(f"Channel FAILED: {channel_name} - {e}")
            await update_channel_status(channel_id, 'inactive')
            await asyncio.sleep(1)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle non-command messages"""
    if is_shutting_down:
        return
    # Ignore non-command messages for now
    pass

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors gracefully"""
    if is_shutting_down:
        return
    
    try:
        logger.error(f"Exception while handling update: {context.error}", exc_info=True)
        
        # Try to notify user if it's a command error
        if update and isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text(
                    "⚠️ An error occurred. Please try again later or contact the bot owner."
                )
            except:
                pass
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

# ==================== WEB SERVER (FOR RENDER HEALTH CHECKS) ====================

async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text='OK', status=200)

async def start_web_server():
    """Start a simple web server for health checks"""
    try:
        app = web.Application()
        app.router.add_get('/', health_check)
        app.router.add_get('/health', health_check)
        
        port = int(os.getenv('PORT', '8080'))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        logger.info(f"🌐 Web server started on port {port}")
    except Exception as e:
        logger.error(f"Failed to start web server: {e}")

# ==================== MAIN FUNCTION ====================

async def setup_bot_commands(application: Application):
    """Setup bot commands menu"""
    try:
        commands = [
            BotCommand("start", "Start the bot"),
            BotCommand("help", "Show help message"),
            BotCommand("list", "List all channels"),
            BotCommand("status", "Show bot status"),
            BotCommand("usercount", "Get user count across channels"),
            BotCommand("broadcast", "Broadcast to all channels"),
            BotCommand("publish", "Publish to group"),
            BotCommand("on", "Turn monitoring ON"),
            BotCommand("off", "Turn monitoring OFF"),
        ]
        
        await application.bot.set_my_commands(commands)
        logger.info("✅ Bot commands menu setup completed")
    except Exception as e:
        logger.error(f"Failed to setup bot commands: {e}")

async def setup_periodic_check(application: Application):
    """Setup periodic channel status check"""
    try:
        job_queue = application.job_queue
        check_interval = int(await get_config('check_interval') or 3600)
        
        # Remove existing jobs if any
        jobs = job_queue.get_jobs_by_name('channel_check')
        for job in jobs:
            job.schedule_removal()
        
        # Add new job if interval > 0
        if check_interval > 0:
            job_queue.run_repeating(
                check_channel_status,
                interval=check_interval,
                first=60,  # Start after 1 minute
                name='channel_check'
            )
            logger.info(f"⏰ Periodic check scheduled every {seconds_to_readable(check_interval)}")
    except Exception as e:
        logger.error(f"Failed to setup periodic check: {e}")

async def shutdown():
    """Graceful shutdown"""
    global is_shutting_down
    is_shutting_down = True
    logger.info("🛑 Starting graceful shutdown...")
    
    # Wait a moment for ongoing operations
    await asyncio.sleep(2)
    
    # Close database pool
    if db_pool:
        try:
            await db_pool.close()
            logger.info("✅ Database connection closed")
        except Exception as e:
            logger.error(f"Error closing database: {e}")
    
    logger.info("👋 Bot shutdown complete")
    sys.exit(0)

async def main():
    """Main async function - Entry point for Render"""
    # Set up signal handlers for graceful shutdown
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, f: asyncio.create_task(shutdown()))
    
    # Get environment variables
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    version = os.getenv('BOT_VERSION', '2.0.0')
    
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN environment variable is not set!")
        logger.error("Please set it in Render environment variables")
        return
    
    logger.info(f"🚀 Starting Channel Monitor Bot v{version}...")
    
    # Initialize database
    if not await init_db():
        logger.error("❌ Failed to initialize database!")
        logger.error("Please check your DATABASE_URL and PostgreSQL connection")
        return
    
    # Create application
    try:
        application = Application.builder().token(token).build()
        application.add_error_handler(error_handler)
        logger.info("✅ Telegram application created")
    except Exception as e:
        logger.error(f"❌ Failed to create application: {e}")
        return
    
    # Add command handlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("add_admin", add_admin_cmd),
        CommandHandler("remove_admin", remove_admin_cmd),
        CommandHandler("add_channel", add_channel_cmd),
        CommandHandler("remove_channel", remove_channel_cmd),
        CommandHandler("time_period", time_period_cmd),
        CommandHandler("test_message", test_message_cmd),
        CommandHandler("delete_interval", delete_interval_cmd),
        CommandHandler("list", list_channels_cmd),
        CommandHandler("status", status_cmd),
        CommandHandler("broadcast", broadcast_cmd),
        CommandHandler("on", bot_on_cmd),
        CommandHandler("off", bot_off_cmd),
        CommandHandler("usercount", usercount_cmd),
        CommandHandler("create_group", create_group_cmd),
        CommandHandler("add_to_group", add_to_group_cmd),
        CommandHandler("remove_from_group", remove_from_group_cmd),
        CommandHandler("list_groups", list_groups_cmd),
        CommandHandler("delete_group", delete_group_cmd),
        CommandHandler("publish", publish_cmd),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message),
    ]
    
    for handler in handlers:
        application.add_handler(handler)
    
    logger.info(f"✅ {len(handlers)} command handlers registered")
    
    # Start web server in background for health checks
    asyncio.create_task(start_web_server())
    
    # Initialize the bot
    try:
        await application.initialize()
        logger.info("✅ Bot initialized")
        
        # Setup bot commands
        await setup_bot_commands(application)
        
        # Setup periodic checks
        await setup_periodic_check(application)
        
        # Start the bot
        await application.start()
        await application.updater.start_polling(drop_pending_updates=True)
        
        bot_info = await application.bot.get_me()
        logger.info(f"🤖 Bot @{bot_info.username} is now running!")
        logger.info(f"👤 Bot ID: {bot_info.id}")
        logger.info("✅ Bot is ready to receive commands")
        
        # Keep running until shutdown
        logger.info("📡 Listening for updates...")
        await asyncio.Event().wait()
        
    except KeyboardInterrupt:
        logger.info("⚠️ Received keyboard interrupt")
    except Exception as e:
        logger.error(f"❌ Bot crashed: {e}")
    finally:
        # Clean shutdown
        if not is_shutting_down:
            await shutdown()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Bot stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
