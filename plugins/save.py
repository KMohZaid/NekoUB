from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(
    filters.me & filters.command("save", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def save_command(client, message: Message):
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
            import asyncio

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
