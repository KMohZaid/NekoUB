import asyncio

from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

POLL_INTERVAL = 2
RESPONSE_TIMEOUT = 30
DONE_MARKERS = ("no records", "name history", "username history")


def reply_user_id(message: Message) -> int | None:
    """User id of the replied message's sender, if it is a real user."""
    reply = message.reply_to_message
    if not reply:
        return None
    if reply.from_user and not reply.from_user.is_bot:
        return reply.from_user.id
    return None


async def resolve_target(client, message: Message) -> int | None:
    user_id = reply_user_id(message)
    if user_id is not None:
        return user_id

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return None
    ref = parts[1].strip()
    if ref.lstrip("-").isdigit():
        return int(ref)
    try:
        user = await client.get_users(ref)
    except Exception:
        return None
    if user is None or user.is_bot:
        return None
    return user.id


def _is_final(text: str) -> bool:
    low = text.lower()
    return any(marker in low for marker in DONE_MARKERS)


async def query_sangmata(client, user_id: int) -> str:
    """Ask SangMataInfo for a user's history and return its reply text."""
    bot = config.SANGMATA_BOT
    sent = await client.send_message(bot, f"/search_id {user_id}")
    text = ""
    seen: list[int] = []

    try:
        deadline = asyncio.get_event_loop().time() + RESPONSE_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            history = [
                m async for m in client.get_chat_history(bot, limit=8)
            ]
            replies = [m for m in history if m.id > sent.id and m.text]
            if not replies:
                continue
            seen = [m.id for m in replies]
            text = "\n".join(m.text for m in replies)
            if _is_final(text):
                break
    finally:
        try:
            await client.delete_messages(bot, [sent.id, *seen])
        except Exception as exc:
            logger.debug(f"[SANGMATA] Cleanup failed: {exc}")

    return text


@main.app.on_message(
    filters.me & filters.command(["sg", "sangmata"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def sangmata_command(client, message: Message):
    await message.edit("🔎 `Searching name history...`")

    user_id = await resolve_target(client, message)
    if user_id is None:
        await message.edit(
            "Reply to a user's message, or use "
            f"`{config.CMD_PREFIX}sg <user_id|@username>`."
        )
        return

    try:
        text = await query_sangmata(client, user_id)
    except Exception as exc:
        logger.error(f"[SANGMATA] Lookup failed: {exc}")
        await message.edit(f"SangMata lookup failed: {type(exc).__name__}")
        return

    if not text.strip():
        await message.edit("SangMata did not respond. Try again later.")
        return
    if "no records" in text.lower():
        await message.edit(f"No name history found for `{user_id}`.")
        return

    await message.edit(f"**SangMata — history for `{user_id}`**\n\n{text}")
