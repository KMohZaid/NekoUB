import time
from pyrogram import Client
from pyrogram.types import Message

import config
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)


class PingPlugin(Plugin):
    id = "ping"
    category = "Core"
    description = "Check userbot latency."

    @h.command(
        "ping",
        description="Check userbot latency.",
        examples=(Example("{cmd}"),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def ping_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["PingPlugin"],
        args: ParsedOptions,
    ) -> None:
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
