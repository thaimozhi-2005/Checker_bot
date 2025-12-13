import os
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Set
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError
import json

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Data storage (in production, use a database)
class BotData:
    def __init__(self):
        self.owner: int = None
        self.admins: Set[int] = set()
        self.channels: Dict[str, str] = {}  # {id_or_username: name}
        self.check_interval: int = 3600  # 1 hour default
        self.test_message: str = "✓ Status Check"
        self.delete_interval: int = 3  # 3 seconds default
        self.bot_active: bool = True  # Bot on/off status
        self.load_data()
    
    def load_data(self):
        """Load data from file if exists"""
        try:
            if os.path.exists('bot_data.json'):
                with open('bot_data.json', 'r') as f:
                    data = json.load(f)
                    self.owner = data.get('owner')
                    self.admins = set(data.get('admins', []))
                    
                    # Handle both old list format and new dict format
                    channels_data = data.get('channels', {})
                    if isinstance(channels_data, list):
                        # Convert old list format to dict
                        self.channels = {ch: f"Channel_{i+1}" for i, ch in enumerate(channels_data)}
                        self.save_data()  # Save in new format
                    else:
                        self.channels = channels_data
                    
                    self.check_interval = data.get('check_interval', 3600)
                    self.test_message = data.get('test_message', "✓ Status Check")
                    self.delete_interval = data.get('delete_interval', 3)
                    self.bot_active = data.get('bot_active', True)
                    logger.info(f"Data loaded: {len(self.channels)} channels, bot_active={self.bot_active}")
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    
    def save_data(self):
        """Save data to file"""
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
    """Check if user is an admin or owner"""
    return user_id == bot_data.owner or user_id in bot_data.admins

def parse_time_to_seconds(time_str: str) -> int:
    """Convert time string to seconds (e.g., '1h', '30m', '2d')"""
    time_str = time_str.lower().strip()
    
    # Try to parse number with unit
    if time_str.endswith('s'):
        return int(time_str[:-1])
    elif time_str.endswith('m'):
        return int(time_str[:-1]) * 60
    elif time_str.endswith('h'):
        return int(time_str[:-1]) * 3600
    elif time_str.endswith('d'):
        return int(time_str[:-1]) * 86400
    else:
        # Assume seconds if no unit
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
    """Start command handler"""
    user_id = update.effective_user.id
    
    if not bot_data.owner:
        # First user becomes owner
        bot_data.owner = user_id
        bot_data.save_data()
        await update.message.reply_text(
            "🎉 Welcome! You are now the Owner.\n\n"
            "Use /help to see all available commands."
        )
    else:
        status = "🟢 ACTIVE" if bot_data.bot_active else "🔴 INACTIVE"
        await update.message.reply_text(
            f"👋 Channel Monitor Bot\n\n"
            f"Status: {status}\n"
            f"Owner: {bot_data.owner}\n"
            f"Admins: {len(bot_data.admins)}\n"
            f"Channels: {len(bot_data.channels)}\n\n"
            "Use /help to see all commands."
        )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help message with all commands"""
    user_id = update.effective_user.id
    
    if is_admin(user_id):
        help_text = (
            "📋 *Channel Monitor Bot Commands*\n\n"
            
            "👥 *Admin Management:*\n"
            "/add\\_admin `<user_id>` \\- Add admin \\(owner only\\)\n"
            "/remove\\_admin `<user_id>` \\- Remove admin \\(owner only\\)\n\n"
            
            "📢 *Channel Management:*\n"
            "/add\\_channel `<@username or ID>` `<name>` \\- Add channel\n"
            "   Example: `/add_channel @mychannel My Channel`\n"
            "   Example: `/add_channel -1001234567890 Private Channel`\n"
            "/remove\\_channel `<@username or ID>` \\- Remove channel\n"
            "/list \\- Show all monitored channels\n\n"
            
            "⚙️ *Configuration:*\n"
            "/time\\_period `<time>` \\- Set check interval\n"
            "   Examples: `30s`, `5m`, `1h`, `12h`, `1d`, `2d`\n"
            "/test\\_message `<text>` \\- Set test message\n"
            "/delete\\_interval `<time>` \\- Set delete time\n"
            "   Examples: `3s`, `10s`, `30s`, `1m`\n"
            "/status \\- Show current settings\n\n"
            
            "🔧 *Operations:*\n"
            "/broadcast `<message>` \\- Send message to all channels\n"
            "/on \\- Turn bot monitoring ON 🟢\n"
            "/off \\- Turn bot monitoring OFF 🔴\n"
            "/help \\- Show this help message\n\n"
            
            "⚠️ *Important:*\n"
            "Make bot admin in all channels with:\n"
            "• Post Messages permission\n"
            "• Delete Messages permission"
        )
    else:
        help_text = (
            "📋 *Channel Monitor Bot*\n\n"
            "This bot monitors Telegram channels to check if they're banned or blocked\\.\n\n"
            "Available commands:\n"
            "/start \\- Start the bot\n"
            "/help \\- Show this message\n\n"
            "Contact the owner or an admin for access\\."
        )
    
    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a new admin"""
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
        await update.message.reply_text(f"✅ Admin {new_admin_id} added successfully!")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Please provide a numeric ID.")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove an admin"""
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
            await update.message.reply_text(f"✅ Admin {admin_id} removed successfully!")
        else:
            await update.message.reply_text("❌ User is not an admin.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a channel to monitor"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if len(context.args) < 2:
        await update.message.reply_text(
            "Usage: /add_channel <@username or -100ID> <channel_name>\n\n"
            "Examples:\n"
            "/add_channel @mychannel My Channel\n"
            "/add_channel -1001234567890 Private Channel"
        )
        return
    
    channel_id = context.args[0]
    channel_name = ' '.join(context.args[1:])
    
    # Test if bot can access the channel
    try:
        # Try to get chat info
        chat = await context.bot.get_chat(channel_id)
        actual_name = chat.title or channel_name
        
        bot_data.channels[channel_id] = channel_name
        bot_data.save_data()
        await update.message.reply_text(
            f"✅ Channel added successfully!\n\n"
            f"ID: {channel_id}\n"
            f"Name: {channel_name}\n"
            f"Actual: {actual_name}\n\n"
            f"Total channels: {len(bot_data.channels)}"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Warning: Could not verify channel access.\n"
            f"Error: {str(e)}\n\n"
            f"Make sure:\n"
            f"1. Bot is admin in the channel\n"
            f"2. Channel ID/username is correct\n"
            f"3. Bot has 'Post Messages' permission\n\n"
            f"Channel added anyway. Check /list to verify."
        )
        bot_data.channels[channel_id] = channel_name
        bot_data.save_data()

async def remove_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a channel from monitoring"""
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
        await update.message.reply_text(f"✅ Channel '{name}' removed successfully!")
    else:
        await update.message.reply_text("❌ Channel not found in the list.")

async def time_period(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the check interval"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /time_period <time>\n\n"
            "Examples:\n"
            "30s = 30 seconds\n"
            "5m = 5 minutes\n"
            "1h = 1 hour\n"
            "12h = 12 hours\n"
            "1d = 1 day\n"
            "2d = 2 days"
        )
        return
    
    try:
        interval = parse_time_to_seconds(context.args[0])
        if interval < 30:
            await update.message.reply_text("❌ Interval must be at least 30 seconds.")
            return
        bot_data.check_interval = interval
        bot_data.save_data()
        
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
        await update.message.reply_text(f"✅ Check interval set to {readable} ({interval}s)!\n\nNew schedule will start in 10 seconds.")
    except ValueError:
        await update.message.reply_text("❌ Invalid time format. Use: 30s, 5m, 1h, 2d, etc.")

async def test_message_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the test message"""
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
    """Set the delete interval"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "Usage: /delete_interval <time>\n\n"
            "Examples:\n"
            "3s = 3 seconds\n"
            "10s = 10 seconds\n"
            "1m = 1 minute"
        )
        return
    
    try:
        interval = parse_time_to_seconds(context.args[0])
        if interval < 1:
            await update.message.reply_text("❌ Interval must be at least 1 second.")
            return
        bot_data.delete_interval = interval
        bot_data.save_data()
        readable = seconds_to_readable(interval)
        await update.message.reply_text(f"✅ Delete interval set to {readable} ({interval}s)!")
    except ValueError:
        await update.message.reply_text("❌ Invalid time format. Use: 3s, 10s, 1m, etc.")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all monitored channels"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not bot_data.channels:
        await update.message.reply_text("📋 No channels are being monitored.")
        return
    
    channels_list = []
    for idx, (ch_id, ch_name) in enumerate(bot_data.channels.items(), 1):
        channels_list.append(f"{idx}. {ch_name}\n   ID: {ch_id}")
    
    msg = "📋 Monitored Channels:\n\n" + "\n\n".join(channels_list)
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current settings"""
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
    """Turn off bot monitoring"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if not bot_data.bot_active:
        await update.message.reply_text("ℹ️ Bot is already inactive.")
        return
    
    bot_data.bot_active = False
    bot_data.save_data()
    await update.message.reply_text(
        "🔴 Bot monitoring turned OFF\n\n"
        "The bot will not check channels or send alerts.\n"
        "Use /on to resume monitoring."
    )
    logger.info("Bot monitoring turned OFF by admin")

async def bot_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Turn on bot monitoring"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Only admins can use this command.")
        return
    
    if bot_data.bot_active:
        await update.message.reply_text("ℹ️ Bot is already active.")
        return
    
    bot_data.bot_active = True
    bot_data.save_data()
    await update.message.reply_text(
        "🟢 Bot monitoring turned ON\n\n"
        "Channel monitoring has resumed.\n"
        f"Check interval: {seconds_to_readable(bot_data.check_interval)}"
    )
    logger.info("Bot monitoring turned ON by admin")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast a message to all channels"""
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
            logger.info(f"Broadcast successful to {channel_name} ({channel_id})")
            await asyncio.sleep(0.5)  # Avoid rate limits
        except Exception as e:
            logger.error(f"Failed to broadcast to {channel_name} ({channel_id}): {e}")
            failed += 1
            failed_channels.append(f"{channel_name}: {str(e)[:50]}")
    
    report = (
        f"📊 Broadcast Report:\n\n"
        f"✅ Successful: {successful}\n"
        f"❌ Failed: {failed}\n"
        f"📢 Total: {len(bot_data.channels)}"
    )
    
    if failed_channels:
        report += "\n\n⚠️ Failed Channels:\n" + "\n".join([f"• {fc}" for fc in failed_channels[:5]])
        if len(failed_channels) > 5:
            report += f"\n... and {len(failed_channels) - 5} more"
    
    await status_msg.edit_text(report)

async def check_channel_status(context: ContextTypes.DEFAULT_TYPE):
    """Periodic task to check channel status"""
    
    # Check if bot is active
    if not bot_data.bot_active:
        logger.info("Bot is inactive. Skipping channel check.")
        return
    
    if not bot_data.channels:
        logger.info("No channels to check.")
        return
    
    logger.info(f"Starting channel check for {len(bot_data.channels)} channels...")
    
    for channel_id, channel_name in bot_data.channels.items():
        try:
            # First attempt
            logger.info(f"Checking {channel_name} ({channel_id})...")
            msg = await context.bot.send_message(
                chat_id=channel_id,
                text=bot_data.test_message
            )
            
            # Wait before deleting
            await asyncio.sleep(bot_data.delete_interval)
            
            # Delete message
            try:
                await msg.delete()
                logger.info(f"✅ {channel_name} is alive and message deleted")
            except Exception as del_error:
                logger.warning(f"Message sent but couldn't delete from {channel_name}: {del_error}")
            
        except Exception as e:
            logger.warning(f"⚠️ First attempt failed for {channel_name}: {e}")
            
            # Second attempt after 2 seconds
            try:
                await asyncio.sleep(2)
                msg = await context.bot.send_message(
                    chat_id=channel_id,
                    text=bot_data.test_message
                )
                
                await asyncio.sleep(bot_data.delete_interval)
                
                try:
                    await msg.delete()
                    logger.info(f"✅ {channel_name} is alive on retry (message deleted)")
                except Exception as del_error:
                    logger.warning(f"Retry message sent but couldn't delete from {channel_name}: {del_error}")
                    
            except Exception as e2:
                logger.error(f"❌ Channel {channel_name} appears BANNED or inaccessible: {e2}")
                
                # Alert all admins and owner
                warning_msg = (
                    f"⚠️ CHANNEL ALERT ⚠️\n\n"
                    f"📢 Channel: {channel_name}\n"
                    f"🆔 ID: {channel_id}\n"
                    f"🔴 Status: Possibly BANNED or inaccessible\n"
                    f"🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                    f"❌ Failed after 2 attempts\n"
                    f"Error: {str(e2)[:100]}"
                )
                
                # Notify owner
                if bot_data.owner:
                    try:
                        await context.bot.send_message(
                            chat_id=bot_data.owner,
                            text=warning_msg
                        )
                    except Exception as owner_error:
                        logger.error(f"Failed to notify owner: {owner_error}")
                
                # Notify all admins
                for admin_id in bot_data.admins:
                    try:
                        await context.bot.send_message(
                            chat_id=admin_id,
                            text=warning_msg
                        )
                        await asyncio.sleep(0.3)
                    except Exception as admin_error:
                        logger.error(f"Failed to notify admin {admin_id}: {admin_error}")
        
        # Small delay between channels to avoid rate limits
        await asyncio.sleep(1)
    
    logger.info("Channel check completed.")

async def setup_periodic_check(application: Application):
    """Setup periodic channel checking"""
    job_queue = application.job_queue
    job_queue.run_repeating(
        check_channel_status,
        interval=bot_data.check_interval,
        first=10,  # Start 10 seconds after bot starts
        name='channel_check'
    )
    logger.info(f"Periodic check scheduled every {seconds_to_readable(bot_data.check_interval)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Handle errors in the bot"""
    logger.error(f"Exception while handling an update: {context.error}")
    
    # Get error details
    import traceback
    tb_list = traceback.format_exception(None, context.error, context.error.__traceback__)
    tb_string = ''.join(tb_list)
    logger.error(f"Full traceback:\n{tb_string}")

def main():
    """Start the bot"""
    # Get token from environment variable
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return
    
    # Create application
    application = Application.builder().token(token).build()
    
    # Add error handler
    application.add_error_handler(error_handler)
    
    # Add command handlers
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
    
    # Setup periodic check
    application.post_init = setup_periodic_check
    
    # Start the bot
    logger.info("Bot started!")
    logger.info(f"Bot active: {bot_data.bot_active}")
    logger.info(f"Check interval: {seconds_to_readable(bot_data.check_interval)}")
    logger.info(f"Channels loaded: {len(bot_data.channels)}")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
