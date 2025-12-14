import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Set, Optional
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError
import asyncpg
from aiohttp import web

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Database connection pool
db_pool = None

async def init_db():
    """Initialize database connection pool and create tables"""
    global db_pool
    database_url = os.getenv('DATABASE_URL')
    
    if not database_url:
        logger.error("DATABASE_URL not set!")
        return False
    
    try:
        db_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
        
        # Create tables
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
            
            # Set default config if not exists
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ('owner', '0')
                ON CONFLICT (key) DO NOTHING
            ''')
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ('check_interval', '3600')
                ON CONFLICT (key) DO NOTHING
            ''')
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ('test_message', '✓ Status Check')
                ON CONFLICT (key) DO NOTHING
            ''')
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ('delete_interval', '3')
                ON CONFLICT (key) DO NOTHING
            ''')
            await conn.execute('''
                INSERT INTO config (key, value) VALUES ('bot_active', 'true')
                ON CONFLICT (key) DO NOTHING
            ''')
        
        logger.info("Database initialized successfully!")
        return True
    except Exception as e:
        logger.error(f"Database init error: {e}")
        return False

async def get_config(key: str) -> Optional[str]:
    """Get config value from database"""
    async with db_pool.acquire() as conn:
        result = await conn.fetchval('SELECT value FROM config WHERE key = $1', key)
        return result

async def set_config(key: str, value: str):
    """Set config value in database"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO config (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = $2
        ''', key, value)

async def get_owner() -> int:
    """Get owner ID"""
    val = await get_config('owner')
    return int(val) if val else 0

async def set_owner(user_id: int):
    """Set owner ID"""
    await set_config('owner', str(user_id))

async def is_admin(user_id: int) -> bool:
    """Check if user is admin or owner"""
    owner = await get_owner()
    if user_id == owner:
        return True
    
    async with db_pool.acquire() as conn:
        result = await conn.fetchval(
            'SELECT user_id FROM admins WHERE user_id = $1', user_id
        )
        return result is not None

async def add_admin(user_id: int):
    """Add admin to database"""
    async with db_pool.acquire() as conn:
        await conn.execute(
            'INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING',
            user_id
        )

async def remove_admin(user_id: int):
    """Remove admin from database"""
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM admins WHERE user_id = $1', user_id)

async def get_admins_count() -> int:
    """Get number of admins"""
    async with db_pool.acquire() as conn:
        count = await conn.fetchval('SELECT COUNT(*) FROM admins')
        return count

async def get_all_admins() -> List[int]:
    """Get all admin IDs"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id FROM admins')
        return [row['user_id'] for row in rows]

async def add_channel(channel_id: str, channel_name: str):
    """Add channel to database"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            INSERT INTO channels (channel_id, channel_name)
            VALUES ($1, $2)
            ON CONFLICT (channel_id) DO UPDATE SET channel_name = $2
        ''', channel_id, channel_name)

async def remove_channel(channel_id: str):
    """Remove channel from database"""
    async with db_pool.acquire() as conn:
        await conn.execute('DELETE FROM channels WHERE channel_id = $1', channel_id)

async def get_all_channels() -> Dict[str, str]:
    """Get all channels"""
    async with db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT channel_id, channel_name FROM channels')
        return {row['channel_id']: row['channel_name'] for row in rows}

async def update_channel_status(channel_id: str, status: str):
    """Update channel status"""
    async with db_pool.acquire() as conn:
        await conn.execute('''
            UPDATE channels 
            SET status = $2, last_check = NOW()
            WHERE channel_id = $1
        ''', channel_id, status)

def parse_time_to_seconds(time_str: str) -> int:
    """Convert time string to seconds"""
    time_str = time_str.lower().strip()
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

def seconds_to_readable(seconds: int) -> str:
    """Convert seconds to readable format"""
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user_id = update.effective_user.id
    version = os.getenv('BOT_VERSION', '1.0.0')
    owner = await get_owner()
    
    if owner == 0:
        await set_owner(user_id)
        await update.message.reply_text(
            f"🎉 Welcome! You are now the Owner.\n\n"
            f"🤖 Bot Version: {version}\n\n"
            "Use /help to see all available commands."
        )
    else:
        bot_active = await get_config('bot_active')
        status = "🟢 ACTIVE" if bot_active == 'true' else "🔴 INACTIVE"
        channels = await get_all_channels()
        admins_count = await get_admins_count()
        
        await update.message.reply_text(
            f"👋 Channel Monitor Bot\n\n"
            f"🤖 Version: {version}\n"
            f"Status: {status}\n"
            f"Owner: {owner}\n"
            f"Admins: {admins_count}\n"
            f"Channels: {len(channels)}\n\n"
            "Use /help to see all commands."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command"""
    user_id = update.effective_user.id
    
    if await is_admin(user_id):
        help_text = (
            "📋 *Channel Monitor Bot Commands*\n\n"
            "👥 *Admin Management:*\n"
            "/add\\_admin `<user_id>` \\- Add admin\n"
            "/remove\\_admin `<user_id>` \\- Remove admin\n\n"
            "📢 *Channel Management:*\n"
            "/add\\_channel `<@username or ID>` `<n>` \\- Add channel\n"
            "/remove\\_channel `<@username or ID>` \\- Remove channel\n"
            "/list \\- Show all monitored channels\n\n"
            "⚙️ *Configuration:*\n"
            "/time\\_period `<time>` \\- Set check interval\n"
            "   Examples: `30s`, `5m`, `1h`, `12h`, `1d`\n"
            "/test\\_message `<text>` \\- Set test message\n"
            "/delete\\_interval `<time>` \\- Set delete time\n"
            "/status \\- Show current settings\n\n"
            "🔧 *Operations:*\n"
            "/broadcast `<message>` \\- Send to all channels\n"
            "/on \\- Turn monitoring ON 🟢\n"
            "/off \\- Turn monitoring OFF 🔴\n"
            "/help \\- Show this help"
        )
    else:
        help_text = (
            "📋 *Channel Monitor Bot*\n\n"
            "Available commands:\n"
            "/start \\- Start the bot\n"
            "/help \\- Show this message"
        )
    
    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add admin command"""
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
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /add_channel <@username or -100ID> <n>\n\n"
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
            f"ID: {channel_id}\n"
            f"Name: {channel_name}\n"
            f"Actual: {actual_name}\n"
            f"Total: {len(channels)}"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Warning: Could not verify access.\n"
            f"Make sure bot is admin in the channel.\n\n"
            f"Channel added anyway."
        )
        await add_channel(channel_id, channel_name)

async def remove_channel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove channel command"""
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

async def time_period_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set time period command"""
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
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    channels = await get_all_channels()
    
    if not channels:
        await update.message.reply_text("📋 No channels being monitored.")
        return
    
    channels_list = []
    for idx, (ch_id, ch_name) in enumerate(channels.items(), 1):
        channels_list.append(f"{idx}. {ch_name}\n   ID: {ch_id}")
    
    msg = "📋 Monitored Channels:\n\n" + "\n\n".join(channels_list)
    await update.message.reply_text(msg)

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Status command"""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_active = await get_config('bot_active')
    bot_status = "🟢 ACTIVE" if bot_active == 'true' else "🔴 INACTIVE"
    
    check_interval = int(await get_config('check_interval'))
    delete_interval = int(await get_config('delete_interval'))
    test_message = await get_config('test_message')
    owner = await get_owner()
    admins_count = await get_admins_count()
    channels = await get_all_channels()
    
    check_readable = seconds_to_readable(check_interval)
    delete_readable = seconds_to_readable(delete_interval)
    
    status_msg = (
        f"⚙️ Bot Configuration:\n\n"
        f"Status: {bot_status}\n"
        f"👤 Owner: {owner}\n"
        f"👥 Admins: {admins_count}\n"
        f"📢 Channels: {len(channels)}\n"
        f"⏱ Check Interval: {check_readable}\n"
        f"🗑 Delete Interval: {delete_readable}\n"
        f"💬 Test Message: {test_message}"
    )
    await update.message.reply_text(status_msg)

async def bot_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn bot off command"""
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
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_active = await get_config('bot_active')
    if bot_active == 'true':
        await update.message.reply_text("ℹ️ Bot already active.")
        return
    
    await set_config('bot_active', 'true')
    check_interval = int(await get_config('check_interval'))
    await update.message.reply_text(
        "🟢 Bot monitoring turned ON\n\n"
        f"Check interval: {seconds_to_readable(check_interval)}"
    )
    logger.info("Bot turned ON")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast command"""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <your message>")
        return
    
    channels = await get_all_channels()
    if not channels:
        await update.message.reply_text("❌ No channels to broadcast to.")
        return
    
    message = ' '.join(context.args)
    successful = 0
    failed = 0
    failed_channels = []
    
    status_msg = await update.message.reply_text("📡 Broadcasting...")
    
    for channel_id, channel_name in channels.items():
        try:
            await context.bot.send_message(chat_id=channel_id, text=message)
            successful += 1
            logger.info(f"Broadcast OK: {channel_name}")
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Broadcast failed: {channel_name} - {e}")
            failed += 1
            failed_channels.append(f"{channel_name}: {str(e)[:50]}")
    
    report = (
        f"📊 Broadcast Report:\n\n"
        f"✅ Successful: {successful}\n"
        f"❌ Failed: {failed}\n"
        f"📢 Total: {len(channels)}"
    )
    
    if failed_channels:
        report += "\n\n⚠️ Failed:\n" + "\n".join([f"• {fc}" for fc in failed_channels[:5]])
        if len(failed_channels) > 5:
            report += f"\n... and {len(failed_channels) - 5} more"
    
    await status_msg.edit_text(report)

async def check_channel_status(context: ContextTypes.DEFAULT_TYPE):
    """Check channel status periodically"""
    bot_active = await get_config('bot_active')
    if bot_active != 'true':
        logger.info("Bot inactive. Skipping check.")
        return
    
    channels = await get_all_channels()
    if not channels:
        logger.info("No channels to check.")
        return
    
    test_message = await get_config('test_message')
    delete_interval = int(await get_config('delete_interval'))
    
    logger.info(f"Checking {len(channels)} channels...")
    
    for channel_id, channel_name in channels.items():
        try:
            logger.info(f"Checking {channel_name}...")
            msg = await context.bot.send_message(
                chat_id=channel_id,
                text=test_message
            )
            
            await asyncio.sleep(delete_interval)
            
            try:
                await msg.delete()
                logger.info(f"✅ {channel_name} OK")
                await update_channel_status(channel_id, 'active')
            except Exception as del_error:
                logger.warning(f"Sent but can't delete: {channel_name}")
                await update_channel_status(channel_id, 'active_no_delete')
            
        except Exception as e:
            logger.warning(f"⚠️ First attempt failed: {channel_name}")
            
            try:
                await asyncio.sleep(2)
                msg = await context.bot.send_message(
                    chat_id=channel_id,
                    text=test_message
                )
                
                await asyncio.sleep(delete_interval)
                
                try:
                    await msg.delete()
                    logger.info(f"✅ {channel_name} OK on retry")
                    await update_channel_status(channel_id, 'active')
                except Exception:
                    logger.warning(f"Retry sent but can't delete: {channel_name}")
                    await update_channel_status(channel_id, 'active_no_delete')
                    
            except Exception as e2:
                logger.error(f"❌ {channel_name} appears BANNED: {e2}")
                await update_channel_status(channel_id, 'banned')
                
                warning_msg = (
                    f"⚠️ CHANNEL ALERT ⚠️\n\n"
                    f"📢 Channel: {channel_name}\n"
                    f"🆔 ID: {channel_id}\n"
                    f"🔴 Status: Possibly BANNED\n"
                    f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"❌ Failed after 2 attempts"
                )
                
                owner = await get_owner()
                if owner != 0:
                    try:
                        await context.bot.send_message(
                            chat_id=owner,
                            text=warning_msg
                        )
                    except Exception as err:
                        logger.error(f"Can't notify owner: {err}")
                
                admins = await get_all_admins()
                for admin_id in admins:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=warning_msg
                        )
                        await asyncio.sleep(0.3)
                    except Exception as err:
                        logger.error(f"Can't notify admin {admin_id}: {err}")
        
        await asyncio.sleep(1)
    
    logger.info("Check completed.")

async def setup_periodic_check(application: Application):
    """Setup periodic check"""
    check_interval = int(await get_config('check_interval'))
    job_queue = application.job_queue
    job_queue.run_repeating(
        check_channel_status,
        interval=check_interval,
        first=10,
        name='channel_check'
    )
    logger.info(f"Check scheduled every {seconds_to_readable(check_interval)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Error handler"""
    logger.error(f"Error: {context.error}")

async def health_check(request):
    """Health check endpoint"""
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
    """Start web server for Render"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    port = int(os.getenv('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"Web server started on port {port}")

def main():
    """Main function"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    version = os.getenv('BOT_VERSION', '1.0.0')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    logger.info(f"Starting Bot v{version}")
    
    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_admin", add_admin_cmd))
    application.add_handler(CommandHandler("remove_admin", remove_admin_cmd))
    application.add_handler(CommandHandler("add_channel", add_channel_cmd))
    application.add_handler(CommandHandler("remove_channel", remove_channel_cmd))
    application.add_handler(CommandHandler("time_period", time_period_cmd))
    application.add_handler(CommandHandler("test_message", test_message_cmd))
    application.add_handler(CommandHandler("delete_interval", delete_interval_cmd))
    application.add_handler(CommandHandler("list", list_channels_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CommandHandler("on", bot_on_cmd))
    application.add_handler(CommandHandler("off", bot_off_cmd))
    
    application.post_init = setup_periodic_check
    
    async def run_bot():
        # Initialize database
        if not await init_db():
            logger.error("Failed to initialize database!")
            return
        
        await start_web_server()
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Bot polling started!")
        
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping...")
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
            if db_pool:
                await db_pool.close()
    
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
