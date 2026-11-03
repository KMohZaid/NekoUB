import asyncio

from pyrogram import Client
from pyrogram.types import Message

import config
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)


class SavePlugin(Plugin):
    id = "save"
    category = "Utility"
    description = "Save a replied message to Saved Messages."

    @h.command(
        "save",
        description=description,
        examples=(Example("{cmd} (reply to a message)"),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def save_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["SavePlugin"],
        args: ParsedOptions,
    ) -> None:
        """Save replied message to Saved Messages (as forward).

        Usage: Reply to a message and use .save

        Example:
            (reply to a message) .save
        """
        try:
            reply = message.reply_to_message

            if not reply:
                await message.edit("❌ Please reply to a message to save it!")
                return

            saved = False
            method_used = ""

            # Try to forward first (preserves original sender info)
            try:
                await reply.forward("me")
                saved = True
                method_used = "forwarded"
                logger.info("Message forwarded to Saved Messages")
            except Exception as forward_error:
                logger.warning(f"Forward failed: {forward_error}, trying copy...")

                # If forward fails (e.g., restricted channel), try copying
                try:
                    await reply.copy("me")
                    saved = True
                    method_used = "copied"
                    logger.info("Message copied to Saved Messages")
                except Exception as copy_error:
                    logger.error(f"Copy also failed: {copy_error}")

            # Show result
            if saved:
                await message.edit(f"✅ Saved to Saved Messages ({method_used})!")

                # Auto-delete confirmation after 2 seconds
                await asyncio.sleep(2)
                await message.delete()
            else:
                await message.edit(
                    "❌ Something went wrong and save failed!\nTry manually copying the message."
                )
                logger.error("Both forward and copy failed for save command")

        except Exception as e:
            error_msg = f"❌ Error saving message: {str(e)}"
            await message.edit(error_msg)
            logger.error(f"Error in save command: {e}")
