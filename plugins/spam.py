import asyncio
import re

from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

# Global spam control
spam_active = {}  # Dict of chat_id -> task
spam_stats = {}  # Dict of chat_id -> {'current': int, 'total': int}


def parse_delay(delay_str):
    """Parse delay string to seconds.

    Supports:
    - int or string number: seconds (e.g., 10 or "10")
    - "Xs" or "X": X seconds
    - "Xm": X minutes
    - "Xh": X hours

    Returns seconds as float, or None if invalid.
    """
    if isinstance(delay_str, int):
        return float(delay_str)

    if isinstance(delay_str, float):
        return delay_str

    # String parsing
    delay_str = str(delay_str).strip().lower()

    # Match pattern like "10s", "5m", "1h" or just "10"
    match = re.match(r"^(\d+(?:\.\d+)?)(s|m|h)?$", delay_str)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2) or "s"  # Default to seconds

    if unit == "s":
        return value
    elif unit == "m":
        return value * 60
    elif unit == "h":
        return value * 3600

    return None


def parse_spam_args(text):
    """Parse spam command arguments.

    Returns: dict with keys: count, delay, delete_after, reply_mode, message_text
    """
    # Remove command prefix
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None

    args_str = parts[1]

    result = {
        "count": None,
        "delay": config.SPAM_DELAY,
        "delete_after": None,
        "reply_mode": False,
        "ignore_limit": False,
        "message_text": None,
    }

    # Split and parse flags manually
    tokens = args_str.split()
    i = 0
    message_parts = []

    while i < len(tokens):
        token = tokens[i]

        # Check for flags
        if token in ["-c", "-count"]:
            # Next token should be count
            if i + 1 < len(tokens):
                try:
                    result["count"] = int(tokens[i + 1])
                    i += 2
                    continue
                except ValueError:
                    pass
        elif token in ["-d", "-delay"]:
            # Next token should be delay
            if i + 1 < len(tokens):
                delay_val = parse_delay(tokens[i + 1])
                if delay_val is not None:
                    result["delay"] = delay_val
                    i += 2
                    continue
        elif token in ["-del", "-delete"]:
            # No argument, just set flag
            result["delete_after"] = 2.5
            i += 1
            continue
        elif token in ["-r", "-reply"]:
            # No argument, just set flag
            result["reply_mode"] = True
            i += 1
            continue
        elif token in ["-ignore", "-nolimit"]:
            # No argument, just set flag
            result["ignore_limit"] = True
            i += 1
            continue

        # If we get here, it's part of the message or count (if -c not used)
        # Try to parse as count if count is not set yet
        if result["count"] is None:
            try:
                result["count"] = int(token)
                i += 1
                continue
            except ValueError:
                pass

        # Otherwise, it's part of the message
        message_parts.append(token)
        i += 1

    # Join message parts
    if message_parts:
        result["message_text"] = " ".join(message_parts)

    return result


@main.app.on_message(
    filters.me
    & filters.command(
        ["spam", "spam_stop", "spam_help", "spam_stat"],
        prefixes=config.CMD_PREFIX,
    )
)
@continue_propagation
async def spam_command(client, message: Message):
    """Unified spam command with flags.

    Usage:
        .spam [flags] <count> <text>
        .spam [flags] (reply to message)
    Flags:
        -c, -count <num>     : Specify count (optional, can use direct number)
        -d, -delay <time>    : Delay between messages (10s, 5m, 1h)
        -del, -delete        : Auto-delete sent messages after 2.5s
        -r, -reply           : Send as reply to replied message

    Examples:
        .spam 10 Hello World
        .spam -d 5s 10 Hello
        .spam -del -c 10 -d 2s Test
        .spam -r 5 Hi (reply to message)
        .spam_stop
        .spam_help
        .spam_stat
    """
    global spam_active, spam_stats

    try:
        cmd = message.command[0]

        # Handle spam_stat
        if cmd == "spam_stat":
            chat_id = message.chat.id
            if chat_id in spam_stats:
                current = spam_stats[chat_id]["current"]
                total = spam_stats[chat_id]["total"]
                await message.edit(f"📊 Spam Progress: {current}/{total}")
            else:
                await message.edit("ℹ️ No active spam in this chat")

            await asyncio.sleep(3)
            await message.delete()
            return

        # Handle spam_stop
        if cmd == "spam_stop":
            chat_id = message.chat.id
            if chat_id in spam_active:
                spam_active[chat_id].cancel()
                del spam_active[chat_id]
                if chat_id in spam_stats:
                    del spam_stats[chat_id]
                await message.edit("✅ Spam stopped")
                logger.info("Spam stopped by user")
            else:
                await message.edit("ℹ️ No active spam to stop")

            await asyncio.sleep(2)
            await message.delete()
            return

        # Handle spam_help
        if cmd == "spam_help":
            help_text = f"""**📨 Spam Command Help**

**Usage:**
`.spam [flags] <count> <text>`
`.spam [flags]` (reply to message)

**Flags:**
`-c, -count <num>` - Specify count
`-d, -delay <time>` - Delay (10s, 5m, 1h)
`-del, -delete` - Auto-delete after 2.5s
`-r, -reply` - Reply to replied message
`-ignore, -nolimit` - Bypass max count limit

**Examples:**
• `.spam 10 Hello World`
• `.spam -d 5s 10 Test`
• `.spam -del -c 10 -d 2s Message`
• `.spam -r 5 Hi` (reply mode)
• `.spam -c 5` (copy replied message)
• `.spam -ignore 1000 Spam` (bypass limit)

**Other Commands:**
• `.spam_stop` - Stop active spam
• `.spam_stat` - Show spam progress
• `.spam_help` - Show this help

**Note:** Max count is **{config.MAX_SPAM_COUNT}** (use `-ignore` to bypass)"""

            await message.edit(help_text)
            await asyncio.sleep(30)
            await message.delete()
            return

        # Handle spam command
        args = parse_spam_args(message.text)

        if args is None:
            await message.edit(
                "Usage: `.spam [flags] <count> <text>` or use `.spam_help`"
            )
            return

        # Validate count
        if args["count"] is None:
            await message.edit("Error: No count provided")
            return

        # Check limit unless -ignore flag is set
        if not args["ignore_limit"] and args["count"] > config.MAX_SPAM_COUNT:
            await message.edit(
                f"Error: Maximum spam count is {config.MAX_SPAM_COUNT}\nUse `-ignore` flag to bypass"
            )
            return

        if args["count"] < 1:
            await message.edit("Error: Count must be at least 1")
            return

        # Get message source
        reply = message.reply_to_message
        copy_mode = False

        if args["message_text"] is None:
            if not reply:
                await message.edit(
                    "Error: No text provided and no message to reply to"
                )
                return
            copy_mode = True

        # Delete command message
        await message.delete()

        # Cancel previous spam in this chat if active
        chat_id = message.chat.id
        if chat_id in spam_active:
            spam_active[chat_id].cancel()
            logger.info("Cancelled previous spam task")

        # Start spamming
        async def spam_loop():
            try:
                count = args["count"]
                delay = args["delay"]
                delete_after = args["delete_after"]
                reply_mode = args["reply_mode"]

                # Initialize stats
                spam_stats[chat_id] = {"current": 0, "total": count}

                # Helper to delete a message after delay
                async def delete_msg_later(msg, delay_sec):
                    await asyncio.sleep(delay_sec)
                    try:
                        await msg.delete()
                    except Exception as e:
                        logger.debug(f"Failed to delete message: {e}")

                if copy_mode:
                    logger.info(
                        f"Spamming {count} messages (copy mode) with {delay}s delay"
                    )
                    for i in range(count):
                        if chat_id not in spam_active:
                            logger.info(f"Spam stopped at {i + 1}/{count}")
                            break
                        try:
                            sent = await reply.copy(message.chat.id)
                            spam_stats[chat_id]["current"] = i + 1
                            if delete_after:
                                asyncio.create_task(
                                    delete_msg_later(sent, delete_after)
                                )
                            if i < count - 1:
                                await asyncio.sleep(delay)
                        except Exception as e:
                            logger.error(
                                f"Error copying spam message {i + 1}/{count}: {e}"
                            )
                            break
                else:
                    logger.info(
                        f"Spamming {count} messages with {delay}s delay: {args['message_text'][:50]}..."
                    )
                    for i in range(count):
                        if chat_id not in spam_active:
                            logger.info(f"Spam stopped at {i + 1}/{count}")
                            break
                        try:
                            # Send message
                            if reply_mode and reply:
                                sent = await reply.reply(args["message_text"])
                            else:
                                sent = await client.send_message(
                                    message.chat.id, args["message_text"]
                                )

                            spam_stats[chat_id]["current"] = i + 1

                            if delete_after:
                                asyncio.create_task(
                                    delete_msg_later(sent, delete_after)
                                )

                            if i < count - 1:
                                await asyncio.sleep(delay)
                        except Exception as e:
                            logger.error(
                                f"Error sending spam message {i + 1}/{count}: {e}"
                            )
                            break

                logger.info(f"Spam completed: {count} messages sent")

            finally:
                if chat_id in spam_active:
                    del spam_active[chat_id]
                if chat_id in spam_stats:
                    del spam_stats[chat_id]

        task = asyncio.create_task(spam_loop())
        spam_active[chat_id] = task

    except Exception as e:
        error_msg = f"Error in spam command: {str(e)}"
        await message.edit(error_msg)
        logger.error(error_msg)
