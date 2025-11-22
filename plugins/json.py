import json
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(filters.me & filters.command("json", prefixes=config.CMD_PREFIX))
@continue_propagation
async def json_command(client, message: Message):
    """Export replied message as JSON file.

    Usage: .json (reply to a message)
    """
    try:
        # Check if replying to a message
        if not message.reply_to_message:
            await message.edit("❌ Reply to a message to export it as JSON")
            return

        reply = message.reply_to_message

        # Create filename: message_chat_id_message_id.json
        filename = f"message_{reply.chat.id}_{reply.id}.json"

        await message.edit("📄 Generating JSON file...")

        # Convert message to dict and then to JSON
        try:
            message_dict = json.loads(str(reply))
        except:
            # Fallback: use vars() to get all attributes
            message_dict = {
                key: str(value) if not isinstance(value, (dict, list, str, int, float, bool, type(None))) else value
                for key, value in vars(reply).items()
                if not key.startswith('_')
            }

        # Write to JSON file
        json_content = json.dumps(message_dict, indent=4, ensure_ascii=False, default=str)

        # Save to temporary file
        temp_file = f"/tmp/{filename}"
        with open(temp_file, 'w', encoding='utf-8') as f:
            f.write(json_content)

        # Send as document
        await client.send_document(
            chat_id=message.chat.id,
            document=temp_file,
            caption=f"📄 Message JSON Export\n**Chat ID:** `{reply.chat.id}`\n**Message ID:** `{reply.id}`"
        )

        # Delete command message
        await message.delete()

        # Clean up temp file
        import os
        try:
            os.remove(temp_file)
        except:
            pass

        logger.info(f"Exported message {reply.id} to JSON")

    except Exception as e:
        error_msg = f"❌ Error exporting JSON: {str(e)}"
        await message.edit(error_msg)
        logger.error(f"Error in json command: {e}")
