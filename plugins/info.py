from pyrogram import filters
from pyrogram.types import Message, User, Chat
from pyrogram.enums import ChatType

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


def format_user_info(user: User) -> str:
    """Format user information."""
    info = f"**👤 User Information**\n\n"
    info += f"**Name:** {user.first_name or ''}"
    if user.last_name:
        info += f" {user.last_name}"
    info += "\n"
    info += f"**User ID:** `{user.id}`\n"
    if user.username:
        info += f"**Username:** @{user.username}\n"
    info += f"**Is Bot:** {'Yes' if user.is_bot else 'No'}\n"
    if user.is_verified:
        info += f"**Verified:** ✅\n"
    if user.is_premium:
        info += f"**Premium:** ⭐\n"
    if user.dc_id:
        info += f"**DC ID:** {user.dc_id}\n"
    return info


def format_chat_info(chat: Chat) -> str:
    """Format chat/group information."""
    info = ""

    if chat.type == ChatType.PRIVATE:
        info += f"**💬 Private Chat**\n\n"
    elif chat.type == ChatType.GROUP:
        info += f"**👥 Group Chat**\n\n"
    elif chat.type == ChatType.SUPERGROUP:
        info += f"**🏢 Supergroup**\n\n"
    elif chat.type == ChatType.CHANNEL:
        info += f"**📢 Channel**\n\n"

    info += f"**Title:** {chat.title or 'N/A'}\n"
    info += f"**Chat ID:** `{chat.id}`\n"

    if chat.username:
        info += f"**Username:** @{chat.username}\n"

    if chat.members_count:
        info += f"**Members:** {chat.members_count}\n"

    if chat.description:
        desc = chat.description[:100] + "..." if len(chat.description) > 100 else chat.description
        info += f"**Description:** {desc}\n"

    if chat.dc_id:
        info += f"**DC ID:** {chat.dc_id}\n"

    return info


@main.app.on_message(filters.me & filters.command("info", prefixes=config.CMD_PREFIX))
@continue_propagation
async def info_command(client, message: Message):
    """Get information about user, chat, or replied message.

    Usage:
        .info - Get current chat info
        .info (reply to message) - Get sender's info
    """
    try:
        reply = message.reply_to_message

        if reply:
            # Get info about the user who sent the replied message
            if reply.from_user:
                user = await client.get_users(reply.from_user.id)
                info_text = format_user_info(user)

                # Add message info
                info_text += f"\n**📨 Message Info**\n"
                info_text += f"**Message ID:** `{reply.id}`\n"
                if reply.date:
                    info_text += f"**Date:** {reply.date}\n"
                if reply.edit_date:
                    info_text += f"**Edited:** {reply.edit_date}\n"

                # Add forward from chat info if message is forwarded
                if reply.forward_from_chat:
                    info_text += f"\n**🔄 Forwarded From Chat:**\n"
                    info_text += f"**Chat ID:** `{reply.forward_from_chat.id}`\n"
                    if reply.forward_from_chat.title:
                        info_text += f"**Chat Title:** {reply.forward_from_chat.title}\n"
                    if reply.forward_from_chat.username:
                        info_text += f"**Username:** @{reply.forward_from_chat.username}\n"
                    if reply.forward_from_chat.type:
                        info_text += f"**Type:** {reply.forward_from_chat.type.name}\n"

                await message.edit(info_text)
            else:
                await message.edit("❌ Could not get user information from replied message")
        else:
            # Get current chat info
            chat = await client.get_chat(message.chat.id)
            info_text = format_chat_info(chat)
            await message.edit(info_text)

        logger.info("Info command executed")

    except Exception as e:
        error_msg = f"❌ Error getting info:\n{str(e)}"
        await message.edit(error_msg)
        logger.error(f"Error in info command: {e}")
