import asyncio
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(filters.me & filters.command("reload", prefixes=config.CMD_PREFIX))
@continue_propagation
async def reload_command(client, message: Message):
    """Reload all plugins without restarting the bot.

    Usage: .reload
    """
    try:
        await message.edit("🔄 Reloading plugins...")

        # Get plugin count before reload
        old_count = len(main.loaded_plugins)

        try:
            # Reload all plugins
            main.reload_plugins()

            # Get new count
            new_count = len(main.loaded_plugins)

            await message.edit(
                f"✅ Plugins reloaded!\n"
                f"Previous: {old_count} plugins\n"
                f"Current: {new_count} plugins"
            )

            logger.info("Plugins reloaded successfully")

            # Auto-delete after 10 seconds
            await asyncio.sleep(10)
            await message.delete()

        except Exception as reload_error:
            error_msg = f"❌ Error during reload:\n```\n{str(reload_error)}\n```"
            await message.edit(error_msg)
            logger.error(f"Error in reload_plugins: {reload_error}", exc_info=True)
            # Don't auto-delete on error so user can see it

    except Exception as e:
        error_msg = f"❌ Error in reload command:\n```\n{str(e)}\n```"
        await message.edit(error_msg)
        logger.error(f"Error in reload command: {e}", exc_info=True)
