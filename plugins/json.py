import json
import os
import tempfile

from pyrogram import Client
from pyrogram.types import Message

import config
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)


class JsonPlugin(Plugin):
    id = "json"
    category = "Utility"
    description = "Export a replied message as a JSON file."

    @h.command(
        "json",
        description=description,
        examples=(Example("{cmd} (reply to a message)"),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def json_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["JsonPlugin"],
        args: ParsedOptions,
    ) -> None:
        """Export replied message as JSON file.

        Usage: .json (reply to a message)
        """
        command_message = message
        temp_file = None
        try:
            reply = command_message.reply_to_message
            if reply is None:
                await ctx.edit(
                    "❌ Reply to a message to export it as JSON",
                    message=command_message,
                )
                return

            message_dict = json.loads(str(reply))
            json_content = json.dumps(
                message_dict, indent=4, ensure_ascii=False, default=str
            )
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as output:
                output.write(json_content)
                temp_file = output.name

            await client.send_document(
                chat_id=command_message.chat.id,
                document=temp_file,
                file_name=f"message_{reply.chat.id}_{reply.id}.json",
                caption=(
                    f"📄 Message JSON Export\n**Chat ID:** `{reply.chat.id}`\n"
                    f"**Message ID:** `{reply.id}`"
                ),
            )
            await command_message.delete()
            logger.info("Exported message %s to JSON", reply.id)
        except Exception as error:
            await ctx.edit(
                f"❌ Error exporting JSON: {error}", message=command_message
            )
            logger.error("Error in json command: %s", error)
        finally:
            if temp_file is not None:
                try:
                    os.remove(temp_file)
                except OSError:
                    logger.warning("Could not remove temporary JSON export %s", temp_file)
