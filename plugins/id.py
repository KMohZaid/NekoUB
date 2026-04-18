from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(
    filters.me & filters.command("id", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def id_command(client, message: Message):
    """Get IDs of chat, user, or media.

    Usage:
        .id - Show current chat ID
        .id (reply to user) - Show user and chat IDs
        .id (reply to media) - Show media file IDs
    """
    try:
        output = []

        # Always show chat ID
        output.append(f"**Chat ID:** `{message.chat.id}`")

        # If replying to a message
        if message.reply_to_message:
            reply = message.reply_to_message

            # Show user ID if message is from a user
            if reply.from_user:
                output.append(f"**User ID:** `{reply.from_user.id}`")
                if reply.from_user.username:
                    output.append(f"**Username:** @{reply.from_user.username}")
                output.append(f"**First Name:** {reply.from_user.first_name}")
                if reply.from_user.last_name:
                    output.append(f"**Last Name:** {reply.from_user.last_name}")

            # Show forward from chat if message is forwarded
            if reply.forward_from_chat:
                output.append(f"\n**Forwarded From Chat:**")
                output.append(f"**Chat ID:** `{reply.forward_from_chat.id}`")
                if reply.forward_from_chat.title:
                    output.append(
                        f"**Chat Title:** {reply.forward_from_chat.title}"
                    )
                if reply.forward_from_chat.username:
                    output.append(
                        f"**Username:** @{reply.forward_from_chat.username}"
                    )

            # Check for media/document
            media_info = None

            if reply.photo:
                media_info = (
                    "Photo",
                    reply.photo.file_id,
                    reply.photo.file_unique_id,
                )
            elif reply.video:
                media_info = (
                    "Video",
                    reply.video.file_id,
                    reply.video.file_unique_id,
                )
            elif reply.document:
                media_info = (
                    "Document",
                    reply.document.file_id,
                    reply.document.file_unique_id,
                )
            elif reply.audio:
                media_info = (
                    "Audio",
                    reply.audio.file_id,
                    reply.audio.file_unique_id,
                )
            elif reply.voice:
                media_info = (
                    "Voice",
                    reply.voice.file_id,
                    reply.voice.file_unique_id,
                )
            elif reply.video_note:
                media_info = (
                    "Video Note",
                    reply.video_note.file_id,
                    reply.video_note.file_unique_id,
                )
            elif reply.sticker:
                media_info = (
                    "Sticker",
                    reply.sticker.file_id,
                    reply.sticker.file_unique_id,
                )
            elif reply.animation:
                media_info = (
                    "Animation",
                    reply.animation.file_id,
                    reply.animation.file_unique_id,
                )

            # Add media info if found
            if media_info:
                media_type, file_id, file_unique_id = media_info
                output.append(f"\n**{media_type}:**")
                output.append(f"**File ID:** `{file_id}`")
                output.append(f"**Unique ID:** `{file_unique_id}`")

            # Show message ID
            output.append(f"\n**Message ID:** `{reply.id}`")

        # Format and send
        await message.edit("\n".join(output))

        logger.info(f"Showed ID info for chat {message.chat.id}")

    except Exception as e:
        error_msg = f"❌ Error getting IDs: {str(e)}"
        await message.edit(error_msg)
        logger.error(f"Error in id command: {e}")
