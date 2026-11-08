import asyncio

from pyrogram import Client
from pyrogram.types import Message

import config
import main
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)


class ReloadPlugin(Plugin):
    id = "reload"
    category = "Core"
    description = "Hot-reload public plugins without restarting."

    @h.command(
        "reload",
        description=description,
        examples=(Example("{cmd}"),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def reload_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["ReloadPlugin"],
        args: ParsedOptions,
    ) -> None:
        """Reload all plugins without restarting the bot.

        Usage: .reload
        """
        try:
            await ctx.edit("🔄 Reloading plugins...", message=message)

            # Get plugin count before reload
            old_count = len(main.loaded_plugins)

            try:
                # Reload all plugins
                main.reload_plugins()

                # Get new count
                new_count = len(main.loaded_plugins)

                await ctx.edit(
                    f"✅ Plugins reloaded!\n"
                    f"Previous: {old_count} plugins\n"
                    f"Current: {new_count} plugins",
                    message=message,
                )

                logger.info("Plugins reloaded successfully")

                # Auto-delete after 10 seconds
                await asyncio.sleep(10)
                await message.delete()

            except Exception as reload_error:
                error_msg = (
                    f"❌ Error during reload:\n```\n{str(reload_error)}\n```"
                )
                await ctx.edit(error_msg, message=message)
                logger.error(
                    f"Error in reload_plugins: {reload_error}", exc_info=True
                )
                # Don't auto-delete on error so user can see it

        except Exception as e:
            error_msg = f"❌ Error in reload command:\n```\n{str(e)}\n```"
            await ctx.edit(error_msg, message=message)
            logger.error(f"Error in reload command: {e}", exc_info=True)
