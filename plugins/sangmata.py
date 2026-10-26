import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import Message

import config
from core.plugins import Example, ParsedOptions, Plugin, PluginContext, handlers as h
from utils import get_logger

logger = get_logger(__name__)

POLL_INTERVAL = 2
RESPONSE_TIMEOUT = 30
DONE_MARKERS = (
    "no records",
    "no data available",
    "used up your quota for today",
    "name history",
    "username history",
)


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

    if message.reply_to_message:
        return None

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


def _next_quota_reset_markup() -> str:
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    reset_at = datetime.combine(tomorrow, datetime.min.time(), tzinfo=timezone.utc)
    reset_unix = int(reset_at.timestamp())
    return f'<tg-time unix="{reset_unix}" format="dt">00:00 UTC</tg-time>'


def _quota_reset_message(text: str) -> str | None:
    if "used up your quota for today" not in text.lower():
        return None

    return (
        "SangMata's daily lookup quota is used up. It resets at "
        f"{_next_quota_reset_markup()}."
    )


async def query_sangmata(client, user_id: int) -> str:
    """Ask SangMata for a user's history and return its reply text.

    SangMata expects just the numeric id (no /search_id prefix).
    """
    bot = config.SANGMATA_BOT
    logger.info(f"[SANGMATA] Sending lookup for user_id={user_id} to @{bot}")
    sent = await client.send_message(bot, str(user_id))
    logger.info(f"[SANGMATA] Lookup message sent: message_id={sent.id}")
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


class SangmataPlugin(Plugin):
    id = "sangmata"
    category = "Utility"
    description = "Look up a user's past names and usernames."

    @h.command(
        "sg",
        aliases=("sangmata",),
        description=description,
        examples=(
            Example("{cmd} (reply to a user)"),
            Example("{cmd} @username"),
            Example("{cmd} 12345678"),
        ),
        prefixes=config.CMD_PREFIX,
        parse_mode="markdown",
    )
    async def sangmata_command(
        self,
        client: Client,
        message: Message,
        ctx: PluginContext["SangmataPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        logger.info(f"[SANGMATA] Command received in chat_id={message.chat.id}")
        try:
            await message.edit(
                f"🔎 Searching name history...\n"
                f"Daily quota resets at {_next_quota_reset_markup()}.",
                parse_mode=ParseMode.HTML,
            )
        except Exception as exc:
            logger.warning(f"[SANGMATA] Could not show search status: {exc}")
            await message.edit("🔎 Searching name history...")

        if message.reply_to_message and reply_user_id(message) is None:
            await message.edit("SangMata lookup only works for users, not bots or chats.")
            return

        user_id = await resolve_target(client, message)
        target_user = None
        if user_id is not None:
            try:
                target_user = await client.get_users(user_id)
            except Exception:
                target_user = None
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

        quota_message = _quota_reset_message(text)
        if quota_message:
            await message.edit(quota_message, parse_mode=ParseMode.HTML)
            return

        if any(
            marker in text.lower()
            for marker in ("no records", "no data available")
        ):
            user_name = " ".join(
                part
                for part in (
                    getattr(target_user, "first_name", None),
                    getattr(target_user, "last_name", None),
                )
                if part
            )
            if user_name:
                await message.edit(
                    f'No name history found for <a href="tg://user?id={user_id}">'
                    f"{user_name}</a>.",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await message.edit(f"No name history found for `{user_id}`.")
            return

        await message.edit(f"**SangMata — history for `{user_id}`**\n\n{text}")
