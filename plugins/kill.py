from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import get_logger, continue_propagation

logger = get_logger(__name__)


@main.app.on_message(
    filters.me & filters.command("kill", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def kill_command(client, message: Message):
    """Kill running exec/eval commands.

    Usage: .kill
    """
    try:
        if not main.running_tasks:
            await message.edit("❌ No running tasks to kill")
            return

        killed_tasks = []

        # Cancel all running tasks
        for task_id, task_info in list(main.running_tasks.items()):
            task = task_info["task"]
            task_type = task_info["type"]
            task_msg = task_info["message"]

            if not task.done():
                task.cancel()
                killed_tasks.append(
                    f"`{task_type}` in chat `{task_msg.chat.id}`"
                )
                logger.info(
                    f"Killed {task_type} task in chat {task_msg.chat.id}"
                )

                # Update the original message
                try:
                    await task_msg.edit(
                        f"❌ **{task_type.upper()} killed by user**"
                    )
                except Exception as e:
                    logger.warning(f"Could not update killed task message: {e}")

            # Remove from tracking
            del main.running_tasks[task_id]

        if killed_tasks:
            output = f"🔪 **Killed {len(killed_tasks)} task(s):**\n\n"
            output += "\n".join(f"• {task}" for task in killed_tasks)
            await message.edit(output)
        else:
            await message.edit("ℹ️ All tasks were already completed")

        logger.info(f"Killed {len(killed_tasks)} tasks")

    except Exception as e:
        error_msg = f"❌ Error killing tasks:\n{str(e)}"
        await message.edit(error_msg)
        logger.error(f"Error in kill command: {e}")
