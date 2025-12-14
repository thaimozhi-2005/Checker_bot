import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Set
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError
import json
from aiohttp import web

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Data storage
class BotData:
    def __init__(self):
        self.owner: int = None
        self.admins: Set[int] = set()
        self.channels: Dict[str, str] = {}
        self.check_interval: int = 3600
        self.test_message: str = "✓ Status Check"
        self.delete_interval: int = 3
        self.bot_active: bool = True
        self.load_data()
    
    def load_data(self):
        try:
            if os.path.exists('bot_data.json'):
                with open('bot_data.json', 'r') as f:
                    data = json.load(f)
                    self.owner = data.get('owner')
                    self.admins = set(data.get('admins', []))
                    
                    channels_data = data.get('channels', {})
                    if isinstance(channels_data, list):
                        self.channels = {ch: f"Channel_{i+1}" for i, ch in enumerate(channels_data)}
                        self.save_data()
                    else:
                        self.channels = channels_data
                    
                    self.check_interval = data.get('check_interval', 3600)
                    self.test_message = data.get('test_message', "✓ Status Check")
                    self.delete_interval = data.get('delete_interval', 3)
                    self.bot_active = data.get('bot_active', True)
                    logger.info(f"Data loaded: {len(self.channels)} channels")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    
    def save_data(self):
        try:
            with open('bot_data.json', 'w') as f:
                json.dump({
                    'owner': self.owner,
                    'admins': list(self.admins),
                    'channels': self.channels,
                    'check_interval': self.check_interval,
                    'test_message': self.test_message,
                    'delete_interval': self.delete_interval,
                    'bot_active': self.bot_active
                }, f, indent=2)
                logger.info("Data saved successfully")
        except Exception as e:
            logger.error(f"Error saving data: {e}")

bot_data = BotData()

def is_admin(user_id: int) -> bool:
    return user_id == bot_data.owner or user_id in bot_data.admins

def parse_time_to_seconds(time_str: str) -> int:
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
    user_id = update.effective_user.id
    version = os.getenv('BOT_VERSION', '1.0.0')
    
    if not bot_data.owner:
        bot_data.owner = user_id
        bot_data.save_data()
        await update.message.reply_text(
            f"🎉 Welcome! You are now the Owner.\n\n"
            f"🤖 Bot Version: {version}\n\n"
            "Use /help to see all available commands."
        )
    else:
        status = "🟢 ACTIVE" if bot_data.bot_active else "🔴 INACTIVE"
        await update.message.reply_text(
            f"👋 Channel Monitor Bot\n\n"
            f"🤖 Version: {version}\n"
            f"Status: {status}\n"
            f"Owner: {bot_data.owner}\n"
            f"Admins: {len(bot_data.admins)}\n"
            f"Channels: {len(bot_data.channels)}\n\n"
            "Use /help to see all commands."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        help_text = (
            "📋 *Channel Monitor Bot Commands*\n\n"
            "👥 *Admin Management:*\n"
            "/add\\_admin `<user_id>` \\- Add admin\n"
            "/remove\\_admin `<user_id>` \\- Remove admin\n\n"
            "📢 *Channel Management:*\n"
            "/add\\_channel `<@username or ID>` `<name>` \\- Add channel\n"
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

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != bot_data.owner:
        await update.message.reply_text("❌ Only the owner can add admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /add_admin <user_id>")
        return
    
    try:
        new_admin_id = int(context.args[0])
        if new_admin_id == bot_data.owner:
            await update.message.reply_text("❌ Owner cannot be added as admin.")
            return
        bot_data.admins.add(new_admin_id)
        bot_data.save_data()
        await update.message.reply_text(f"✅ Admin {new_admin_id} added!")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != bot_data.owner:
        await update.message.reply_text("❌ Only the owner can remove admins.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /remove_admin <user_id>")
        return
    
    try:
        admin_id = int(context.args[0])
        if admin_id in bot_data.admins:
            bot_data.admins.remove(admin_id)
            bot_data.save_data()
            await update.message.reply_text(f"✅ Admin {admin_id} removed!")
        else:
            await update.message.reply_text("❌ User is not an admin.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
        
        bot_data.channels[channel_id] = channel_name
        bot_data.save_data()
        await update.message.reply_text(
            f"✅ Channel added!\n\n"
            f"ID: {channel_id}\n"
            f"Name: {channel_name}\n"
            f"Actual: {actual_name}\n"
            f"Total: {len(bot_data.channels)}"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Warning: Could not verify access.\n"
            f"Make sure bot is admin in the channel.\n\n"
            f"Channel added anyway."
        )
        bot_data.channels[channel_id] = channel_name
        bot_data.save_data()

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /remove_channel <@username or ID>")
        return
    
    channel_id = context.args[0]
    if channel_id in bot_data.channels:
        name = bot_data.channels[channel_id]
        del bot_data.channels[channel_id]
        bot_data.save_data()
        await update.message.reply_text(f"✅ Channel '{name}' removed!")
    else:
        await update.message.reply_text("❌ Channel not found.")

async def time_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
        bot_data.check_interval = interval
        bot_data.save_data()
        
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
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /test_message <your message>")
        return
    
    message = ' '.join(context.args)
    bot_data.test_message = message
    bot_data.save_data()
    await update.message.reply_text(f"✅ Test message set to:\n\n{message}")

async def delete_interval_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
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
        bot_data.delete_interval = interval
        bot_data.save_data()
        readable = seconds_to_readable(interval)
        await update.message.reply_text(f"✅ Delete interval set to {readable}!")
    except ValueError:
        await update.message.reply_text("❌ Invalid format. Use: 3s, 10s, 1m, etc.")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not bot_data.channels:
        await update.message.reply_text("📋 No channels being monitored.")
        return
    
    channels_list = []
    for idx, (ch_id, ch_name) in enumerate(bot_data.channels.items(), 1):
        channels_list.append(f"{idx}. {ch_name}\n   ID: {ch_id}")
    
    msg = "📋 Monitored Channels:\n\n" + "\n\n".join(channels_list)
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    bot_status = "🟢 ACTIVE" if bot_data.bot_active else "🔴 INACTIVE"
    check_readable = seconds_to_readable(bot_data.check_interval)
    delete_readable = seconds_to_readable(bot_data.delete_interval)
    
    status_msg = (
        f"⚙️ Bot Configuration:\n\n"
        f"Status: {bot_status}\n"
        f"👤 Owner: {bot_data.owner}\n"
        f"👥 Admins: {len(bot_data.admins)}\n"
        f"📢 Channels: {len(bot_data.channels)}\n"
        f"⏱ Check Interval: {check_readable}\n"
        f"🗑 Delete Interval: {delete_readable}\n"
        f"💬 Test Message: {bot_data.test_message}"
    )
    await update.message.reply_text(status_msg)

async def bot_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not bot_data.bot_active:
        await update.message.reply_text("ℹ️ Bot already inactive.")
        return
    
    bot_data.bot_active = False
    bot_data.save_data()
    await update.message.reply_text(
        "🔴 Bot monitoring turned OFF\n\n"
        "Use /on to resume."
    )
    logger.info("Bot turned OFF")

async def bot_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if bot_data.bot_active:
        await update.message.reply_text("ℹ️ Bot already active.")
        return
    
    bot_data.bot_active = True
    bot_data.save_data()
    await update.message.reply_text(
        "🟢 Bot monitoring turned ON\n\n"
        f"Check interval: {seconds_to_readable(bot_data.check_interval)}"
    )
    logger.info("Bot turned ON")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text("Usage: /broadcast <your message>")
        return
    
    if not bot_data.channels:
        await update.message.reply_text("❌ No channels to broadcast to.")
        return
    
    message = ' '.join(context.args)
    successful = 0
    failed = 0
    failed_channels = []
    
    status_msg = await update.message.reply_text("📡 Broadcasting...")
    
    for channel_id, channel_name in bot_data.channels.items():
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
        f"📢 Total: {len(bot_data.channels)}"
    )
    
    if failed_channels:
        report += "\n\n⚠️ Failed:\n" + "\n".join([f"• {fc}" for fc in failed_channels[:5]])
        if len(failed_channels) > 5:
            report += f"\n... and {len(failed_channels) - 5} more"
    
    await status_msg.edit_text(report)

async def check_channel_status(context: ContextTypes.DEFAULT_TYPE):
    if not bot_data.bot_active:
        logger.info("Bot inactive. Skipping check.")
        return
    
    if isinstance(bot_data.channels, list):
        logger.warning("Converting channels to dict...")
        new_channels = {ch: f"Channel_{i+1}" for i, ch in enumerate(bot_data.channels)}
        bot_data.channels = new_channels
        bot_data.save_data()
    
    if not bot_data.channels:
        logger.info("No channels to check.")
        return
    
    logger.info(f"Checking {len(bot_data.channels)} channels...")
    
    for channel_id, channel_name in bot_data.channels.items():
        try:
            logger.info(f"Checking {channel_name}...")
            msg = await context.bot.send_message(
                chat_id=channel_id,
                text=bot_data.test_message
            )
            
            await asyncio.sleep(bot_data.delete_interval)
            
            try:
                await msg.delete()
                logger.info(f"✅ {channel_name} OK")
            except Exception as del_error:
                logger.warning(f"Sent but can't delete: {channel_name}")
            
        except Exception as e:
            logger.warning(f"⚠️ First attempt failed: {channel_name}")
            
            try:
                await asyncio.sleep(2)
                msg = await context.bot.send_message(
                    chat_id=channel_id,
                    text=bot_data.test_message
                )
                
                await asyncio.sleep(bot_data.delete_interval)
                
                try:
                    await msg.delete()
                    logger.info(f"✅ {channel_name} OK on retry")
                except Exception:
                    logger.warning(f"Retry sent but can't delete: {channel_name}")
                    
            except Exception as e2:
                logger.error(f"❌ {channel_name} appears BANNED: {e2}")
                
                warning_msg = (
                    f"⚠️ CHANNEL ALERT ⚠️\n\n"
                    f"📢 Channel: {channel_name}\n"
                    f"🆔 ID: {channel_id}\n"
                    f"🔴 Status: Possibly BANNED\n"
                    f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"❌ Failed after 2 attempts"
                )
                
                if bot_data.owner:
                    try:
                        await context.bot.send_message(
                            chat_id=bot_data.owner,
                            text=warning_msg
                        )
                    except Exception as err:
                        logger.error(f"Can't notify owner: {err}")
                
                for admin_id in bot_data.admins:
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
    job_queue = application.job_queue
    job_queue.run_repeating(
        check_channel_status,
        interval=bot_data.check_interval,
        first=10,
        name='channel_check'
    )
    logger.info(f"Check scheduled every {seconds_to_readable(bot_data.check_interval)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {context.error}")

async def health_check(request):
    return web.Response(text="Bot is running!", status=200)

async def start_web_server():
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
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    version = os.getenv('BOT_VERSION', '1.0.0')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set!")
        return
    
    logger.info(f"Starting Bot v{version}")
    
    application = Application.builder().token(token).build()
    application.add_error_handler(error_handler)
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add_admin", add_admin))
    application.add_handler(CommandHandler("remove_admin", remove_admin))
    application.add_handler(CommandHandler("add_channel", add_channel))
    application.add_handler(CommandHandler("remove_channel", remove_channel))
    application.add_handler(CommandHandler("time_period", time_period))
    application.add_handler(CommandHandler("test_message", test_message_cmd))
    application.add_handler(CommandHandler("delete_interval", delete_interval_cmd))
    application.add_handler(CommandHandler("list", list_channels))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CommandHandler("on", bot_on))
    application.add_handler(CommandHandler("off", bot_off))
    
    application.post_init = setup_periodic_check
    
    async def run_bot():
        await start_web_server()
        await application.initialize()
        await application.start()
        await application.updater.start_polling()
        logger.info("Bot polling started!")
        logger.info(f"Active: {bot_data.bot_active}")
        logger.info(f"Channels: {len(bot_data.channels)}")
        
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping...")
        finally:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
    
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
