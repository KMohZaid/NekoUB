import html
import platform
import time
from pyrogram import Client, __version__ as pyrogram_version
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
import main
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)

# Bot start time
start_time = time.time()


def get_uptime() -> str:
    """Get bot uptime."""
    uptime_seconds = int(time.time() - start_time)
    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    uptime_str = ""
    if days > 0:
        uptime_str += f"{days}d "
    if hours > 0:
        uptime_str += f"{hours}h "
    if minutes > 0:
        uptime_str += f"{minutes}m "
    uptime_str += f"{seconds}s"

    return uptime_str.strip()


class AlivePlugin(Plugin):
    id = "alive"
    category = "Core"
    description = "Show userbot status, uptime, plugins and system info."

    @h.command(
        "alive",
        description=description,
        examples=(Example("{cmd}"),),
        prefixes=config.CMD_PREFIX,
    )
    async def alive_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["AlivePlugin"],
        args: ParsedOptions,
    ) -> None:
        """Show userbot status with cute neko theme.

        Usage: .alive
        """
        try:
            # Get user info
            me = await client.get_me()
            username = f"@{me.username}" if me.username else "No username"

            # Build an HTML status message with a monospaced banner.
            banner = (
                "╔═══════════════════╗\n"
                "║   NekoUB Userbot   ║\n"
                "╚═══════════════════╝"
            )
            alive_msg = f"<pre>{banner}</pre>\n\n"
            alive_msg += "<b>🐱 Nya~ I'm alive and ready!</b>\n\n"

            alive_msg += "<b>┌─ Owner Info ─┐</b>\n"
            alive_msg += f"│ 👤 <b>Name:</b> {html.escape(me.first_name)}\n"
            alive_msg += f"│ 🆔 <b>ID:</b> <code>{me.id}</code>\n"
            alive_msg += f"│ 📝 <b>Username:</b> {html.escape(username)}\n"
            alive_msg += "└───────────────┘\n\n"

            alive_msg += "<b>┌─ System Info ─┐</b>\n"
            alive_msg += f"│ ⏰ <b>Uptime:</b> {get_uptime()}\n"
            alive_msg += f"│ 🔌 <b>Plugins:</b> {len(main.loaded_plugins)}\n"
            alive_msg += f"│ 📚 <b>Pyrogram:</b> {pyrogram_version}\n"
            alive_msg += f"│ 🐍 <b>Python:</b> {platform.python_version()}\n"
            alive_msg += f"│ 💻 <b>Platform:</b> {html.escape(platform.system())}\n"
            alive_msg += "└───────────────┘\n\n"

            alive_msg += f"<b>✨ Prefix:</b> <code>{html.escape(config.CMD_PREFIX)}</code>\n"
            alive_msg += "<b>💝 Made with love by NekoUB</b>"

            await message.edit(alive_msg, parse_mode=ParseMode.HTML)

            logger.info("Alive command executed")

        except Exception as e:
            error_msg = f"❌ Error in alive command:\n{str(e)}"
            await message.edit(error_msg)
            logger.error(f"Error in alive command: {e}")
