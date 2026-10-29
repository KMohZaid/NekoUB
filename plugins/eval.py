import sys
import io
import asyncio
import traceback
from pyrogram import Client
from pyrogram.types import Message

import config
import main
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger, format_output, code_format

logger = get_logger(__name__)


async def run_eval_task(code, client, message, ctx=None):
    """Run the actual eval code (can be cancelled)."""
    try:
        # Capture stdout
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected_output = io.StringIO()
        redirected_error = io.StringIO()
        sys.stdout = redirected_output
        sys.stderr = redirected_error

        try:
            # Create execution environment
            exec_globals = {
                "client": client,
                "message": message,
                "app": main.app,
                "__name__": "__main__",
            }

            # Check if code contains await - if so, wrap in async function
            if "await" in code:
                # Wrap in async function - properly indent all lines
                indented_code = "\n".join(
                    "    " + line if line.strip() else ""
                    for line in code.splitlines()
                )
                async_code = f"async def __eval_async():\n{indented_code}"
                exec(async_code, exec_globals)
                # Call and await the function in the actual async context
                result = await exec_globals["__eval_async"]()
                if result is not None:
                    print(result)
            else:
                # Try to evaluate as expression first, then exec if that fails
                try:
                    result = eval(code, exec_globals)
                    if result is not None:
                        print(result)
                except SyntaxError:
                    exec(code, exec_globals)

        except Exception as e:
            # Capture exception
            error = traceback.format_exc()
            print(error, file=sys.stderr)

        finally:
            # Restore stdout/stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Get output
        stdout = redirected_output.getvalue()
        stderr = redirected_error.getvalue()

        # Prepare output with code shown
        output = f"**Code:**\n```py\n{code}\n```\n\n"

        if stdout:
            output += f"**OUTPUT:**\n```\n{stdout.strip()}\n```\n\n"
        if stderr:
            output += f"**ERROR:**\n```\n{stderr.strip()}\n```\n\n"

        if not stdout and not stderr:
            output += "Code executed successfully (no output)"

        # Format and send
        if ctx is not None and not ctx.actor_is_owner:
            await ctx.send(format_output(output, max_length=4000), reply_to=message)
        else:
            await message.edit(format_output(output, max_length=4000))
        logger.info(f"Evaluated code: {code[:50]}...")

    except asyncio.CancelledError:
        text = f"❌ **EVAL killed by user**\n\n**Code:**\n```py\n{code[:500]}\n```"
        if ctx is not None and not ctx.actor_is_owner:
            await ctx.send(text, reply_to=message)
        else:
            await message.edit(text)
        logger.info("Eval command was cancelled")
        raise
    except Exception as e:
        error_msg = f"Error evaluating code:\n{str(e)}"
        if ctx is not None and not ctx.actor_is_owner:
            await ctx.send(code_format(error_msg), reply_to=message)
        else:
            await message.edit(code_format(error_msg))
        logger.error(f"Error in eval command: {e}")


class EvalPlugin(Plugin):
    id = "eval"
    category = "Utility"
    description = "Evaluate Python code, including asynchronous code."

    @h.command(
        "eval",
        sudo=True,
        grantable=False,
        signature="<python code>",
        short_description="Evaluate Python code and show the result.",
        description=description,
        examples=(Example('{cmd} print("hello")', "Run a short Python statement."),),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def eval_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["EvalPlugin"],
        args: ParsedOptions,
    ) -> None:
        """Evaluate Python code.

        Usage: .eval <python code>
        Example: .eval print("Hello World")
        """
        try:
            # Get the code from the message
            cmd = message.text.split(maxsplit=1)

            if len(cmd) < 2:
                if ctx.actor_is_owner:
                    await message.edit("Usage: `.eval <python code>`")
                else:
                    await ctx.send("Usage: `#eval <python code>`", reply_to=message)
                return

            code = cmd[1]

            # Update message to show it's running
            if ctx.actor_is_owner:
                await message.edit(f"Evaluating:\n{code_format(code, 'python')}")
            else:
                await ctx.send(f"Evaluating:\n{code_format(code, 'python')}", reply_to=message)

            # Create task and track it
            task_id = f"{message.chat.id}:{message.id}"
            task = asyncio.create_task(run_eval_task(code, client, message, ctx))

            main.running_tasks[task_id] = {
                "task": task,
                "type": "eval",
                "message": message,
            }

            try:
                await task
            finally:
                # Remove from tracking when done
                if task_id in main.running_tasks:
                    del main.running_tasks[task_id]

        except asyncio.CancelledError:
            # Task was killed, already handled in run_eval_task
            pass
        except Exception as e:
            error_msg = f"Error evaluating code:\n{str(e)}"
            await message.edit(code_format(error_msg))
            logger.error(f"Error in eval command: {e}")
