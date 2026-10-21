import asyncio
import html
import json
import os
import random
import re
import string
import tempfile
from pathlib import Path

import emoji
from pyrogram import filters
from pyrogram.enums import ParseMode
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
MAX_ANIMATED_STICKERS_PER_PACK = 50
MAX_EMOJIS_PER_PACK = 200
MAX_EMOJI_COUNT = 6
MAX_NORMAL_PACK_SIZE = MAX_STICKERS_PER_PACK
MAX_ANIMATED_PACK_SIZE = MAX_ANIMATED_STICKERS_PER_PACK
MAX_EMOJI_PACK_SIZE = MAX_EMOJIS_PER_PACK
STICKER_PACK_URL_PREFIX = "https://t.me/addstickers/"
MAX_STICKER_SHORT_NAME_LENGTH = 62
PACK_TYPE_ICONS = {"normal": "📄", "animated": "💿", "emoji": "🪎"}
ADD_COOLDOWN = 1  # seconds between sticker adds
PACKS_PAGE_SIZE = 10
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


def _limit_pack_short_name(short_name: str) -> str:
    """Keep the full Telegram add-sticker URL within Telegram's 62-char limit."""
    return short_name[:MAX_STICKER_SHORT_NAME_LENGTH].rstrip("_")


def build_default_pack_names(
    user, pack_number: int, pack_type: str = "normal"
) -> tuple[str, str]:
    """Names for the auto ("no suffix") pack, e.g. '@OpPeace' / 'oppeace_vol1_7192'."""
    title_fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    name_fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)

    base = get_base_display(user)
    base_slug = get_base_slug(user)

    type_suffix = " (Animated)" if pack_type == "animated" else " Emojis" if pack_type == "emoji" else ""
    if title_fmt:
        title = _format_all(title_fmt, user, pack_number)
        if pack_type != "normal" and pack_type.lower() not in title.lower():
            title = f"{title}{type_suffix}"
    else:
        title = base if pack_number == 1 else f"{base} Vol{pack_number}"
        title += type_suffix

    if name_fmt:
        formatted_name = _format_all(name_fmt, user, pack_number)
        suffix = f"_vol{pack_number}_{user.id}"
        if pack_type != "normal" and pack_type not in formatted_name:
            suffix = f"_{pack_type}{suffix}"
        short_name = _limit_pack_short_name(
            f"{formatted_name[:MAX_STICKER_SHORT_NAME_LENGTH - len(suffix)]}{suffix}"
        )
    else:
        type_part = f"_{pack_type}" if pack_type != "normal" else ""
        suffix = f"_vol{pack_number}{type_part}_{user.id}"
        short_name = f"{base_slug[:MAX_STICKER_SHORT_NAME_LENGTH - len(suffix)]}{suffix}"

    return title, _limit_pack_short_name(short_name)


def build_themed_pack_names(
    user, suffix: str, volume: int, pack_type: str = "normal"
) -> tuple[str, str]:
    """Names for a themed pack, e.g. '@OpPeace fun' / 'oppeace_fun_7192'."""
    title_fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    name_fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)

    base = get_base_display(user)
    base_slug = get_base_slug(user)
    suffix_slug = slugify(suffix) or "pack"
    type_suffix = " (Animated)" if pack_type == "animated" else " Emojis" if pack_type == "emoji" else ""

    if title_fmt:
        title = _format_all(title_fmt, user, volume, suffix=suffix)
        if pack_type != "normal" and pack_type.lower() not in title.lower():
            title = f"{title}{type_suffix}"
    else:
        title = f"{base} {suffix}"
        if volume > 1:
            title += f" Vol{volume}"
        title += type_suffix

    if name_fmt:
        formatted_name = _format_all(name_fmt, user, volume, suffix=suffix_slug)
        user_marker = f"_{user.id}"
        marker = user_marker
        if pack_type != "normal" and pack_type not in formatted_name.lower():
            marker = f"_{pack_type}{marker}"
        if volume > 1 and f"{volume}" not in formatted_name:
            marker = f"_vol{volume}{marker}"
        if user_marker in formatted_name:
            short_name = _limit_pack_short_name(
                f"{formatted_name[:MAX_STICKER_SHORT_NAME_LENGTH - len(marker)]}"
                f"{marker if marker != user_marker else ''}"
            )
        else:
            short_name = _limit_pack_short_name(
                f"{formatted_name[:MAX_STICKER_SHORT_NAME_LENGTH - len(marker)]}{marker}"
            )
    else:
        volume_part = f"_vol{volume}" if volume > 1 else ""
        type_part = f"_{pack_type}" if pack_type != "normal" else ""
        suffix_part = f"{volume_part}{type_part}_{user.id}"
        base_part = f"{base_slug}_{suffix_slug}"
        short_name = (
            f"{base_part[:MAX_STICKER_SHORT_NAME_LENGTH - len(suffix_part)]}"
            f"{suffix_part}"
        )

    return title, _limit_pack_short_name(short_name)


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


def _get_input_pack_type(sticker) -> str:
    mime_type = (getattr(sticker, "mime_type", None) or "").lower()
    if (
        getattr(sticker, "is_animated", False)
        or getattr(sticker, "is_video", False)
        or mime_type in ("application/x-tgsticker", "video/webm")
    ):
        return "animated"
    attributes = getattr(sticker, "attributes", None) or []
    if any(
        isinstance(attribute, (types.DocumentAttributeAnimated, types.DocumentAttributeVideo))
        for attribute in attributes
    ):
        return "animated"
    return "normal"


def _pack_cache_key(pack_type: str) -> str:
    return f"{pack_type}_pack"


def _pack_limit(pack_type: str) -> int:
    return {
        "normal": MAX_NORMAL_PACK_SIZE,
        "animated": MAX_ANIMATED_PACK_SIZE,
        "emoji": MAX_EMOJI_PACK_SIZE,
    }[pack_type]


def _is_animated_document(document) -> bool:
    if getattr(document, "mime_type", "") in ("application/x-tgsticker", "video/webm"):
        return True
    return any(
        isinstance(attribute, (types.DocumentAttributeAnimated, types.DocumentAttributeVideo))
        for attribute in getattr(document, "attributes", [])
    )


async def _get_pack_type(client, short_name: str, is_emoji: bool = False, documents=None) -> str:
    if is_emoji:
        return "emoji"
    if documents:
        return "animated" if any(_is_animated_document(document) for document in documents) else "normal"
    stickers = await client.get_stickers(short_name)
    if any(getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False) for sticker in stickers):
        return "animated"
    return "normal"


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
    client,
    title: str,
    short_name: str,
    sticker,
    emoji: str | None = None,
    pack_type: str = "normal",
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
            emojis=(True if pack_type == "emoji" else None),
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
    candidate = _limit_pack_short_name(short_name)
    for attempt in range(6):
        if await short_name_available(client, candidate):
            return candidate
        candidate = _limit_pack_short_name(
            f"{short_name[:MAX_STICKER_SHORT_NAME_LENGTH - 5]}_{_random_token()}"
        )
    return candidate


async def create_pack_safe(
    client,
    title: str,
    short_name: str,
    sticker,
    emoji: str | None = None,
    pack_type: str = "normal",
) -> str:
    """Create a pack, retrying with a random suffix if the name is taken."""
    candidate = await make_unique_short_name(client, short_name)
    last_exc = None
    for attempt in range(4):
        try:
            await create_pack_with_sticker(
                client, title, candidate, sticker, emoji, pack_type
            )
            return candidate
        except Exception as exc:  # noqa: BLE001 - inspect message below
            last_exc = exc
            msg = str(exc).upper()
            if "SHORT_NAME" in msg or "OCCUPIED" in msg or "INVALID" in msg:
                candidate = _limit_pack_short_name(
                    f"{short_name[:MAX_STICKER_SHORT_NAME_LENGTH - 6]}_{_random_token(5)}"
                )
                continue
            raise
    raise last_exc if last_exc else RuntimeError("pack creation failed")


async def get_pack_sticker_count(client, pack_short_name: str) -> int | None:
    try:
        stickers = await client.get_stickers(pack_short_name)
        return len(stickers)
    except (StickersetInvalid, StickersEmpty):
        return None


def _pack_sort_key(pack: dict) -> tuple[int, str]:
    title = (pack.get("title") or "").strip()
    first = title[:1]
    if first and not first.isalnum():
        category = 0
    elif first and first.isdigit():
        category = 1
    else:
        category = 2
    return category, title.casefold()


async def _parse_owned_pack(client, covered) -> dict | None:
    sticker_set = getattr(covered, "set", None)
    if sticker_set is None:
        return None
    type_cover = (
        (getattr(covered, "documents", None) or [None])[0]
        if getattr(covered, "documents", None)
        else getattr(covered, "cover", None)
        or next(iter(getattr(covered, "covers", [])), None)
    )
    pack = {
        "id": sticker_set.id,
        "access_hash": sticker_set.access_hash,
        "title": sticker_set.title,
        "short_name": sticker_set.short_name,
        "count": sticker_set.count,
        "is_emoji": bool(getattr(sticker_set, "emojis", False)),
        "is_archived": bool(getattr(sticker_set, "archived", False)),
        "creator": bool(getattr(sticker_set, "creator", False)),
    }
    try:
        pack["type"] = await _get_pack_type(
            client,
            pack["short_name"],
            pack["is_emoji"],
            [type_cover] if type_cover else None,
        )
    except Exception as exc:
        logger.warning(
            f"[STICKER] Could not inspect pack type for {pack['short_name']}: {exc}"
        )
        pack["type"] = "emoji" if pack["is_emoji"] else "normal"
    pack["limit"] = _pack_limit(pack["type"])
    pack["icon"] = PACK_TYPE_ICONS[pack["type"]]
    return pack


async def fetch_all_owned_packs(client, include_archived: bool = True) -> list[dict]:
    await ensure_me(client)
    packs = []
    offset_id = 0
    page_size = 40
    while True:
        response = await client.invoke(
            functions.messages.GetMyStickers(offset_id=offset_id, limit=page_size)
        )
        batch = response.sets
        if not batch:
            break
        for covered in batch:
            pack = await _parse_owned_pack(client, covered)
            if pack is not None:
                packs.append(pack)
        if len(batch) < page_size:
            break
        offset_id = batch[-1].set.id
    if not include_archived:
        packs = [pack for pack in packs if not pack["is_archived"]]
    packs.sort(key=_pack_sort_key)
    return packs


async def fetch_owned_packs(
    client, include_archived: bool = True, page: int = 1, page_size: int = 100
) -> list[dict]:
    """Return a numbered page from the owned sticker-pack list."""
    if page < 1:
        return []
    packs = await fetch_all_owned_packs(client, include_archived)
    start = (page - 1) * page_size
    return packs[start : start + page_size]


async def fetch_archived_packs(client) -> list[dict]:
    packs = []
    offset_id = 0
    page_size = 40
    while True:
        response = await client.invoke(
            functions.messages.GetArchivedStickers(offset_id=offset_id, limit=page_size)
        )
        batch = getattr(response, "sets", []) or []
        if not batch:
            break
        for covered in batch:
            pack = await _parse_owned_pack(client, covered)
            if pack is not None:
                pack["is_archived"] = True
                packs.append(pack)
        if len(batch) < page_size:
            break
        offset_id = batch[-1].set.id
    return packs


async def _installed_pack_types(client) -> list[dict]:
    sets = []
    for request in (
        functions.messages.GetAllStickers(hash=0),
        functions.messages.GetEmojiStickers(hash=0),
        functions.messages.GetMaskStickers(hash=0),
    ):
        response = await client.invoke(request)
        sets.extend(getattr(response, "sets", []) or [])
    unique = {pack.id: pack for pack in sets}
    result = []
    for sticker_set in unique.values():
        kind = "emoji" if getattr(sticker_set, "emojis", False) else "normal"
        if kind != "emoji":
            try:
                stickers = await client.get_stickers(sticker_set.short_name)
                if any(st.is_animated or st.is_video for st in stickers):
                    kind = "animated"
            except Exception as exc:
                logger.debug(f"[STICKER] Could not inspect installed pack {sticker_set.short_name}: {exc}")
        result.append({"id": sticker_set.id, "type": kind})
    return result


def _format_pack_type_counts(counts: dict) -> str:
    parts = [
        f"{PACK_TYPE_ICONS[kind]} {counts.get(kind, 0)} {kind}"
        for kind in ("normal", "animated", "emoji")
        if counts.get(kind, 0)
    ]
    return " · ".join(parts) or "0"


async def get_pack_usage_stats(client) -> dict:
    owned_packs = await fetch_all_owned_packs(client)
    owned_by_id = {pack["id"]: pack for pack in owned_packs if pack["creator"]}
    installed = await _installed_pack_types(client)
    installed_by_id = {pack["id"]: pack for pack in installed}
    archived_packs = await fetch_archived_packs(client)
    archived_by_id = {pack["id"]: pack for pack in archived_packs}
    active_by_id = {
        pack_id: pack for pack_id, pack in installed_by_id.items()
        if pack_id not in archived_by_id
    }
    owned_active = [
        {**pack, "is_archived": False} for pack_id, pack in owned_by_id.items()
        if pack_id in active_by_id
    ]
    owned_archived = [
        {**pack, "is_archived": True} for pack_id, pack in owned_by_id.items()
        if pack_id in archived_by_id
    ]
    owned = sorted(owned_active + owned_archived, key=_pack_sort_key)
    active = sorted(owned_active, key=_pack_sort_key)

    def type_counts(packs):
        return {
            kind: sum(pack["type"] == kind for pack in packs)
            for kind in ("normal", "animated", "emoji")
        }

    overall_active_by_id = dict(active_by_id)
    overall_archived_by_id = {
        pack["id"]: pack for pack in archived_packs
    }
    overall_archived = list(overall_archived_by_id.values())
    overall_active = [
        pack
        for pack_id, pack in overall_active_by_id.items()
        if pack_id not in overall_archived_by_id
    ]
    overall_active_types = type_counts(overall_active)
    overall_archived_types = type_counts(overall_archived)
    overall_total_packs_by_id = {**overall_active_by_id, **overall_archived_by_id}
    overall_total_packs = list(overall_total_packs_by_id.values())
    overall_total_types = type_counts(overall_total_packs)
    overall_total = len(overall_total_packs_by_id)
    owned_active_types = type_counts(owned_active)
    owned_total_types = type_counts(owned)
    overall_active = sorted(overall_active, key=_pack_sort_key)
    overall_archived = sorted(overall_archived, key=_pack_sort_key)
    overall_total_packs = sorted(
        overall_total_packs_by_id.values(),
        key=_pack_sort_key,
    )
    return {
        "owned": len(owned),
        "active": len(owned_active),
        "archived": len(owned_archived),
        "total": len(owned),
        **owned_total_types,
        "owned_archived_types": type_counts(owned_archived),
        "owned_packs": owned,
        "active_packs": active,
        "overall_active": len(overall_active),
        "overall_archived": len(overall_archived),
        "overall_total": overall_total,
        "overall_active_types": overall_active_types,
        "overall_archived_types": overall_archived_types,
        "overall_total_types": overall_total_types,
        "overall_active_packs": overall_active,
        "overall_archived_packs": overall_archived,
        "overall_total_packs": overall_total_packs,
    }


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


async def resolve_current_pack(client, pack_type: str = "normal") -> dict:
    await ensure_me(client)
    cache = load_cache()

    user_id = me_user.id
    first_name = me_user.first_name or "User"

    if cache.get("user_id") != user_id:
        cache = {"user_id": user_id, "first_name": first_name}

    pack_key = _pack_cache_key(pack_type)
    legacy_name = cache.get("current_pack_short_name")
    legacy_count = cache.get("current_pack_count", 0)
    legacy_number = cache.get("current_pack_number", 1)
    state = cache.get(pack_key)
    if state is None and pack_type == "normal" and legacy_name:
        if await get_pack_sticker_count(client, legacy_name) is not None:
            legacy_packs = await fetch_all_owned_packs(client)
            legacy_pack = next(
                (pack for pack in legacy_packs if pack["short_name"] == legacy_name),
                None,
            )
            if legacy_pack and legacy_pack["type"] == "normal":
                state = {
                    "user_id": user_id,
                    "first_name": first_name,
                    "current_pack_number": legacy_number,
                    "current_pack_short_name": legacy_name,
                    "current_pack_count": legacy_count,
                    "type": "normal",
                }
                cache[pack_key] = state
    if state is None:
        state = {
            "user_id": user_id,
            "current_pack_number": 1,
            "current_pack_short_name": None,
            "current_pack_count": 0,
            "type": pack_type,
        }
        cache[pack_key] = state
    pack_number = state.get("current_pack_number", 1)

    while True:
        _, short_name = build_default_pack_names(me_user, pack_number, pack_type)
        count = await get_pack_sticker_count(client, short_name)
        state = {
            "user_id": user_id,
            "first_name": first_name,
            "current_pack_number": pack_number,
            "current_pack_short_name": short_name if count is not None else None,
            "current_pack_count": count or 0,
            "type": pack_type,
        }
        cache[pack_key] = state
        save_cache(cache)
        if count is None or count < _pack_limit(pack_type):
            return {**cache, **state}
        pack_number += 1


async def ensure_pack(client, pack_type: str = "normal") -> dict:
    cache = load_cache()
    pack_key = _pack_cache_key(pack_type)
    state = cache.get(pack_key, {})
    if not state.get("current_pack_short_name") and state.get("current_pack_count", 0) == 0:
        return await resolve_current_pack(client, pack_type)
    return {**cache, **state}


def save_pack_state(pack_type: str, state: dict) -> None:
    cache = load_cache()
    cache[_pack_cache_key(pack_type)] = {
        key: state[key]
        for key in (
            "user_id", "first_name", "current_pack_number",
            "current_pack_short_name", "current_pack_count", "type",
        )
        if key in state
    }
    cache["user_id"] = state.get("user_id")
    cache["first_name"] = state.get("first_name")
    save_cache(cache)


# ============================================================
# Pack resolution (themed / explicit target)
# ============================================================


def _parse_volume(title: str, prefix: str) -> int | None:
    """Return the volume number for a title matching '{prefix}' or '{prefix} VolN'."""
    if title == prefix:
        return 1
    match = re.fullmatch(re.escape(prefix) + r"(?: Vol)?(\d+)", title)
    if match:
        return int(match.group(1))
    return None


async def resolve_themed_target(
    client, suffix: str, pack_type: str = "normal"
) -> dict:
    """Find (or plan to create) the pack for a given suffix/theme.

    Returns a target dict understood by add_sticker_to_target.
    """
    await ensure_me(client)
    pack_type = pack_type or "normal"
    packs = await fetch_all_owned_packs(client, include_archived=False)

    base = get_base_display(me_user)
    type_suffix = " (Animated)" if pack_type == "animated" else " Emojis" if pack_type == "emoji" else ""
    prefix = f"{base} {suffix}{type_suffix}"

    volumes: dict[int, dict] = {}
    for pack in packs:
        if pack["type"] != pack_type or pack["is_archived"]:
            continue
        vol = _parse_volume(pack["title"], prefix)
        if vol is not None:
            volumes[vol] = pack

    if volumes:
        for vol in sorted(volumes):
            if volumes[vol]["count"] < _pack_limit(pack_type):
                pack = volumes[vol]
                return {
                    **pack,
                    "create": False,
                    "suffix": suffix,
                    "volume": vol,
                    "type": pack_type,
                }
        next_vol = max(volumes) + 1
    else:
        next_vol = 1

    title, short_name = build_themed_pack_names(me_user, suffix, next_vol, pack_type)
    return {
        "create": True,
        "title": title,
        "short_name": short_name,
        "suffix": suffix,
        "volume": next_vol,
        "type": pack_type,
    }


async def resolve_pack_reference(client, ref: str) -> dict | None:
    """Resolve a numeric id, global list index, or a short name."""
    global _pack_index
    packs = await fetch_all_owned_packs(client, include_archived=False)
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
        pack_type = _get_input_pack_type(sticker)
        cache = await ensure_pack(client, pack_type)
        user_id = cache["user_id"]
        first_name = cache.get("first_name", "User")
        pack_number = cache["current_pack_number"]
        short_name = cache["current_pack_short_name"]
        count = cache["current_pack_count"]

        if short_name is None:
            pack_type = _get_input_pack_type(sticker)
            title, short_name = build_default_pack_names(
                me_user, pack_number, pack_type
            )
            created = await create_pack_safe(
                client, title, short_name, sticker, emoji, pack_type
            )
            cache["current_pack_short_name"] = created
            cache["current_pack_count"] = 1
            cache["type"] = pack_type
            save_pack_state(pack_type, cache)
            await send_pack_notification(client, title, created)
            return True

        await add_sticker_to_pack(client, short_name, sticker, emoji)
        count += 1
        cache["current_pack_count"] = count
        cache["type"] = pack_type

        if count >= _pack_limit(pack_type):
            cache["current_pack_number"] = pack_number + 1
            cache["current_pack_short_name"] = None
            cache["current_pack_count"] = 0

        save_pack_state(pack_type, cache)
        return True

    except _PACK_FULL_ERRORS:
        pack_type = _get_input_pack_type(sticker)
        cache = await ensure_pack(client, pack_type)
        cache["current_pack_number"] = cache["current_pack_number"] + 1
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_pack_state(pack_type, cache)
        return await add_sticker_with_pack_management(client, sticker, emoji)

    except StickersetInvalid:
        pack_type = _get_input_pack_type(sticker)
        cache = await ensure_pack(client, pack_type)
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_pack_state(pack_type, cache)
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
            pack_type = _get_input_pack_type(sticker)
            created = await create_pack_safe(
                client, title, target["short_name"], sticker, emoji, target.get("type", pack_type)
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


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


async def packs_search(client, message: Message, query: str) -> None:
    """Render `.packs search <keyword>` results (global sticker-set search)."""
    if not query:
        await message.edit(
            f"Usage: <code>{_esc(config.CMD_PREFIX)}packs search &lt;keyword&gt;</code>\n"
            "Searches all public Telegram sticker packs.",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.edit(
        f"🔎 Searching sticker packs for <b>{_esc(query)}</b>…",
        parse_mode=ParseMode.HTML,
    )
    try:
        results = await search_sticker_sets(client, query)
    except Exception as exc:
        logger.exception("[STICKER] Pack search failed")
        await message.edit(
            f"❌ Search failed: {_esc(type(exc).__name__)}: {_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    if not results:
        await message.edit(
            f"No sticker packs found for <b>{_esc(query)}</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [f"<b>Sticker packs matching</b> {_esc(query)} ({len(results)})\n"]
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
        tag = f" · {' · '.join(tags)}" if tags else ""
        pack_type = "emoji" if pack["emojis"] else "normal"
        icon = PACK_TYPE_ICONS[pack_type]
        limit = _pack_limit(pack_type)
        url = f"{STICKER_PACK_URL_PREFIX}{pack['short_name']}"
        lines.append(
            f'<blockquote>{i}. <a href="{_esc(url)}">'
            f"{_esc(pack['title'])}</a> {icon}{_esc(tag)}\n"
            f"    {_esc(pack['short_name'])} · {pack['count']}/{limit}</blockquote>"
        )
    if len(results) > MAX_DISPLAY_SEARCH:
        lines.append(f"\n…and {len(results) - MAX_DISPLAY_SEARCH} more.")
    await message.edit(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


@main.app.on_message(
    filters.me
    & filters.command(["packs", "mypacks"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def packs_command(client, message: Message):
    global _pack_index
    logger.info(f"[STICKER] packs command received in chat={message.chat.id}")

    raw = (message.text or "").split(maxsplit=1)
    args = raw[1].strip() if len(raw) > 1 else ""
    tokens = args.split()

    if tokens and tokens[0].lower() in ("search", "find"):
        await packs_search(client, message, " ".join(tokens[1:]))
        return

    count_only = False
    page = 1
    selected_types: set[str] = set()
    for token in tokens:
        flag = token.lower()
        if flag in ("-c", "-count"):
            count_only = True
        elif flag in ("-e", "-emoji"):
            selected_types.add("emoji")
        elif flag in ("-a", "-animated"):
            selected_types.add("animated")
        elif flag in ("-n", "-normal"):
            selected_types.add("normal")
        elif flag in ("-s", "-sticker"):
            selected_types.update(("normal", "animated"))
        elif flag.isdigit() and page == 1:
            page = int(flag)
        elif flag not in ("search", "find"):
            await message.edit(
                f"Usage: <code>{_esc(config.CMD_PREFIX)}packs [page] "
                "[-c] [-e] [-a] [-n] [-s]</code> or "
                f"<code>{_esc(config.CMD_PREFIX)}packs search &lt;keyword&gt;</code>",
                parse_mode=ParseMode.HTML,
            )
            return

    try:
        stats = await get_pack_usage_stats(client)
        archived_count = stats["archived"]
        owned_packs = stats["owned_packs"]
        all_active_packs = stats["active_packs"]
    except Exception as exc:
        logger.exception("[STICKER] Failed to fetch owned/archived pack counts")
        await message.edit(
            f"❌ Failed to fetch packs: {type(exc).__name__}: {exc}"
        )
        return

    counts = {
        pack_type: stats[pack_type]
        for pack_type in ("normal", "animated", "emoji")
    }
    selected_types = {"normal", "animated", "emoji"} if len(selected_types) == 3 else selected_types
    filter_types = selected_types or {"normal", "animated", "emoji"}
    if count_only:
        await message.edit(
            "<b>Sticker pack counts</b>\n\n"
            "<b>Owned ✨</b>\n"
            "<blockquote>"
            f"Total: {stats['owned']} ({_format_pack_type_counts(counts)})\n"
            f"Active: {stats['active']}\n"
            f"🗃️ Archived: {stats['archived']} "
            f"({_format_pack_type_counts(stats['owned_archived_types'])})"
            "</blockquote>\n\n"
            "<b>Overall 🌐</b>\n"
            "<blockquote>"
            f"Total: {stats['overall_total']} "
            f"({_format_pack_type_counts(stats['overall_total_types'])})\n"
            f"Active: {stats['overall_active']}\n"
            f"🗃️ Archived: {stats['overall_archived']} "
            f"({_format_pack_type_counts(stats['overall_archived_types'])})"
            "</blockquote>",
            parse_mode=ParseMode.HTML,
        )
        return

    filtered_count = sum(counts[pack_type] for pack_type in filter_types)
    if not filtered_count:
        await message.edit(
            "No active sticker packs match that filter.",
            parse_mode=ParseMode.HTML,
        )
        return

    packs = (
        [pack for pack in owned_packs if pack["type"] in filter_types]
        if selected_types
        else owned_packs
    )
    _pack_index = owned_packs

    total_pages = max(1, (len(packs) + PACKS_PAGE_SIZE - 1) // PACKS_PAGE_SIZE)
    if page < 1 or page > total_pages:
        await message.edit(
            f"Page must be between 1 and {total_pages}.",
            parse_mode=ParseMode.HTML,
        )
        return

    start = (page - 1) * PACKS_PAGE_SIZE
    page_packs = packs[start : start + PACKS_PAGE_SIZE]
    if selected_types:
        visible_numbers = {
            pack["id"]: all_active_packs.index(pack) + 1 for pack in page_packs
        }
    else:
        visible_numbers = {
            pack["id"]: index + 1
            for index, pack in enumerate(page_packs, start)
        }
    filter_label = ", ".join(sorted(filter_types))
    lines = [
        f"<b>Your sticker packs</b> · page {page}/{total_pages} · "
        f"{len(packs)} shown · {stats['owned']} owned · "
        f"{archived_count} archived\n",
        f"Counts: 📄 {counts['normal']} normal · 💿 {counts['animated']} animated · "
        f"🪎 {counts['emoji']} emoji"
        f"{f' · filter: {_esc(filter_label)}' if selected_types else ''}\n",
    ]
    lines.append("<blockquote>")
    for pack in page_packs:
        number = visible_numbers[pack["id"]]
        pack_type = pack["type"]
        limit = _pack_limit(pack_type)
        icon = PACK_TYPE_ICONS[pack_type]
        item_name = "emoji" if pack_type == "emoji" else "stickers"
        url = f"{STICKER_PACK_URL_PREFIX}{pack['short_name']}"
        lines.append(
            f'{number}. <a href="{_esc(url)}">'
            f"{_esc(pack['title'])}</a> {icon} "
            f"({pack['count']}/{limit} {item_name})"
            f"{' 🗃️' if pack['is_archived'] else ''}\n"
            f"    short: {_esc(pack['short_name'])}\n"
            f"    id: {pack['id']}  •  {pack['count']}/{limit} {item_name}\n"
        )
    lines.append("</blockquote>")
    nav = []
    filter_args = " ".join(token for token in tokens if token.startswith("-"))
    if page > 1:
        nav.append(
            f"◀ <code>{_esc(config.CMD_PREFIX)}packs {filter_args} {page - 1}</code>".strip()
        )
    if page < total_pages:
        nav.append(
            f"<code>{_esc(config.CMD_PREFIX)}packs {filter_args} {page + 1}</code> ▶"
        )
    if nav:
        lines.append("  ·  ".join(nav))
    lines.append(
        f"Search public packs with <code>{_esc(config.CMD_PREFIX)}packs search &lt;keyword&gt;</code>. "
        f"Use <code>{_esc(config.CMD_PREFIX)}kang &lt;number|shortname&gt;</code> or "
        f"<code>{_esc(config.CMD_PREFIX)}kang &lt;suffix&gt;</code> to add stickers."
    )
    await message.edit(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


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
                target = await resolve_themed_target(
                    client, pack_ref, _get_input_pack_type(media)
                )
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
