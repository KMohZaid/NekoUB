import asyncio
import json
import os
import random
import re
import string
import tempfile
from pathlib import Path

import emoji
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
from userbot.media import image_to_sticker, video_to_sticker
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / ".sticker_cache.json"
MAX_STICKERS_PER_PACK = 120
MAX_EMOJI_COUNT = 6
ADD_COOLDOWN = 1  # seconds between sticker adds
MAX_DISPLAY_PACKS = 50  # cap for the .packs listing
MAX_DISPLAY_SEARCH = 40  # cap for `.packs search` results

# --- Runtime state ---
monitoring_chats: set[int] = set()
me_user = None
_sticker_queue: asyncio.Queue | None = None
_worker_running = False
_pack_index: list[dict] = []  # last fetched list of owned packs (for .kang <number>)


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


def _strip_quotes(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def slugify(text: str | None) -> str:
    """Turn arbitrary text into a Telegram-safe short-name fragment."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def get_base_display(user) -> str:
    """Human-facing base name, e.g. '@OpPeace' (falls back to first name)."""
    override = _strip_quotes(config.STICKERSET_BASE)
    if override:
        return override
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    return user.first_name or "User"


def get_base_slug(user) -> str:
    """Short-name-safe version of the base, e.g. 'oppeace'."""
    override = _strip_quotes(config.STICKERSET_BASE)
    if override:
        slug = slugify(override)
    else:
        username = getattr(user, "username", None)
        slug = slugify(username) if username else slugify(user.first_name)
    if not slug or not slug[0].isalpha():
        slug = "s" + slug
    return slug


def _format_all(template: str, user, index: int, suffix: str = "") -> str:
    return template.format(
        user_id=user.id,
        first_name=user.first_name or "User",
        username=getattr(user, "username", "") or "",
        base=get_base_display(user),
        suffix=suffix,
        index=index,
    )


def build_default_pack_names(user, pack_number: int) -> tuple[str, str]:
    """Names for the auto ("no suffix") pack, e.g. '@OpPeace' / 'oppeace_vol1_7192'."""
    title_fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    name_fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)

    base = get_base_display(user)
    base_slug = get_base_slug(user)

    if title_fmt:
        title = _format_all(title_fmt, user, pack_number)
    else:
        title = base if pack_number == 1 else f"{base} Vol{pack_number}"

    if name_fmt:
        short_name = _format_all(name_fmt, user, pack_number)
    else:
        short_name = f"{base_slug}_vol{pack_number}_{user.id}"

    return title, short_name


def build_themed_pack_names(user, suffix: str, volume: int) -> tuple[str, str]:
    """Names for a themed pack, e.g. '@OpPeace fun' / 'oppeace_fun_7192'."""
    title_fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    name_fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)

    base = get_base_display(user)
    base_slug = get_base_slug(user)
    suffix_slug = slugify(suffix) or "pack"

    if title_fmt:
        title = _format_all(title_fmt, user, volume, suffix=suffix)
    else:
        title = f"{base} {suffix}"
        if volume > 1:
            title += f" Vol{volume}"

    if name_fmt:
        short_name = _format_all(name_fmt, user, volume, suffix=suffix_slug)
    else:
        short_name = f"{base_slug}_{suffix_slug}"
        if volume > 1:
            short_name += f"_vol{volume}"
        short_name += f"_{user.id}"

    return title, short_name


# Keep the old public helpers working for any external callers.
def build_pack_short_name(user_id: int, pack_number: int) -> str:
    if me_user is not None:
        return build_default_pack_names(me_user, pack_number)[1]
    fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)
    if fmt:
        return fmt.format(user_id=user_id, index=pack_number)
    return f"a{user_id}_vol{pack_number}"


def build_pack_title(first_name: str, pack_number: int) -> str:
    if me_user is not None:
        return build_default_pack_names(me_user, pack_number)[0]
    fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    if fmt:
        return fmt.format(first_name=first_name, index=pack_number)
    return f"{first_name}'s Pack Vol{pack_number}"


def _random_token(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


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


def message_media(message: Message) -> tuple[str | None, object]:
    """Classify a message's media as sticker/image/video (None if unsupported)."""
    if message.sticker:
        return "sticker", message.sticker
    if message.photo:
        return "image", message.photo
    if message.animation:
        return "video", message.animation
    if message.video:
        return "video", message.video
    if message.document:
        mime = (message.document.mime_type or "").lower()
        name = (message.document.file_name or "").lower()
        if mime.startswith("image/gif") or name.endswith(".gif"):
            return "video", message.document
        if mime.startswith("image/"):
            return "image", message.document
        if mime.startswith("video/"):
            return "video", message.document
    return None, None


async def convert_and_upload(client, message: Message, kind: str):
    """Convert a photo/GIF/video to a sticker and upload it as a document.

    Returns (document, uploaded_message); the caller removes the message once
    the sticker has been stored in a pack.
    """
    with tempfile.TemporaryDirectory() as folder:
        source = await client.download_media(
            message, file_name=os.path.join(folder, "source")
        )
        if not source:
            return None, None
        output = os.path.join(
            folder, "sticker.webm" if kind == "video" else "sticker.webp"
        )
        if kind == "video":
            await video_to_sticker(source, output)
        else:
            await image_to_sticker(source, output)
        uploaded = await client.send_document(
            "me", output, disable_notification=True
        )
    return getattr(uploaded, "document", None), uploaded


async def add_sticker_to_pack(
    client, pack_short_name: str, sticker, emoji: str | None = None
) -> None:
    input_doc = decode_sticker_file_id(sticker)
    await client.invoke(
        functions.stickers.AddStickerToSet(
            stickerset=types.InputStickerSetShortName(
                short_name=pack_short_name
            ),
            sticker=types.InputStickerSetItem(
                document=input_doc,
                emoji=emoji or getattr(sticker, "emoji", None) or "🌟",
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
                    emoji=emoji or getattr(sticker, "emoji", None) or "🌟",
                    keywords="",
                )
            ],
        )
    )


async def short_name_available(client, short_name: str) -> bool:
    try:
        return bool(
            await client.invoke(
                functions.stickers.CheckShortName(short_name=short_name)
            )
        )
    except Exception:
        # If the check itself fails, assume available and let create decide.
        return True


async def make_unique_short_name(client, short_name: str) -> str:
    candidate = short_name[:60]
    for attempt in range(6):
        if await short_name_available(client, candidate):
            return candidate
        candidate = f"{short_name[:54]}_{_random_token()}"
    return candidate


async def create_pack_safe(
    client, title: str, short_name: str, sticker, emoji: str | None = None
) -> str:
    """Create a pack, retrying with a random suffix if the name is taken."""
    candidate = await make_unique_short_name(client, short_name)
    last_exc = None
    for attempt in range(4):
        try:
            await create_pack_with_sticker(client, title, candidate, sticker, emoji)
            return candidate
        except Exception as exc:  # noqa: BLE001 - inspect message below
            last_exc = exc
            msg = str(exc).upper()
            if "SHORT_NAME" in msg or "OCCUPIED" in msg or "INVALID" in msg:
                candidate = f"{short_name[:50]}_{_random_token(5)}"
                continue
            raise
    raise last_exc if last_exc else RuntimeError("pack creation failed")


async def get_pack_sticker_count(client, pack_short_name: str) -> int | None:
    try:
        stickers = await client.get_stickers(pack_short_name)
        return len(stickers)
    except (StickersetInvalid, StickersEmpty):
        return None


async def fetch_owned_packs(client) -> list[dict]:
    """Return every sticker set created by the current account."""
    await ensure_me(client)
    result = await client.invoke(
        functions.messages.GetMyStickers(offset_id=0, limit=100)
    )
    packs: list[dict] = []
    for covered in result.sets:
        s = getattr(covered, "set", None)
        if s is None:
            continue
        packs.append(
            {
                "id": s.id,
                "access_hash": s.access_hash,
                "title": s.title,
                "short_name": s.short_name,
                "count": s.count,
            }
        )
    packs.sort(key=lambda p: p["title"].lower())
    return packs


async def search_sticker_sets(
    client, query: str, exclude_featured: bool = False
) -> list[dict]:
    """Search Telegram for public sticker sets by keyword.

    Uses ``messages.searchStickerSets`` (users-only). Returns a list of
    dicts with the set's title, short name, id, sticker count and flags.
    """
    result = await client.invoke(
        functions.messages.SearchStickerSets(
            q=query,
            hash=0,
            exclude_featured=exclude_featured or None,
        )
    )
    # ``FoundStickerSetsNotModified`` is returned when the cached hash
    # matches; there is nothing to show in that case.
    covered = getattr(result, "sets", None)
    if not covered:
        return []

    found: list[dict] = []
    for item in covered:
        s = getattr(item, "set", None)
        if s is None:
            continue
        found.append(
            {
                "id": s.id,
                "access_hash": s.access_hash,
                "title": s.title,
                "short_name": s.short_name,
                "count": s.count,
                "official": bool(getattr(s, "official", False)),
                "masks": bool(getattr(s, "masks", False)),
                "emojis": bool(getattr(s, "emojis", False)),
            }
        )
    return found


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
# Pack resolution (default auto pack)
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

    while True:
        _, short_name = build_default_pack_names(me_user, pack_number)
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


async def ensure_pack(client) -> dict:
    cache = load_cache()
    if (
        not cache.get("current_pack_short_name")
        and cache.get("current_pack_count", 0) == 0
    ):
        cache = await resolve_current_pack(client)
    return cache


# ============================================================
# Pack resolution (themed / explicit target)
# ============================================================


def _parse_volume(title: str, prefix: str) -> int | None:
    """Return the volume number for a title matching '{prefix}' or '{prefix} VolN'."""
    if title == prefix:
        return 1
    match = re.fullmatch(re.escape(prefix) + r" Vol(\d+)", title)
    if match:
        return int(match.group(1))
    return None


async def resolve_themed_target(client, suffix: str) -> dict:
    """Find (or plan to create) the pack for a given suffix/theme.

    Returns a target dict understood by add_sticker_to_target.
    """
    await ensure_me(client)
    packs = await fetch_owned_packs(client)

    base = get_base_display(me_user)
    prefix = f"{base} {suffix}"

    volumes: dict[int, dict] = {}
    for pack in packs:
        vol = _parse_volume(pack["title"], prefix)
        if vol is not None:
            volumes[vol] = pack

    if volumes:
        for vol in sorted(volumes):
            if volumes[vol]["count"] < MAX_STICKERS_PER_PACK:
                pack = volumes[vol]
                return {
                    "create": False,
                    "title": pack["title"],
                    "short_name": pack["short_name"],
                    "id": pack["id"],
                    "access_hash": pack["access_hash"],
                    "suffix": suffix,
                    "volume": vol,
                }
        next_vol = max(volumes) + 1
    else:
        next_vol = 1

    title, short_name = build_themed_pack_names(me_user, suffix, next_vol)
    return {
        "create": True,
        "title": title,
        "short_name": short_name,
        "suffix": suffix,
        "volume": next_vol,
    }


async def resolve_pack_reference(client, ref: str) -> dict | None:
    """Resolve a numeric id, 1-based list index, or a short name."""
    global _pack_index
    packs = await fetch_owned_packs(client)
    _pack_index = packs

    if ref.isdigit():
        number = int(ref)
        for pack in packs:
            if pack["id"] == number:
                return {**pack, "create": False}
        if 1 <= number <= len(packs):
            return {**packs[number - 1], "create": False}

    lowered = ref.lower()
    for pack in packs:
        if pack["short_name"].lower() == lowered:
            return {**pack, "create": False}

    return None


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
            title, short_name = build_default_pack_names(
                me_user, pack_number
            )
            created = await create_pack_safe(
                client, title, short_name, sticker, emoji
            )
            cache["current_pack_short_name"] = created
            cache["current_pack_count"] = 1
            save_cache(cache)
            await send_pack_notification(client, title, created)
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
            return await add_sticker_with_pack_management(
                client, sticker, emoji
            )
        except Exception:
            return False
    except Exception as exc:
        logger.error(f"[STICKER] Add failed: {type(exc).__name__}: {exc}")
        return False


async def add_sticker_to_target(
    client, target: dict, sticker, emoji: str | None = None
) -> bool:
    """Add a sticker to a specific (themed or referenced) target pack."""
    try:
        if target.get("create"):
            title = target["title"]
            created = await create_pack_safe(
                client, title, target["short_name"], sticker, emoji
            )
            await send_pack_notification(client, title, created)
            return True

        await add_sticker_to_pack(client, target["short_name"], sticker, emoji)
        return True

    except _PACK_FULL_ERRORS:
        suffix = target.get("suffix")
        if suffix:
            fresh = await resolve_themed_target(client, suffix)
            return await add_sticker_to_target(client, fresh, sticker, emoji)
        return await add_sticker_with_pack_management(client, sticker, emoji)

    except StickersetInvalid:
        suffix = target.get("suffix")
        if suffix:
            fresh = await resolve_themed_target(client, suffix)
            return await add_sticker_to_target(client, fresh, sticker, emoji)
        return await add_sticker_with_pack_management(client, sticker, emoji)

    except Exception as exc:
        logger.error(
            f"[STICKER] Target add failed: {type(exc).__name__}: {exc}"
        )
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
        media_message, emoji_str, message, target = await queue.get()
        uploaded = None
        try:
            kind, media = message_media(media_message)
            source = media if kind == "sticker" else None
            if kind in ("image", "video"):
                source, uploaded = await convert_and_upload(
                    client, media_message, kind
                )
            if source is None:
                logger.error("[STICKER] Unsupported or empty media")
                await send_result_feedback(client, message, False)
                continue
            if target is None:
                success = await add_sticker_with_pack_management(
                    client, source, emoji_str
                )
            else:
                success = await add_sticker_to_target(
                    client, target, source, emoji_str
                )
            await send_result_feedback(client, message, success)
        except Exception as exc:
            logger.error(f"[STICKER] Queue worker error: {exc}")
            await send_result_feedback(client, message, False)
        finally:
            if uploaded is not None:
                try:
                    await uploaded.delete()
                except Exception:
                    pass
            queue.task_done()
            await asyncio.sleep(ADD_COOLDOWN)


async def enqueue_sticker(
    client,
    media_message: Message,
    emoji_str: str | None,
    message: Message,
    target: dict | None = None,
):
    queue = await _get_queue()
    asyncio.ensure_future(_queue_worker(client))
    await queue.put((media_message, emoji_str, message, target))


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
# Argument parsing
# ============================================================


def _looks_like_pack_ref(token: str) -> bool:
    if not token:
        return False
    # Emojis are never pack references.
    if emoji.emoji_list(token):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_.@+-]+", token))


def parse_kang_args(text: str) -> tuple[str | None, str, bool]:
    """Split kang args into (pack_ref, emoji_string, append_mode)."""
    tokens = text.split()
    pack_ref: str | None = None
    append_mode = False
    emoji_tokens: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "-a":
            append_mode = True
        elif token in ("-p", "--pack"):
            if i + 1 < len(tokens):
                pack_ref = tokens[i + 1]
                i += 1
            else:
                emoji_tokens.append(token)
        elif token.startswith("--pack="):
            pack_ref = token.split("=", 1)[1]
        elif token.startswith("-") and len(token) > 1:
            # Unknown flag; ignore it rather than treating it as a pack name.
            pass
        elif pack_ref is None and _looks_like_pack_ref(token):
            pack_ref = token
        else:
            emoji_tokens.append(token)
        i += 1

    return pack_ref, " ".join(emoji_tokens), append_mode


# ============================================================
# Commands
# ============================================================


@main.app.on_message(
    filters.me
    & filters.command("start_sticker_monitor", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def start_sticker_monitor(client, message: Message):
    monitoring_chats.add(message.chat.id)
    await message.edit(
        f"Started sticker monitor in this chat ({message.chat.id})"
    )


@main.app.on_message(
    filters.me
    & filters.command("stop_sticker_monitor", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def stop_sticker_monitor(client, message: Message):
    monitoring_chats.clear()
    await message.edit("Stopped sticker monitor in all chats")


def _md_escape(text: str) -> str:
    for ch in ("_", "*", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


async def packs_search(client, message: Message, query: str) -> None:
    """Render `.packs search <keyword>` results (global sticker-set search)."""
    if not query:
        await message.edit(
            f"Usage: `{config.CMD_PREFIX}packs search <keyword>`\n"
            "Searches all public Telegram sticker packs."
        )
        return

    await message.edit(f"🔎 Searching sticker packs for `{query}`…")
    try:
        results = await search_sticker_sets(client, query)
    except Exception as exc:
        logger.exception("[STICKER] Pack search failed")
        await message.edit(f"❌ Search failed: {type(exc).__name__}: {exc}")
        return

    if not results:
        await message.edit(f"No sticker packs found for `{query}`.")
        return

    lines = [f"**Sticker packs matching** `{query}` ({len(results)})\n"]
    for i, pack in enumerate(results[:MAX_DISPLAY_SEARCH], 1):
        tags = [
            label
            for flag, label in (
                ("official", "official"),
                ("masks", "masks"),
                ("emojis", "emoji"),
            )
            if pack[flag]
        ]
        tag = f"  _({' · '.join(tags)})_" if tags else ""
        lines.append(
            f"`{i}.` **{_md_escape(pack['title'])}**{tag}\n"
            f"    `{pack['short_name']}`  •  {pack['count']} stickers\n"
            f"    https://t.me/addstickers/{pack['short_name']}"
        )
    if len(results) > MAX_DISPLAY_SEARCH:
        lines.append(f"\n…and {len(results) - MAX_DISPLAY_SEARCH} more.")
    await message.edit("\n".join(lines))


@main.app.on_message(
    filters.me
    & filters.command(["packs", "mypacks"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def packs_command(client, message: Message):
    global _pack_index

    raw = (message.text or "").split(maxsplit=1)
    args = raw[1].strip() if len(raw) > 1 else ""

    # `.packs search <keyword>` (aliases: find, s) -> global search.
    if args:
        head, _, rest = args.partition(" ")
        if head.lower() in ("search", "find", "s"):
            await packs_search(client, message, rest.strip())
            return

    try:
        packs = await fetch_owned_packs(client)
    except Exception as exc:
        await message.edit(
            f"❌ Failed to fetch packs: {type(exc).__name__}: {exc}"
        )
        return

    _pack_index = packs

    if not packs:
        await message.edit(
            "You don't own any sticker packs yet.\n"
            "Reply to a sticker and use `.kang` to create one."
        )
        return

    lines = [f"**Your sticker packs** ({len(packs)})\n"]
    for i, pack in enumerate(packs[:MAX_DISPLAY_PACKS], 1):
        lines.append(
            f"`{i}.` **{pack['title']}**\n"
            f"    short: `{pack['short_name']}`\n"
            f"    id: `{pack['id']}`  •  {pack['count']}/{MAX_STICKERS_PER_PACK} stickers\n"
            f"    https://t.me/addstickers/{pack['short_name']}"
        )
    if len(packs) > MAX_DISPLAY_PACKS:
        lines.append(f"\n…and {len(packs) - MAX_DISPLAY_PACKS} more.")
    lines.append(
        "\nSearch all public packs with `.packs search <keyword>`.\n"
        "Use `.kang <number|shortname>` to add to a specific pack, "
        "or `.kang <suffix>` for a themed pack (e.g. `.kang fun`)."
    )
    await message.edit("\n".join(lines))


@main.app.on_message(
    filters.me & filters.command("kang", prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def kang_command(client, message: Message):
    reply = message.reply_to_message
    if not reply:
        await message.edit(
            "Reply to a sticker, image, GIF, or video to kang it."
        )
        return

    kind, media = message_media(reply)
    if kind is None:
        await message.edit(
            "Unsupported media. Reply to a sticker, image, GIF, or video."
        )
        return

    raw = (message.text or "").split(maxsplit=1)
    args = raw[1] if len(raw) > 1 else ""

    pack_ref, emoji_str, append_mode = parse_kang_args(args)

    # Resolve the target pack (None => default auto pack).
    target: dict | None = None
    if pack_ref:
        try:
            target = await resolve_pack_reference(client, pack_ref)
        except Exception as exc:
            logger.error(f"[STICKER] Pack reference lookup failed: {exc}")
            target = None
        if target is None:
            # Not an existing pack: treat the reference as a themed suffix.
            try:
                target = await resolve_themed_target(client, pack_ref)
            except Exception as exc:
                logger.error(f"[STICKER] Themed pack lookup failed: {exc}")
                await message.reply_text(
                    f"Couldn't resolve pack `{pack_ref}`: {type(exc).__name__}"
                )
                return

    user_emojis = list(
        dict.fromkeys(x["emoji"] for x in emoji.emoji_list(emoji_str))
    )
    emoji_count = len(user_emojis)

    # No emoji supplied at all -> keep the sticker's own emoji.
    if emoji_count == 0:
        if emoji_str.strip():
            await message.reply_text(
                "no emoji huh, its fine but not recommended. try again"
            )
            return
        await enqueue_sticker(client, reply, None, message, target)
        return

    if append_mode:
        sticker_emojis = list(
            dict.fromkeys(
                x["emoji"]
                for x in emoji.emoji_list(getattr(media, "emoji", "") or "")
            )
        )
        combined = sticker_emojis + user_emojis
        if len(combined) > MAX_EMOJI_COUNT:
            await message.reply_text(
                f"Too many emojis ({len(combined)}/6). "
                f"Sticker has {len(sticker_emojis)}, you added {emoji_count}."
            )
            return
        await enqueue_sticker(client, reply, "".join(combined), message, target)
    else:
        if emoji_count > MAX_EMOJI_COUNT:
            await message.reply_text(f"Too many emojis ({emoji_count}/6).")
            return
        await enqueue_sticker(
            client, reply, "".join(user_emojis), message, target
        )


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

    await enqueue_sticker(client, message, None, message, None)


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
        f"`{prefix}kang` (reply to a sticker, image, GIF or video)\n"
        "  Copy it into your default (auto) pack. Photos become static\n"
        "  stickers; GIFs and videos become video stickers.\n\n"
        f"`{prefix}kang <number|shortname>`\n"
        "  Add to a specific pack you own. Get numbers from "
        f"`{prefix}packs`.\n\n"
        f"`{prefix}kang <suffix>`\n"
        "  Add to (or create) a themed pack, e.g. "
        f"`{prefix}kang fun` → \"@you fun\".\n"
        f"  `-p <pack>` / `--pack=<pack>` : explicit target pack.\n\n"
        f"Emoji options:\n"
        f"  `{prefix}kang 🐱` — use 🐱 as the sticker emoji\n"
        f"  `{prefix}kang -a 🐱` — append 🐱 to the sticker's emojis\n"
        f"  `{prefix}kang fun 🐱 -a` — themed pack + append emoji\n\n"
        f"`{prefix}packs`\n"
        "  List every sticker pack you own with its number, short name, "
        "id and sticker count.\n\n"
        f"`{prefix}packs search <keyword>`\n"
        "  Search all public Telegram sticker packs by keyword.\n\n"
        f"`{prefix}start_sticker_monitor` / `{prefix}stop_sticker_monitor`\n"
        "  Auto-kang every sticker you send in a chat.\n\n"
        "---\n"
        "Packs auto-advance when full (120 stickers). "
        "New pack links are sent to Saved Messages."
    )
