import time
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(filters.me & filters.command("ping", prefixes=config.CMD_PREFIX))
@continue_propagation
async def ping_command(client, message: Message):
    """Check bot latency.

    Usage: .ping
    """
    try:
        # Record start time
        start_time = time.time()

        # Edit message (this creates round-trip to Telegram)
        await message.edit("🏓 Pong!")

        # Calculate latency
        end_time = time.time()
        latency_ms = round((end_time - start_time) * 1000, 2)

        # Update with latency info
        await message.edit(f"🏓 **Pong!**\n⚡ **Latency:** `{latency_ms}ms`")

        logger.info(f"Ping command executed - Latency: {latency_ms}ms")

    except Exception as e:
        error_msg = f"❌ Error in ping command: {str(e)}"
        await message.edit(error_msg)
        logger.error(f"Error in ping command: {e}")
