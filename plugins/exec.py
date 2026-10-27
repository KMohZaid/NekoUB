import asyncio
from pyrogram import Client
from pyrogram.types import Message

import config
import main
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger, format_output, code_format

logger = get_logger(__name__)


async def run_exec_task(command, message):
    """Run the actual exec command (can be cancelled)."""
    try:
        # Execute the command using asyncio
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        # Decode output
        stdout = stdout.decode() if stdout else ""
        stderr = stderr.decode() if stderr else ""

        # Prepare output with command shown
        output = f"**Command:**\n```bash\n{command}\n```\n\n"

        if stdout:
            output += f"**STDOUT:**\n```\n{stdout.strip()}\n```\n\n"
        if stderr:
            output += f"**STDERR:**\n```\n{stderr.strip()}\n```\n\n"

        if not stdout and not stderr:
            output += "Command executed successfully (no output)\n\n"

        output += f"**Return Code:** `{process.returncode}`"

        # Format and send
        await message.edit(format_output(output, max_length=4000))
        logger.info(f"Executed command: {command}")

    except asyncio.CancelledError:
        await message.edit(
            f"❌ **EXEC killed by user**\n\n**Command:**\n```bash\n{command}\n```"
        )
        logger.info("Exec command was cancelled")
        raise
    except Exception as e:
        error_msg = f"Error executing command:\n{str(e)}"
        await message.edit(code_format(error_msg))
        logger.error(f"Error in exec command: {e}")


class ExecPlugin(Plugin):
    id = "exec"
    category = "Utility"
    description = "Run a shell command and show stdout, stderr, and return code."

    @h.command(
        "exec",
        signature="<shell command>",
        short_description="Run a shell command and return its output.",
        description=description,
        examples=(Example("{cmd} ls -la", "List files in the current working directory."),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def exec_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["ExecPlugin"],
        args: ParsedOptions,
    ) -> None:
        """Execute shell commands.

        Usage: .exec <command>
        Example: .exec ls -la
        """
        try:
            # Get the command from the message
            cmd = message.text.split(maxsplit=1)

            if len(cmd) < 2:
                await message.edit("Usage: `.exec <command>`")
                return

            command = cmd[1]

            # Update message to show it's running
            await message.edit(f"Executing: `{command}`")

            # Create task and track it
            task_id = f"{message.chat.id}:{message.id}"
            task = asyncio.create_task(run_exec_task(command, message))

            main.running_tasks[task_id] = {
                "task": task,
                "type": "exec",
                "message": message,
            }

            try:
                await task
            finally:
                # Remove from tracking when done
                if task_id in main.running_tasks:
                    del main.running_tasks[task_id]

        except asyncio.CancelledError:
            # Task was killed, already handled in run_exec_task
            pass
        except Exception as e:
            error_msg = f"Error executing command:\n{str(e)}"
            await message.edit(code_format(error_msg))
            logger.error(f"Error in exec command: {e}")
