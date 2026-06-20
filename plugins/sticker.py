import asyncio
import json
from pathlib import Path

from pyrogram import filters
from pyrogram.errors import (
    StickersetInvalid,
    StickersEmpty,
    StickerpackStickersTooMuch,
    StickersTooMuch,
)
from pyrogram.file_id import FileId
from pyrogram.raw import functions, types
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / ".sticker_cache.json"
MAX_STICKERS_PER_PACK = 120
ADD_COOLDOWN = 1  # seconds between sticker adds

# --- Runtime state ---
monitoring_chats: set[int] = set()
me_user = None
_sticker_queue: asyncio.Queue | None = None
_worker_running = False


# ============================================================
# Cache helpers
# ============================================================


def load_cache() -> dict:
    # TODO: migrate to a proper DB (sqlite/tinydb) when adding multi-pack
    #       tracking, per-chat settings, or sticker metadata.
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(data: dict) -> None:
    try:
        CACHE_PATH.write_text(json.dumps(data, indent=2))
    except OSError as exc:
        logger.error(f"[STICKER] Cache write failed: {exc}")


# ============================================================
# User / pack name helpers
# ============================================================


async def ensure_me(client) -> None:
    global me_user
    if me_user is None:
        me_user = await client.get_me()


def build_pack_short_name(user_id: int, pack_number: int) -> str:
    fmt = config.STICKERSET_NAME_FORMAT.strip().strip('"').strip("'")
    if fmt:
        return fmt.format(user_id=user_id, index=pack_number)
    return f"a{user_id}_vol{pack_number}"


def build_pack_title(first_name: str, pack_number: int) -> str:
    fmt = config.STICKERSET_TITLE_FORMAT.strip().strip('"').strip("'")
    if fmt:
        return fmt.format(first_name=first_name, index=pack_number)
    return f"{first_name}'s Pack Vol{pack_number}"


# ============================================================
# Sticker API helpers
# ============================================================


def decode_sticker_file_id(sticker) -> types.InputDocument:
    d = FileId.decode(sticker.file_id)
    return types.InputDocument(
        id=d.media_id,
        access_hash=d.access_hash,
        file_reference=d.file_reference,
    )


async def add_sticker_to_pack(
    client, pack_short_name: str, sticker, emoji: str | None = None
) -> None:
    input_doc = decode_sticker_file_id(sticker)
    await client.invoke(
        functions.stickers.AddStickerToSet(
            stickerset=types.InputStickerSetShortName(short_name=pack_short_name),
            sticker=types.InputStickerSetItem(
                document=input_doc,
                emoji=emoji or sticker.emoji or "🌟",
                keywords="",
            ),
        )
    )


async def create_pack_with_sticker(
    client, title: str, short_name: str, sticker, emoji: str | None = None
) -> None:
    input_doc = decode_sticker_file_id(sticker)
    await client.invoke(
        functions.stickers.CreateStickerSet(
            user_id=await client.resolve_peer(me_user.id),
            title=title,
            short_name=short_name,
            stickers=[
                types.InputStickerSetItem(
                    document=input_doc,
                    emoji=emoji or sticker.emoji or "🌟",
                    keywords="",
                )
            ],
        )
    )


async def get_pack_sticker_count(client, pack_short_name: str) -> int | None:
    try:
        stickers = await client.get_stickers(pack_short_name)
        return len(stickers)
    except (StickersetInvalid, StickersEmpty):
        return None


async def send_pack_notification(client, title: str, short_name: str) -> None:
    try:
        await client.send_message(
            "me",
            f"#NEW_PACK\n"
            f"Created new Pack\n\n"
            f"Title : {title}\n"
            f"Shortname : {short_name}\n\n"
            f"Url :\n"
            f"https://t.me/addstickers/{short_name}",
        )
    except Exception as exc:
        logger.error(f"[STICKER] Failed to send pack notification: {exc}")


# ============================================================
# Pack resolution (on startup / refresh)
# ============================================================


async def resolve_current_pack(client) -> dict:
    await ensure_me(client)
    cache = load_cache()

    user_id = me_user.id
    first_name = me_user.first_name or "User"

    if cache.get("user_id") != user_id:
        cache = {
            "user_id": user_id,
            "first_name": first_name,
            "current_pack_number": 1,
            "current_pack_short_name": None,
            "current_pack_count": 0,
        }

    pack_number = cache.get("current_pack_number", 1)
    short_name = build_pack_short_name(user_id, pack_number)

    while True:
        count = await get_pack_sticker_count(client, short_name)
        if count is None:
            cache["current_pack_number"] = pack_number
            cache["current_pack_short_name"] = None
            cache["current_pack_count"] = 0
            save_cache(cache)
            return cache

        if count < MAX_STICKERS_PER_PACK:
            cache["current_pack_number"] = pack_number
            cache["current_pack_short_name"] = short_name
            cache["current_pack_count"] = count
            save_cache(cache)
            return cache

        pack_number += 1
        short_name = build_pack_short_name(user_id, pack_number)


async def ensure_pack(client) -> dict:
    cache = load_cache()
    if not cache.get("current_pack_short_name") and cache.get("current_pack_count", 0) == 0:
        cache = await resolve_current_pack(client)
    return cache


# ============================================================
# Core add logic
# ============================================================


_PACK_FULL_ERRORS = (StickerpackStickersTooMuch, StickersTooMuch)


async def add_sticker_with_pack_management(
    client, sticker, emoji: str | None = None
) -> bool:
    try:
        cache = await ensure_pack(client)
        user_id = cache["user_id"]
        first_name = cache.get("first_name", "User")
        pack_number = cache["current_pack_number"]
        short_name = cache["current_pack_short_name"]
        count = cache["current_pack_count"]

        if short_name is None:
            short_name = build_pack_short_name(user_id, pack_number)
            title = build_pack_title(first_name, pack_number)
            await create_pack_with_sticker(client, title, short_name, sticker, emoji)
            cache["current_pack_short_name"] = short_name
            cache["current_pack_count"] = 1
            save_cache(cache)
            await send_pack_notification(client, title, short_name)
            return True

        await add_sticker_to_pack(client, short_name, sticker, emoji)
        count += 1
        cache["current_pack_count"] = count

        if count >= MAX_STICKERS_PER_PACK:
            cache["current_pack_number"] = pack_number + 1
            cache["current_pack_short_name"] = None
            cache["current_pack_count"] = 0

        save_cache(cache)
        return True

    except _PACK_FULL_ERRORS:
        cache = await ensure_pack(client)
        cache["current_pack_number"] = cache["current_pack_number"] + 1
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_cache(cache)
        return await add_sticker_with_pack_management(client, sticker, emoji)

    except StickersetInvalid:
        cache = await ensure_pack(client)
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_cache(cache)
        try:
            return await add_sticker_with_pack_management(client, sticker, emoji)
        except Exception:
            return False
    except Exception as exc:
        logger.error(f"[STICKER] Add failed: {type(exc).__name__}: {exc}")
        return False


# ============================================================
# Async queue worker (1 sticker at a time, 1s cooldown)
# ============================================================


async def _get_queue() -> asyncio.Queue:
    global _sticker_queue
    if _sticker_queue is None:
        _sticker_queue = asyncio.Queue()
    return _sticker_queue


async def _queue_worker(client):
    global _worker_running
    if _worker_running:
        return
    _worker_running = True
    queue = await _get_queue()

    while True:
        sticker, emoji, message = await queue.get()
        try:
            success = await add_sticker_with_pack_management(client, sticker, emoji)
            await send_result_feedback(client, message, success)
        except Exception as exc:
            logger.error(f"[STICKER] Queue worker error: {exc}")
            await send_result_feedback(client, message, False)
        finally:
            queue.task_done()
            await asyncio.sleep(ADD_COOLDOWN)


async def enqueue_sticker(client, sticker, emoji: str | None, message: Message):
    queue = await _get_queue()
    asyncio.ensure_future(_queue_worker(client))
    await queue.put((sticker, emoji, message))


# ============================================================
# Reaction / feedback helper
# ============================================================


async def send_result_feedback(client, message: Message, success: bool) -> None:
    reaction = "👍" if success else "💔"
    fallback_text = "Done" if success else "Fail"

    try:
        if hasattr(message, "react"):
            await message.react(reaction)
            return
        if hasattr(client, "send_reaction"):
            await client.send_reaction(message.chat.id, message.id, reaction)
            return
        raise RuntimeError("Reaction API is unavailable")
    except Exception:
        try:
            await message.reply_text(fallback_text)
        except Exception:
            pass


# ============================================================
# Commands
# ============================================================


@main.app.on_message(
    filters.me & filters.command("start_sticker_monitor", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def start_sticker_monitor(client, message: Message):
    monitoring_chats.add(message.chat.id)
    await message.edit(f"Started sticker monitor in this chat ({message.chat.id})")


@main.app.on_message(
    filters.me & filters.command("stop_sticker_monitor", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def stop_sticker_monitor(client, message: Message):
    monitoring_chats.clear()
    await message.edit("Stopped sticker monitor in all chats")


@main.app.on_message(
    filters.me & filters.command("kang", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def kang_command(client, message: Message):
    reply = message.reply_to_message
    if not reply or not reply.sticker:
        await message.edit("Reply to a sticker to kang it.")
        return

    parts = (message.text or "").split(maxsplit=1)
    emoji = parts[1].strip() if len(parts) > 1 else None

    await enqueue_sticker(client, reply.sticker, emoji, message)


# ============================================================
# Monitor handler (outgoing stickers in monitored chats)
# ============================================================


@main.app.on_message(filters.me & filters.sticker)
@continue_propagation
async def monitor_sticker_handler(client, message: Message):
    if message.chat.id not in monitoring_chats:
        return
    if message.sticker is None:
        return

    await enqueue_sticker(client, message.sticker, None, message)


# ============================================================
# Help command
# ============================================================


@main.app.on_message(
    filters.me & filters.command("help_sticker", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def help_sticker(client, message: Message):
    prefix = config.CMD_PREFIX
    await message.edit(
        "**Sticker Plugin — Help**\n\n"
        f"`{prefix}start_sticker_monitor`\n"
        "  Start monitoring outgoing stickers in this chat. "
        "Any sticker you send will be auto-added to your pack.\n\n"
        f"`{prefix}stop_sticker_monitor`\n"
        "  Stop monitoring stickers in ALL chats.\n\n"
        f"`{prefix}kang` (reply to sticker)\n"
        "  Copy the replied sticker into your pack. "
        "Specify emojis after the command to override the sticker's emoji.\n"
        f"  Example: `{prefix}kang 🐱🦊`\n\n"
        f"`{prefix}help_sticker`\n"
        "  Show this help message.\n\n"
        "---\n"
        "Packs auto-advance when full (120 stickers). "
        "New pack links are sent to Saved Messages."
    )
