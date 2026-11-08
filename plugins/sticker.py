import asyncio
import hashlib
import html
import json
import math
import os
import random
import re
import string
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import emoji
from pyrogram import Client, enums, filters, types as pyrogram_types
from pyrogram.enums import ParseMode, StickerFormat, StickerType
from pyrogram.types import InputSticker
from pyrogram.errors import (
    StickersetInvalid,
    StickersEmpty,
    StickerpackStickersTooMuch,
    StickersTooMuch,
)
from pyrogram.file_id import FileId, FileType
from pyrogram.raw import functions, types
from pyrogram.types import Message

import config
import main
from plugins import sticker_index
from utils.media import image_to_sticker, video_to_sticker
from core.plugins import Example, Option, Plugin, PluginContext, ParsedOptions, handlers as h
from core.plugins.errors import parse_options
from utils import get_logger

logger = get_logger(__name__)

CACHE_PATH = Path(__file__).resolve().parent.parent / ".sticker_cache.json"
MAX_STICKERS_PER_PACK = 120
MAX_EMOJIS_PER_PACK = 200
MAX_EMOJI_COUNT = 6
MAX_EMOJI_PACK_SIZE = MAX_EMOJIS_PER_PACK
STICKER_PACK_URL_PREFIX = "https://t.me/addstickers/"
MAX_STICKER_SHORT_NAME_LENGTH = 62
PACK_TYPE_ICONS = {"normal": "📄", "animated": "💿", "mixed": "📺", "emoji": "🪎"}
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
    # TODO: Make proper use of the database to store user preferences.
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cache(data: dict) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=CACHE_PATH.parent,
            prefix=f"{CACHE_PATH.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            json.dump(data, temporary, indent=2)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, CACHE_PATH)
    except OSError as exc:
        logger.error(f"[STICKER] Cache write failed: {exc}")
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except OSError:
                pass


# ============================================================
# User / pack name helpers
# ============================================================


async def ensure_me(client) -> None:
    global me_user
    if me_user is None:
        me_user = await client.get_me()


async def cache_owned_packs(client) -> list[dict]:
    """Refresh the cached list of packs owned by the current user."""
    await ensure_me(client)
    packs = await fetch_all_owned_packs(client)
    cache = load_cache()
    if cache.get("user_id") != me_user.id:
        cache = {"user_id": me_user.id}
    cache["owned_packs"] = packs
    save_cache(cache)
    return packs


async def cached_owned_packs(client) -> list[dict]:
    """Return the owned-pack index, initializing it if needed."""
    await ensure_me(client)
    cache = load_cache()
    if cache.get("user_id") != me_user.id or not isinstance(cache.get("owned_packs"), list):
        return await cache_owned_packs(client)
    return cache["owned_packs"]


def _strip_quotes(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def slugify(text: str | None) -> str:
    """Turn arbitrary text into a Telegram-safe short-name fragment."""
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def get_base_display(user) -> str:
    """Return username, full name, or first name for display titles."""
    override = _strip_quotes(config.STICKERSET_BASE)
    if override:
        return override
    username = getattr(user, "username", None)
    if username:
        return f"@{username}"
    full_name = " ".join(
        part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None))
        if part
    )
    return full_name or "User"


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


_NAMING_WARNINGS: set[str] = set()
_NAMING_FIELDS = {"user_id", "first_name", "username", "base", "suffix", "index"}


def _format_all(template: str, user, index: int, suffix: str = "") -> str:
    values = {
        "user_id": user.id,
        "first_name": user.first_name or "User",
        "username": getattr(user, "username", "") or "",
        "base": get_base_display(user),
        "suffix": suffix,
        "index": index,
    }
    return template.format(**values)


def _valid_naming_template(template: str, required: set[str]) -> bool:
    fields = set(re.findall(r"\{([^{}]+)\}", template))
    return bool(template) and required.issubset(fields) and fields.issubset(_NAMING_FIELDS)


def _warn_invalid_naming(label: str, required: set[str]) -> None:
    missing = ", ".join(f"{{{name}}}" for name in sorted(required))
    warning = (
        f"Your {label} is missing required variable(s) {missing}; "
        "will use base format."
    )
    if warning not in _NAMING_WARNINGS:
        logger.warning(warning)
        _NAMING_WARNINGS.add(warning)


def _custom_naming_value(template: str, label: str, required: set[str]) -> bool:
    valid = _valid_naming_template(template, required)
    if not valid:
        _warn_invalid_naming(label, required)
    return valid


def _limit_pack_short_name(short_name: str) -> str:
    """Keep the full Telegram add-sticker URL within Telegram's 62-char limit."""
    return short_name[:MAX_STICKER_SHORT_NAME_LENGTH].rstrip("_")


def build_default_pack_names(
    user, pack_number: int, pack_type: str = "normal"
) -> tuple[str, str]:
    """Names for the auto ("no suffix") pack, e.g. '@OpPeace' / 'oppeace_vol1_7192'."""
    title_fmt = _strip_quotes(config.STICKERSET_TITLE_FORMAT)
    name_fmt = _strip_quotes(config.STICKERSET_NAME_FORMAT)

    if _custom_naming_value(name_fmt, "sticker set shortname", {"index"}):
        short_name = _limit_pack_short_name(
            _format_all(name_fmt, user, pack_number)
        )
    else:
        short_name = f"{user.id}_vol{pack_number}"

    if _custom_naming_value(title_fmt, "sticker set title", {"index"}):
        title = _format_all(title_fmt, user, pack_number)
    else:
        title = f"{base}'s Pack Vol. {pack_number}"
    if pack_type == "emoji" and "emoji" not in title.lower():
        title += " Emojis"

    return title, _limit_pack_short_name(short_name)


def build_themed_pack_names(
    user, suffix: str, volume: int, pack_type: str = "normal"
) -> tuple[str, str]:
    """Names for a themed pack, e.g. '@OpPeace fun' / 'oppeace_fun_7192'."""
    themed_title_fmt = _strip_quotes(config.THEMED_STICKERSET_DISPLAY_NAME)
    themed_name_fmt = _strip_quotes(config.THEMED_STICKERSET_SHORTNAME)

    base = get_base_display(user)
    suffix_slug = slugify(suffix) or "theme"
    if _custom_naming_value(
        themed_name_fmt, "themed sticker set shortname", {"suffix", "index"}
    ):
        short_name = _limit_pack_short_name(
            _format_all(themed_name_fmt, user, volume, suffix=suffix_slug)
        )
    else:
        short_name = f"{user.id}_{suffix_slug}_vol{volume}"

    if _custom_naming_value(
        themed_title_fmt, "themed sticker set title", {"suffix", "index"}
    ):
        title = _format_all(themed_title_fmt, user, volume, suffix=suffix)
    else:
        title = f"{base}'s {suffix} Pack Vol. {volume}"
    if pack_type == "emoji" and "emoji" not in title.lower():
        title += " Emojis"

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


def decode_sticker_file_id(sticker):
    return FileId.decode(sticker.file_id)


def sticker_input_document(sticker) -> types.InputDocument:
    file_id = decode_sticker_file_id(sticker)
    if file_id.file_type not in (FileType.STICKER, FileType.DOCUMENT):
        raise TypeError(
            f"Expected a Telegram sticker or document file ID, "
            f"got {file_id.file_type.name}"
        )
    return types.InputDocument(
        id=file_id.media_id,
        access_hash=file_id.access_hash,
        file_reference=file_id.file_reference,
    )


def _media_fingerprint(path: str, is_video: bool) -> int | str:
    if is_video:
        digest = hashlib.sha256()
        with open(path, "rb") as media_file:
            for block in iter(lambda: media_file.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    from PIL import Image, ImageOps

    size = 32
    with Image.open(path) as image:
        pixels = list(
            ImageOps.exif_transpose(image)
            .convert("L")
            .resize((size, size))
            .getdata()
        )
    coeffs = []
    for v in range(8):
        for u in range(8):
            coeffs.append(
                sum(
                    pixels[y * size + x]
                    * math.cos((2 * x + 1) * u * math.pi / (2 * size))
                    * math.cos((2 * y + 1) * v * math.pi / (2 * size))
                    for y in range(size)
                    for x in range(size)
                )
            )
    median = sorted(coeffs[1:])[len(coeffs[1:]) // 2]
    return sum(1 << i for i, value in enumerate(coeffs) if value > median)


async def _find_duplicate_sticker(
    client, sticker, documents, fingerprint_cache: dict | None = None
) -> bool:
    is_video = bool(getattr(sticker, "is_video", False))
    folder = tempfile.TemporaryDirectory(prefix="nekoub-dedupe-")
    try:
        source_hash = (
            fingerprint_cache.get("_source_hash")
            if fingerprint_cache is not None
            else None
        )
        if source_hash is None:
            source_path = (
                sticker.file_id
                if isinstance(sticker.file_id, (str, os.PathLike))
                and os.path.isfile(sticker.file_id)
                else await client.download_media(
                    sticker.file_id, file_name=os.path.join(folder.name, "source")
                )
            )
            if not source_path:
                return False
            source_hash = await asyncio.to_thread(
                _media_fingerprint, source_path, is_video
            )
            if fingerprint_cache is not None:
                fingerprint_cache["_source_hash"] = source_hash
    except Exception:
        logger.warning("[STICKER] Could not fingerprint input for duplicate check", exc_info=True)
        return False

    try:
        for index, document in enumerate(documents):
            if getattr(document, "mime_type", None) != getattr(sticker, "mime_type", None):
                continue
            if getattr(document, "size", None) != getattr(sticker, "file_size", None):
                continue
            try:
                candidate_unique_id = getattr(document, "file_unique_id", None)
                cache_key = (
                    candidate_unique_id,
                    getattr(document, "mime_type", None),
                    getattr(document, "size", None),
                    is_video,
                )
                cached = fingerprint_cache.get(cache_key) if fingerprint_cache is not None else None
                if cached is not None:
                    candidate_hash = cached
                else:
                    candidate_path = await client.download_media(
                        sticker_set_document_file_id(document),
                        file_name=os.path.join(folder.name, f"candidate-{index}"),
                    )
                    if not candidate_path:
                        continue
                    candidate_hash = await asyncio.to_thread(
                        _media_fingerprint, candidate_path, is_video
                    )
                    if fingerprint_cache is not None and candidate_unique_id:
                        fingerprint_cache[cache_key] = candidate_hash
            except Exception:
                logger.debug(
                    "[STICKER] Could not fingerprint existing pack item", exc_info=True
                )
                continue
            if (
                (source_hash ^ candidate_hash).bit_count() <= 4
                if isinstance(source_hash, int) and isinstance(candidate_hash, int)
                else source_hash == candidate_hash
            ):
                return True
    finally:
        folder.cleanup()
    return False


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
        mime = (getattr(message.document, "mime_type", None) or "").lower()
        name = (getattr(message.document, "file_name", None) or "").lower()
        suffix = Path(name).suffix
        if mime == "image/gif" or suffix == ".gif":
            return "video", message.document
        if mime.startswith("image/") or suffix in {
            ".bmp", ".heic", ".heif", ".jpeg", ".jpg", ".png", ".tif",
            ".tiff", ".webp",
        }:
            return "image", message.document
        if mime.startswith("video/") or suffix in {
            ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm",
        }:
            return "video", message.document
    return None, None


async def convert_and_upload(client, message: Message, kind: str):
    """Convert replied media locally for direct InputSticker file upload."""
    folder = tempfile.TemporaryDirectory(prefix="nekoub-kang-")
    try:
        source = await client.download_media(
            message, file_name=os.path.join(folder.name, "source")
        )
        if not source:
            folder.cleanup()
            return None, None
        output = os.path.join(
            folder.name, "sticker.webm" if kind == "video" else "sticker.webp"
        )
        if kind == "video":
            await video_to_sticker(source, output)
        else:
            await image_to_sticker(source, output)
        sticker = SimpleNamespace(
            file_id=output,
            file_unique_id=None,
            mime_type="video/webm" if kind == "video" else "image/webp",
            file_size=os.path.getsize(output),
            is_video=kind == "video",
            is_animated=False,
            emoji=None,
        )
        return sticker, folder
    except Exception:
        folder.cleanup()
        raise


def _sticker_format(sticker) -> StickerFormat:
    mime_type = (getattr(sticker, "mime_type", None) or "").lower()
    if (
        getattr(sticker, "is_video", False)
        or mime_type == "video/webm"
    ):
        return StickerFormat.VIDEO
    if getattr(sticker, "is_animated", False) or mime_type == "application/x-tgsticker":
        return StickerFormat.ANIMATED
    attributes = getattr(sticker, "attributes", None) or []
    if any(
        isinstance(attribute, types.DocumentAttributeVideo)
        for attribute in attributes
    ):
        return StickerFormat.VIDEO
    if any(
        isinstance(attribute, types.DocumentAttributeAnimated)
        for attribute in attributes
    ):
        return StickerFormat.ANIMATED
    return StickerFormat.STATIC


def _get_input_pack_type(sticker) -> str:
    return "normal"


def _pack_cache_key(pack_type: str) -> str:
    return f"{pack_type}_pack"


def _pack_limit(pack_type: str) -> int:
    if pack_type in ("normal", "animated", "mixed"):
        return MAX_STICKERS_PER_PACK
    return MAX_EMOJIS_PER_PACK


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
    if not documents:
        sticker_set = await client.get_sticker_set(short_name)
        documents = getattr(sticker_set, "stickers", None) or []
    has_static = False
    has_animated = False
    for sticker in documents:
        if _is_animated_document(sticker) or getattr(sticker, "is_animated", False) or getattr(sticker, "is_video", False):
            has_animated = True
        else:
            has_static = True
        if has_static and has_animated:
            return "mixed"
    return "animated" if has_animated else "normal"


async def add_sticker_to_pack(
    client, pack_short_name: str, sticker, emoji: str | None = None,
    fetch_result: bool = False,
    response_holder: dict | None = None,
    allow_duplicates: bool = False,
):
    previous = await client.invoke(
        functions.messages.GetStickerSet(
            stickerset=types.InputStickerSetShortName(short_name=pack_short_name),
            hash=0,
        )
    )
    # file_unique_id is mostly useless across packs: Telegram can give the same
    # sticker a different ID in another pack. Still check it first; when it does
    # match, we can skip the more expensive media-download/fingerprint checks.
    if not allow_duplicates and getattr(sticker, "file_unique_id", None) and any(
        getattr(document, "file_unique_id", None) == sticker.file_unique_id
        for document in previous.documents
    ):
        if response_holder is not None:
            response_holder["sticker_set"] = previous
        return (previous if fetch_result else None), False, previous
    if not allow_duplicates and await _find_duplicate_sticker(
        client, sticker, previous.documents
    ):
        if response_holder is not None:
            response_holder["sticker_set"] = previous
        return (previous if fetch_result else None), False, previous
    try:
        result = await client.add_sticker_to_set(
            user_id="me",
            name=pack_short_name,
            sticker=InputSticker(
                sticker=sticker.file_id,
                format=_sticker_format(sticker),
                emoji_list=[emoji or getattr(sticker, "emoji", None) or "🌟"],
            ),
        )
        added = True
        if response_holder is not None:
            response_holder["sticker_set"] = result
        return result, added, previous
    except Exception as exc:
        if "STICKERSET_STICKER_ALREADY_EXISTS" not in str(exc).upper():
            raise

    result = await client.invoke(
        functions.messages.GetStickerSet(
            stickerset=types.InputStickerSetShortName(short_name=pack_short_name),
            hash=0,
        )
    )
    if response_holder is not None:
        response_holder["sticker_set"] = result
    return (result if fetch_result else None), False, previous


async def sticker_is_in_pack(client, pack_short_name: str, sticker) -> bool:
    """Verify Telegram actually includes a sticker in the requested pack."""
    file_id = getattr(sticker, "file_id", None)
    if not file_id:
        return False
    try:
        media_id = FileId.decode(file_id).media_id
        sticker_set = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=pack_short_name),
                hash=0,
            )
        )
    except Exception:
        logger.exception(
            f"[STICKER] Could not verify sticker membership in {pack_short_name}"
        )
        return False
    return any(
        getattr(document, "id", None) == media_id
        for document in getattr(sticker_set, "documents", []) or []
    )


def sticker_set_document_file_id(document) -> str:
    """Convert a raw or parsed Telegram sticker document into a sticker file ID."""
    # Parsed Pyrogram documents already carry a reusable file ID and may not
    # expose raw MTProto fields such as dc_id. Prefer that representation.
    file_id = getattr(document, "file_id", None)
    if file_id:
        return file_id

    raw_document = getattr(document, "raw", None) or document
    dc_id = getattr(raw_document, "dc_id", None)
    media_id = getattr(raw_document, "id", None)
    access_hash = getattr(raw_document, "access_hash", None)
    file_reference = getattr(raw_document, "file_reference", None)
    if all(value is not None for value in (dc_id, media_id, access_hash, file_reference)):
        return FileId(
            file_type=FileType.STICKER,
            dc_id=dc_id,
            media_id=media_id,
            access_hash=access_hash,
            file_reference=file_reference,
        ).encode()

    raise TypeError(
        f"Expected a Telegram sticker document with raw file fields or file_id, "
        f"got {type(document).__name__}"
    )


async def find_added_sticker_in_result(
    client, pack_short_name: str, result, previous
) -> str | None:
    """Return the newly added sticker from Kurigram's parsed set result."""
    stickers = getattr(result, "stickers", None) or []
    if not stickers:
        result = await client.get_sticker_set(pack_short_name)
        stickers = getattr(result, "stickers", None) or []
    return getattr(stickers[-1], "file_id", None) if stickers else None


def _json_value(value, *, include_raw=False):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_json_value(item, include_raw=include_raw) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item, include_raw=include_raw) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        attributes = vars(value).items()
    elif getattr(type(value), "__slots__", None):
        attributes = (
            (key, getattr(value, key))
            for key in type(value).__slots__
            if hasattr(value, key)
        )
    else:
        return repr(value)
    result = {}
    for key, item in attributes:
        if key.startswith("_"):
            continue
        if key == "raw" and not include_raw:
            continue
        result[key] = _json_value(item, include_raw=include_raw)
    return {"_": type(value).__name__, **result}


async def send_sticker_set_debug_json(
    client, replied_message, input_sticker, response_sticker_set
) -> None:
    """Upload the add-call inputs and full returned set to the configured log chat."""
    if not config.DEBUG_CHAT_ID:
        return
    payload = {
        "replied_msg": _json_value(replied_message, include_raw=True),
        "inpit_sticker": _json_value(input_sticker, include_raw=True),
        "response_stickerset": _json_value(response_sticker_set, include_raw=True),
    }
    path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, default=repr)
            path = output.name
        await client.send_document(
            config.DEBUG_CHAT_ID,
            path,
            file_name="kang-sticker-set-response.json",
            disable_notification=True,
        )
    except Exception as exc:
        logger.warning(f"[STICKER] Failed to upload sticker-set JSON: {exc}")
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


async def find_sticker_in_result(
    client, result, source_sticker=None, source_kind="sticker"
):
    """Choose the pack item matching a sticker, or the newest converted item."""
    stickers = getattr(result, "stickers", None) or []
    if not stickers:
        return None
    if source_kind != "sticker":
        return stickers[-1]

    unique_id = getattr(source_sticker, "file_unique_id", None)
    if unique_id:
        match = next(
            (
                item for item in stickers
                if getattr(item, "file_unique_id", None) == unique_id
            ),
            None,
        )
        if match is not None:
            return match

    # Telegram can assign a different unique ID when a sticker is added to a
    # pack. Fall back to comparing the downloaded media content.
    is_video = bool(getattr(source_sticker, "is_video", False))
    with tempfile.TemporaryDirectory(prefix="nekoub-match-") as folder:
        source_path = (
            source_sticker.file_id
            if isinstance(source_sticker.file_id, (str, os.PathLike))
            and os.path.isfile(source_sticker.file_id)
            else await client.download_media(
                source_sticker.file_id, file_name=os.path.join(folder, "source")
            )
        )
        if not source_path:
            return None
        source_hash = await asyncio.to_thread(_media_fingerprint, source_path, is_video)
        for index, candidate in enumerate(stickers):
            if unique_id and getattr(candidate, "file_unique_id", None) == unique_id:
                continue
            try:
                candidate_path = await client.download_media(
                    candidate.file_id,
                    file_name=os.path.join(folder, f"candidate-{index}"),
                )
                if not candidate_path:
                    continue
                candidate_hash = await asyncio.to_thread(
                    _media_fingerprint, candidate_path, is_video
                )
            except Exception:
                logger.debug("[STICKER] Could not compare pack sticker media", exc_info=True)
                continue
            if (
                (source_hash ^ candidate_hash).bit_count() <= 4
                if isinstance(source_hash, int) and isinstance(candidate_hash, int)
                else source_hash == candidate_hash
            ):
                return candidate
    return None


async def create_pack_with_sticker(
    client,
    title: str,
    short_name: str,
    sticker,
    emoji: str | None = None,
    pack_type: str = "normal",
):
    return await client.create_new_sticker_set(
        user_id="me",
        name=short_name,
        title=title,
        stickers=[
            InputSticker(
                sticker=sticker.file_id,
                format=_sticker_format(sticker),
                emoji_list=[emoji or getattr(sticker, "emoji", None) or "🌟"],
            )
        ],
        sticker_type=(
            StickerType.CUSTOM_EMOJI if pack_type == "emoji" else StickerType.REGULAR
        ),
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
) -> tuple[str, object]:
    """Create a pack, retrying with a random suffix if the name is taken."""
    candidate = await make_unique_short_name(client, short_name)
    last_exc = None
    for attempt in range(4):
        try:
            result = await create_pack_with_sticker(
                client, title, candidate, sticker, emoji, pack_type
            )
            if isinstance(result, types.messages.StickerSetNotModified):
                result = await client.invoke(
                    functions.messages.GetStickerSet(
                        stickerset=types.InputStickerSetShortName(short_name=candidate),
                        hash=0,
                    )
                )
            return candidate, result
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
        sticker_set = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=pack_short_name),
                hash=0,
            )
        )
        return len(getattr(sticker_set, "documents", None) or [])
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
        documents = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=pack["short_name"]),
                hash=0,
            )
        )
        pack["type"] = await _get_pack_type(
            client,
            pack["short_name"],
            pack["is_emoji"],
            getattr(documents, "documents", None)
            or ([type_cover] if type_cover else None),
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
        parsed = await asyncio.gather(
            *(_parse_owned_pack(client, covered) for covered in batch)
        )
        packs.extend(pack for pack in parsed if pack is not None)
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
                pack = await client.invoke(
                    functions.messages.GetStickerSet(
                        stickerset=types.InputStickerSetShortName(
                            short_name=sticker_set.short_name
                        ),
                        hash=0,
                    )
                )
                stickers = getattr(pack, "documents", None) or []
                has_static = any(not _is_animated_document(st) for st in stickers)
                has_animated = any(_is_animated_document(st) for st in stickers)
                kind = "mixed" if has_static and has_animated else "animated" if has_animated else "normal"
            except Exception as exc:
                logger.debug(f"[STICKER] Could not inspect installed pack {sticker_set.short_name}: {exc}")
        result.append({"id": sticker_set.id, "type": kind})
    return result


def _format_pack_type_counts(counts: dict) -> str:
    parts = [
        f"{PACK_TYPE_ICONS[kind]} {counts.get(kind, 0)} {kind}"
        for kind in ("normal", "animated", "mixed", "emoji")
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
            for kind in ("normal", "animated", "mixed", "emoji")
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
    # Rescan from the first volume instead of assuming the cached current
    # volume is still the latest; gaps define the end of the sequence.
    pack_number = 1

    # Walk consecutive volumes until the first missing one. Keep the last
    # existing volume as current; only advance when it is already full.
    last_existing = None
    while True:
        _, short_name = build_default_pack_names(me_user, pack_number, pack_type)
        count = await get_pack_sticker_count(client, short_name)
        if count is None:
            if last_existing is None:
                state.update(
                    current_pack_number=pack_number,
                    current_pack_short_name=None,
                    current_pack_count=0,
                    type=pack_type,
                    first_name=first_name,
                )
            elif last_existing["current_pack_count"] >= _pack_limit(pack_type):
                state.update(
                    current_pack_number=pack_number,
                    current_pack_short_name=None,
                    current_pack_count=0,
                    type=pack_type,
                    first_name=first_name,
                )
            else:
                state.update(last_existing)
            cache[pack_key] = state
            save_cache(cache)
            return {**cache, **state}

        last_existing = {
            "user_id": user_id,
            "first_name": first_name,
            "current_pack_number": pack_number,
            "current_pack_short_name": short_name,
            "current_pack_count": count,
            "type": pack_type,
        }
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
    type_suffix = " Emojis" if pack_type == "emoji" else ""
    prefix = f"{base} {suffix}{type_suffix}"

    volumes: dict[int, dict] = {}
    for pack in packs:
        if pack["is_emoji"] != (pack_type == "emoji") or pack["is_archived"]:
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
    if ref.isdigit():
        packs = await fetch_all_owned_packs(client, include_archived=False)
        _pack_index = packs
        number = int(ref)
        for pack in packs:
            if pack["id"] == number:
                return {**pack, "create": False}
        if 1 <= number <= len(packs):
            return {**packs[number - 1], "create": False}
        return None

    try:
        result = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=ref),
                hash=0,
            )
        )
    except StickersetInvalid:
        return None

    sticker_set = getattr(result, "set", None)
    if sticker_set is None or not getattr(sticker_set, "creator", False):
        return None
    documents = getattr(result, "documents", []) or []
    pack_type = await _get_pack_type(
        client,
        sticker_set.short_name,
        bool(getattr(sticker_set, "emojis", False)),
        documents,
    )
    return {
        "id": sticker_set.id,
        "access_hash": sticker_set.access_hash,
        "title": sticker_set.title,
        "short_name": sticker_set.short_name,
        "count": sticker_set.count,
        "is_emoji": bool(getattr(sticker_set, "emojis", False)),
        "is_archived": bool(getattr(sticker_set, "archived", False)),
        "creator": bool(getattr(sticker_set, "creator", False)),
        "type": pack_type,
        "limit": _pack_limit(pack_type),
        "icon": PACK_TYPE_ICONS[pack_type],
        "create": False,
    }


# ============================================================
# Core add logic
# ============================================================


_PACK_FULL_ERRORS = (StickerpackStickersTooMuch, StickersTooMuch)


async def add_sticker_with_pack_management(
    client,
    sticker,
    emoji: str | None = None,
    need_file_id: bool = False,
    response_holder: dict | None = None,
    allow_duplicates: bool = False,
) -> tuple[str | None, str | None, bool]:
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
            previous = None
            if need_file_id:
                try:
                    previous = await client.invoke(
                        functions.messages.GetStickerSet(
                            stickerset=types.InputStickerSetShortName(short_name=short_name),
                            hash=0,
                        )
                    )
                except StickersetInvalid:
                    pass
            created, result = await create_pack_safe(
                client, title, short_name, sticker, emoji, pack_type
            )
            if response_holder is not None:
                response_holder["sticker_set"] = result
            cache["current_pack_short_name"] = created
            cache["current_pack_count"] = 1
            cache["type"] = pack_type
            save_pack_state(pack_type, cache)
            file_id = (
                await find_added_sticker_in_result(
                    client, created, result, previous
                )
                if need_file_id else None
            )
            return created, file_id, True

        result, added, previous = await add_sticker_to_pack(
            client,
            short_name,
            sticker,
            emoji,
            fetch_result=need_file_id,
            response_holder=response_holder,
            allow_duplicates=allow_duplicates,
        )
        if added:
            count += 1
        elif not need_file_id:
            # The API rejects an existing document; no pack response is needed here.
            pass
        cache["current_pack_count"] = count
        cache["type"] = pack_type

        if count >= _pack_limit(pack_type):
            cache["current_pack_number"] = pack_number + 1
            cache["current_pack_short_name"] = None
            cache["current_pack_count"] = 0

        save_pack_state(pack_type, cache)
        file_id = (
            await find_added_sticker_in_result(
                client, short_name, result, previous
            )
            if need_file_id and added else None
        )
        return short_name, file_id, added

    except _PACK_FULL_ERRORS:
        pack_type = _get_input_pack_type(sticker)
        cache = await ensure_pack(client, pack_type)
        cache["current_pack_number"] = cache["current_pack_number"] + 1
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_pack_state(pack_type, cache)
        return await add_sticker_with_pack_management(
            client,
            sticker,
            emoji,
            need_file_id,
            allow_duplicates=allow_duplicates,
        )

    except StickersetInvalid:
        pack_type = _get_input_pack_type(sticker)
        cache = await ensure_pack(client, pack_type)
        stale_name = cache.get("current_pack_short_name")
        logger.warning(
            "[STICKER] Current pack %s is invalid; resetting %s pack state",
            stale_name, pack_type,
        )
        cache["current_pack_short_name"] = None
        cache["current_pack_count"] = 0
        save_pack_state(pack_type, cache)
        try:
            return await add_sticker_with_pack_management(
                client,
                sticker,
                emoji,
                need_file_id,
                allow_duplicates=allow_duplicates,
            )
        except Exception:
            logger.exception(
                "[STICKER] Retry after invalid pack failed (pack=%s, type=%s)",
                stale_name, pack_type,
            )
            return None, None, False
    except Exception as exc:
        logger.exception(
            "[STICKER] Add failed (pack=%s, type=%s, sticker=%s): %s: %s",
            locals().get("short_name"), locals().get("pack_type"),
            getattr(sticker, "file_unique_id", None), type(exc).__name__, exc,
        )
        return None, None, False


async def add_sticker_to_target(
    client,
    target: dict,
    sticker,
    emoji: str | None = None,
    need_file_id: bool = False,
    response_holder: dict | None = None,
    allow_duplicates: bool = False,
) -> tuple[str | None, str | None, bool]:
    """Add a sticker to a specific (themed or referenced) target pack."""
    try:
        if target.get("create"):
            title = target["title"]
            pack_type = target.get("type") or _get_input_pack_type(sticker)
            previous = None
            if need_file_id:
                try:
                    previous = await client.invoke(
                        functions.messages.GetStickerSet(
                            stickerset=types.InputStickerSetShortName(
                                short_name=target["short_name"]
                            ),
                            hash=0,
                        )
                    )
                except StickersetInvalid:
                    pass
            created, result = await create_pack_safe(
                client, title, target["short_name"], sticker, emoji, pack_type
            )
            if response_holder is not None:
                response_holder["sticker_set"] = result
            await send_pack_notification(client, title, created)
            file_id = (
                await find_added_sticker_in_result(
                    client, created, result, previous
                )
                if need_file_id else None
            )
            return created, file_id, True

        result, added, previous = await add_sticker_to_pack(
            client,
            target["short_name"],
            sticker,
            emoji,
            fetch_result=need_file_id,
            response_holder=response_holder,
            allow_duplicates=allow_duplicates,
        )
        file_id = (
            await find_added_sticker_in_result(
                client, target["short_name"], result, previous
            )
            if need_file_id and added else None
        )
        return target["short_name"], file_id, added

    except _PACK_FULL_ERRORS:
        suffix = target.get("suffix")
        if suffix:
            fresh = await resolve_themed_target(
                client, suffix, _get_input_pack_type(sticker)
            )
            return await add_sticker_to_target(
                client,
                fresh,
                sticker,
                emoji,
                need_file_id,
                allow_duplicates=allow_duplicates,
            )
        return await add_sticker_with_pack_management(
            client,
            sticker,
            emoji,
            need_file_id,
            allow_duplicates=allow_duplicates,
        )

    except StickersetInvalid:
        suffix = target.get("suffix")
        if suffix:
            fresh = await resolve_themed_target(
                client, suffix, _get_input_pack_type(sticker)
            )
            return await add_sticker_to_target(
                client,
                fresh,
                sticker,
                emoji,
                need_file_id,
                allow_duplicates=allow_duplicates,
            )
        return await add_sticker_with_pack_management(
            client,
            sticker,
            emoji,
            need_file_id,
            allow_duplicates=allow_duplicates,
        )

    except Exception as exc:
        logger.exception(
            "[STICKER] Target add failed (pack=%s, type=%s, sticker=%s): %s: %s",
            target.get("short_name"), target.get("type"),
            getattr(sticker, "file_unique_id", None), type(exc).__name__, exc,
        )
        return None, None, False


# ============================================================
# Async queue worker (1 sticker at a time, 1s cooldown)
# ============================================================


async def _get_queue() -> asyncio.Queue:
    global _sticker_queue
    if _sticker_queue is None:
        _sticker_queue = asyncio.Queue()
    return _sticker_queue


def _fingerprint_record(fingerprint, is_video: bool) -> tuple[str, str]:
    return (
        ("sha256", str(fingerprint))
        if is_video
        else ("phash64", f"{int(fingerprint):016x}")
    )


def _document_is_video(document) -> bool:
    mime_type = (getattr(document, "mime_type", "") or "").lower()
    return mime_type == "video/webm" or any(
        isinstance(attribute, types.DocumentAttributeVideo)
        for attribute in getattr(document, "attributes", []) or []
    )


def _document_cache_identity(document) -> dict | None:
    document_id = getattr(document, "id", None)
    if document_id is None:
        raw = getattr(document, "raw", None)
        document_id = getattr(raw, "id", None)
    if document_id is None:
        return None
    unique_id = getattr(document, "file_unique_id", None)
    if unique_id is None:
        raise ValueError(f"Sticker document {document_id} has no parsed file_unique_id")
    return {
        "document_id": str(document_id),
        "file_unique_id": unique_id,
        "mime_type": getattr(document, "mime_type", None),
        "size": getattr(document, "size", getattr(document, "file_size", None)),
        "is_video": _document_is_video(document),
    }


async def _fingerprint_document(client, document, index: int) -> int | str | None:
    try:
        with tempfile.TemporaryDirectory(prefix="nekoub-index-") as folder:
            path = await client.download_media(
                sticker_set_document_file_id(document),
                file_name=os.path.join(folder, f"sticker-{index}"),
            )
            if not path:
                return None
            return await asyncio.to_thread(
                _media_fingerprint, path, _document_is_video(document)
            )
    except Exception:
        logger.debug("[STICKER] Could not fingerprint indexed sticker", exc_info=True)
        return None


async def _fetch_indexed_pack(client, pack: dict, pack_set=None) -> tuple[dict, list[dict]]:
    if pack_set is None:
        pack_set = await client.invoke(
            functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetShortName(short_name=pack["short_name"]),
                hash=0,
            )
        )
    if isinstance(pack_set, types.messages.StickerSet):
        raw_documents = getattr(pack_set, "documents", None) or []
        documents = await asyncio.gather(
            *(
                pyrogram_types.Sticker._parse(
                    client, document,
                    {
                        type(attribute): attribute
                        for attribute in getattr(document, "attributes", []) or []
                    },
                )
                for document in raw_documents
            )
        )
    elif isinstance(pack_set, types.StickerSet):
        raw_documents = getattr(pack_set, "documents", None) or []
        documents = await asyncio.gather(
            *(
                pyrogram_types.Sticker._parse(
                    client, document,
                    {
                        type(attribute): attribute
                        for attribute in getattr(document, "attributes", []) or []
                    },
                )
                for document in raw_documents
            )
        )
        if not raw_documents:
            documents = getattr(pack_set, "stickers", None) or []
    else:
        raise TypeError(f"Unsupported sticker set response: {type(pack_set).__name__}")
    previous = sticker_index.get_pack_records(pack["short_name"])
    records = []
    for index, document in enumerate(documents):
        record = _document_cache_identity(document)
        if record is None:
            raise ValueError(f"Sticker in {pack['short_name']} has no document id")
        prior = previous.get(record["document_id"])
        if prior and sticker_index.metadata_unchanged(prior, record) and prior.get("verified"):
            record.update(
                fingerprint_kind=prior.get("fingerprint_kind"),
                fingerprint=prior.get("fingerprint"),
                verified=True,
            )
        else:
            fingerprint = await _fingerprint_document(client, document, index)
            kind, value = _fingerprint_record(fingerprint, record["is_video"]) if fingerprint is not None else (None, None)
            record.update(fingerprint_kind=kind, fingerprint=value, verified=fingerprint is not None)
        records.append(record)
    return pack, records


_index_refresh_lock = asyncio.Lock()


async def refresh_sticker_index(client, *, force: bool = False) -> bool:
    """Refresh sticker fingerprints; publish only a complete inventory snapshot."""
    async with _index_refresh_lock:
        await ensure_me(client)
        if not force and sticker_index.snapshot_is_usable(me_user.id, max_age=3600):
            return True
        try:
            all_owned_packs = await fetch_all_owned_packs(client, include_archived=True)
            cache = load_cache()
            if cache.get("user_id") != me_user.id:
                cache = {"user_id": me_user.id}
            cache["owned_packs"] = all_owned_packs
            save_cache(cache)
            packs = [pack for pack in all_owned_packs if pack.get("creator")]
            eligible_packs = [pack for pack in packs if not pack.get("is_archived")]
            snapshots = []
            semaphore = asyncio.Semaphore(3)

            async def fetch_one(pack):
                async with semaphore:
                    return await _fetch_indexed_pack(client, pack)

            snapshots = await asyncio.gather(*(fetch_one(pack) for pack in eligible_packs))
            sticker_index.replace_snapshot(snapshots, me_user.id)
            logger.info(
                "[STICKER] Duplicate index refreshed: %s packs, %s stickers, %s bytes",
                len(snapshots), sum(len(records) for _, records in snapshots),
                sticker_index.cache_size(),
            )
            return True
        except Exception:
            logger.exception("[STICKER] Duplicate index refresh failed; keeping last good index")
            return False


async def _ensure_sticker_index(client, packs: list[dict], sticker) -> bool:
    """Ensure a fresh, complete cache for the requested duplicate-check scope."""
    await ensure_me(client)
    required_counts = {pack["short_name"]: int(pack.get("count", 0)) for pack in packs}
    required_names = set(required_counts)

    def usable() -> bool:
        return (
            sticker_index.snapshot_is_usable(me_user.id, max_age=7 * 86400)
            and sticker_index.packs_indexed(required_names)
            and sticker_index.packs_match_counts(required_counts)
            and sticker_index.packs_usable(required_names, max_age=7 * 86400)
            and not sticker_index.has_unverified(
                required_names, getattr(sticker, "mime_type", None),
                getattr(sticker, "file_size", None),
                bool(getattr(sticker, "is_video", False)),
            )
        )

    if not required_names:
        return True
    if usable():
        return True

    task = getattr(main, "_sticker_index_refresh_task", None)
    if task is None or task.done():
        task = asyncio.create_task(refresh_sticker_index(client, force=True))
        setattr(main, "_sticker_index_refresh_task", task)
        logger.info("[STICKER] Refreshing incomplete duplicate-index coverage")
    if not await task:
        return False
    return usable()


async def _index_scheduler_loop() -> None:
    await asyncio.sleep(3)
    client = main.app
    while client is not None:
        try:
            await refresh_sticker_index(client, force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[STICKER] Scheduled index refresh failed")
        await asyncio.sleep(3600)
        client = main.app


async def _update_index_after_add(client, short_name: str, response, target=None) -> None:
    try:
        async with _index_refresh_lock:
            await _update_index_after_add_locked(client, short_name, response, target)
    except Exception:
        logger.warning(
            "[STICKER] Could not update duplicate index after adding to %s",
            short_name, exc_info=True,
        )


async def _update_index_after_add_locked(client, short_name: str, response, target=None) -> None:
    raw_set = getattr(response, "set", None)
    if target is not None:
        pack = dict(target)
    elif raw_set is not None:
        pack = {
            "id": getattr(raw_set, "id", short_name),
            "short_name": getattr(raw_set, "short_name", short_name),
            "is_emoji": bool(getattr(raw_set, "emojis", False)),
            "is_archived": bool(getattr(raw_set, "archived", False)),
            "type": "emoji" if getattr(raw_set, "emojis", False) else "normal",
        }
    else:
        pack = {
            "id": short_name,
            "short_name": short_name,
            "is_emoji": False,
            "is_archived": False,
            "type": "normal",
        }
    pack["short_name"] = short_name
    pack_set = await client.invoke(
        functions.messages.GetStickerSet(
            stickerset=types.InputStickerSetShortName(short_name=short_name),
            hash=0,
        )
    )
    actual_set = getattr(pack_set, "set", None)
    if actual_set is not None:
        pack.update(
            id=getattr(actual_set, "id", pack.get("id")),
            short_name=getattr(actual_set, "short_name", short_name),
            is_emoji=bool(getattr(actual_set, "emojis", pack.get("is_emoji", False))),
            is_archived=bool(getattr(actual_set, "archived", pack.get("is_archived", False))),
        )
    index_pack, records = await _fetch_indexed_pack(client, pack, pack_set)
    sticker_index.replace_pack(index_pack, records)


def ensure_sticker_index_scheduler() -> None:
    task = getattr(main, "_sticker_index_scheduler", None)
    if task is not None and not task.done():
        return
    loop = getattr(getattr(main, "app", None), "loop", None)
    if loop is None:
        return
    try:
        setattr(main, "_sticker_index_scheduler", loop.create_task(_index_scheduler_loop()))
    except Exception:
        logger.exception("[STICKER] Could not start duplicate-index scheduler")


async def _duplicate_check_targets(client, target: dict | None) -> list[dict]:
    """Return only volumes belonging to the selected auto, theme, or explicit pack."""
    if target is not None and not target.get("suffix"):
        return [target]

    await ensure_me(client)
    packs = [
        pack for pack in await cached_owned_packs(client)
        if not pack.get("is_archived")
    ]
    if target is None:
        auto_names = {
            build_default_pack_names(me_user, volume)[1]
            for volume in range(1, len(packs) + 2)
        }
        return [pack for pack in packs if pack["short_name"] in auto_names]

    theme_names = {
        build_themed_pack_names(
            me_user, target["suffix"], volume, target.get("type", "normal")
        )[1]
        for volume in range(1, len(packs) + 2)
    }
    return [
        pack
        for pack in packs
        if pack["is_emoji"] == (target.get("type") == "emoji")
        and pack["short_name"] in theme_names
    ]


async def _queue_worker(client):
    global _worker_running
    if _worker_running:
        return
    _worker_running = True
    queue = await _get_queue()

    while True:
        (
            media_message,
            emoji_str,
            message,
            target,
            show_sticker,
            allow_duplicates,
        ) = await queue.get()
        converted_folder = None
        source = None
        kind = None
        try:
            kind, media = message_media(media_message)
            source = media if kind == "sticker" else None
            if kind in ("image", "video"):
                source, converted_folder = await convert_and_upload(
                    client, media_message, kind
                )
            if source is None:
                logger.error("[STICKER] Unsupported or empty media")
                await send_result_feedback(client, message, False)
                continue
            add_response = {}
            duplicate_pack = None
            if not allow_duplicates:
                pack_type = _get_input_pack_type(source)
                packs_to_check = await _duplicate_check_targets(client, target)
                candidate_names = {
                    pack["short_name"]
                    for pack in packs_to_check
                    if pack.get("is_emoji") == (pack_type == "emoji")
                }
                cache_complete = await _ensure_sticker_index(client, packs_to_check, source)
                if not cache_complete:
                    logger.error(
                        "[STICKER] Duplicate check blocked: index is not complete for %s",
                        ", ".join(sorted(candidate_names)) or "no destination packs",
                    )
                    await send_result_feedback(client, message, False)
                    continue

                cache_started = time.perf_counter()

                is_video = bool(getattr(source, "is_video", False))
                mime_type = getattr(source, "mime_type", None)
                source_size = getattr(source, "file_size", None)
                cache_candidate = sticker_index.find_match(
                    getattr(source, "file_unique_id", None), None, is_video,
                    mime_type, source_size, candidate_names,
                )
                if cache_candidate is None:
                    try:
                        with tempfile.TemporaryDirectory(prefix="nekoub-source-") as folder:
                            source_path = (
                                source.file_id
                                if isinstance(source.file_id, (str, os.PathLike))
                                and os.path.isfile(source.file_id)
                                else await client.download_media(
                                    source.file_id, file_name=os.path.join(folder, "source")
                                )
                            )
                            if not source_path:
                                raise RuntimeError("Could not download source sticker to fingerprint")
                            source_hash = await asyncio.to_thread(
                                _media_fingerprint, source_path, is_video
                            )
                    except Exception:
                        logger.exception("[STICKER] Could not fingerprint source for indexed lookup")
                        await send_result_feedback(client, message, False)
                        continue
                    cache_candidate = sticker_index.find_match(
                        None, source_hash, is_video, mime_type, source_size,
                        candidate_names,
                    )
                logger.info(
                    "[STICKER] Indexed duplicate check finished in %.4fs (match=%s, packs=%s)",
                    time.perf_counter() - cache_started,
                    bool(cache_candidate), len(candidate_names),
                )

                if cache_candidate is not None:
                    duplicate_pack = (
                        next(
                            pack for pack in packs_to_check
                            if pack["short_name"] == cache_candidate["short_name"]
                        ),
                        None,
                    )

            if duplicate_pack:
                pack, pack_set = duplicate_pack
                pack_short_name, file_id, added = pack["short_name"], None, False
                add_response["sticker_set"] = (
                    pack_set or await client.get_sticker_set(pack_short_name)
                )
            elif target is None:
                pack_short_name, file_id, added = await add_sticker_with_pack_management(
                    client,
                    source,
                    emoji_str,
                    need_file_id=show_sticker,
                    response_holder=add_response,
                    allow_duplicates=True,
                )
                if pack_short_name:
                    await cache_owned_packs(client)
                    if added:
                        await _update_index_after_add(
                            client, pack_short_name, add_response.get("sticker_set")
                        )
            else:
                pack_short_name, file_id, added = await add_sticker_to_target(
                    client,
                    target,
                    source,
                    emoji_str,
                    need_file_id=show_sticker,
                    response_holder=add_response,
                    allow_duplicates=True,
                )
                if pack_short_name and added:
                    await cache_owned_packs(client)
                    await _update_index_after_add(
                        client, pack_short_name, add_response.get("sticker_set"), target
                    )
            success = pack_short_name is not None
            if success and not added:
                await _send_reaction(client, message, "🤬", context="duplicate")
            elif success:
                await send_result_feedback(client, message, True)
            else:
                logger.error(
                    "[STICKER] Kang add returned no pack (chat=%s, message=%s, "
                    "source_message=%s, target=%s)",
                    message.chat.id, message.id, media_message.id,
                    (target or {}).get("short_name", "auto"),
                )
                await send_result_feedback(client, message, False)
            sticker_set = None
            if success:
                sticker_set = add_response.get("sticker_set")
                input_sticker = InputSticker(
                    sticker=source.file_id,
                    format=_sticker_format(source),
                    emoji_list=[emoji_str or getattr(source, "emoji", None) or "🌟"],
                )
                await send_sticker_set_debug_json(
                    client, media_message, input_sticker, sticker_set
                )

            if success and show_sticker:
                try:
                    if not getattr(sticker_set, "stickers", None):
                        sticker_set = await client.get_sticker_set(pack_short_name)
                    selected = (
                        find_sticker_in_result(
                            sticker_set,
                            source_sticker=media if kind == "sticker" else None,
                            source_kind=kind,
                        )
                        if not added
                        else (getattr(sticker_set, "stickers", None) or [None])[-1]
                    )
                    if selected is None or not getattr(selected, "file_id", None):
                        raise RuntimeError(
                            f"Could not identify matching sticker in {pack_short_name}."
                        )
                    sticker_message = await client.send_sticker(
                        message.chat.id,
                        selected.file_id,
                        reply_to_message_id=message.id,
                    )
                    logger.info(
                        f"[STICKER] Sent sticker-set item from pack={pack_short_name} "
                        f"unique_id={selected.file_unique_id} "
                        f"as message={sticker_message.id}"
                    )
                except Exception as send_exc:
                    logger.error(
                        f"[STICKER] Added to pack {pack_short_name}, "
                        f"but couldn't send its sticker file: {send_exc}"
                    )
                    logger.exception("[STICKER] Failed to send selected pack sticker")
        except Exception as exc:
            logger.exception(
                f"[STICKER] Queue worker error: {type(exc).__name__}: {exc} "
                f"(source={type(source).__name__ if source is not None else 'none'}, "
                f"media={type(media_message).__name__}, kind={kind!r})"
            )
            await send_result_feedback(client, message, False)
        finally:
            if converted_folder is not None:
                converted_folder.cleanup()
            queue.task_done()
            await asyncio.sleep(ADD_COOLDOWN)


async def enqueue_sticker(
    client,
    media_message: Message,
    emoji_str: str | None,
    message: Message,
    target: dict | None = None,
    show_sticker: bool = False,
    allow_duplicates: bool = False,
):
    queue = await _get_queue()
    asyncio.ensure_future(_queue_worker(client))
    await queue.put(
        (media_message, emoji_str, message, target, show_sticker, allow_duplicates)
    )


# ============================================================
# Reaction / feedback helper
# ============================================================


async def _send_reaction(client, message: Message, reaction: str, *, context: str) -> bool:
    try:
        if hasattr(message, "react"):
            await message.react(reaction)
        elif hasattr(client, "send_reaction"):
            await client.send_reaction(message.chat.id, message.id, reaction)
        else:
            raise RuntimeError("Reaction API is unavailable")
        logger.debug(
            "[STICKER] Reaction sent: %s (context=%s, chat=%s, message=%s)",
            reaction, context, message.chat.id, message.id,
        )
        return True
    except Exception:
        logger.warning(
            "[STICKER] Reaction failed: %s (context=%s, chat=%s, message=%s)",
            reaction, context, message.chat.id, message.id, exc_info=True,
        )
        return False


async def send_result_feedback(client, message: Message, success: bool) -> None:
    reaction = "👍" if success else "💔"
    fallback_text = "Done" if success else "Fail"

    if await _send_reaction(client, message, reaction, context="result"):
        return
    try:
        await message.reply_text(fallback_text)
    except Exception:
        logger.warning(
            "[STICKER] Feedback reply failed (chat=%s, message=%s)",
            message.chat.id, message.id, exc_info=True,
        )


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


KANG_OPTIONS = (
    Option("append_mode", short="-a", action="store_true", help="Append supplied emojis to the sticker's existing emoji list."),
    Option("theme", short="-t", type=str, help="Select or create a themed sticker pack volume."),
    Option("duplicate", short="-d", aliases=("-duplicate",), action="store_true", help="Allow adding a duplicate sticker."),
    Option("show", short="-s", aliases=("-show", "--show", "-send", "--send"), action="store_true", help="Send the matching sticker after processing."),
)


def parse_kang_args(
    text: str, parsed: ParsedOptions | None = None
) -> tuple[str | None, str | None, str, bool, bool, bool]:
    """Parse typed flags, then split remaining positional args into pack and emojis."""
    parsed = parsed or parse_options(text, KANG_OPTIONS, "kang")
    pack_ref: str | None = None
    emoji_tokens: list[str] = []
    for token in parsed.positional:
        if pack_ref is None and parsed.theme is None and _looks_like_pack_ref(token):
            pack_ref = token
        else:
            emoji_tokens.append(token)
    return (
        pack_ref,
        parsed.theme,
        " ".join(emoji_tokens),
        parsed.append_mode,
        parsed.show,
        parsed.duplicate,
    )


# ============================================================
# Commands
# ============================================================


async def start_sticker_monitor(client, message: Message):
    monitoring_chats.add(message.chat.id)
    await message.edit(
        f"Started sticker monitor in this chat ({message.chat.id})"
    )


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


PACKS_OPTIONS = (
    Option("count", short="-c", aliases=("-count",), action="store_true", help="Show owned and overall pack counts."),
    Option("emoji", short="-e", aliases=("-emoji",), action="store_true", help="Filter to emoji packs."),
    Option("animated", short="-a", aliases=("-animated",), action="store_true", help="Include animated and video packs."),
    Option("normal", short="-n", aliases=("-normal",), action="store_true", help="Include static and mixed packs."),
    Option("sticker", short="-s", aliases=("-sticker",), action="store_true", help="Include regular sticker packs."),
)


async def packs_command(
    client, message: Message, parsed: ParsedOptions | None = None
):
    global _pack_index
    logger.info(f"[STICKER] packs command received in chat={message.chat.id}")

    parsed = parsed or parse_options(message.text or "", PACKS_OPTIONS, "packs")
    tokens = list(parsed.positional)

    if tokens and tokens[0].lower() in ("search", "find"):
        await packs_search(client, message, " ".join(tokens[1:]))
        return

    count_only = parsed.count
    page = 1
    selected_types: set[str] = set()
    if parsed.emoji:
        selected_types.add("emoji")
    if parsed.animated:
        selected_types.update(("animated", "mixed"))
    if parsed.normal:
        selected_types.update(("normal", "mixed"))
    if parsed.sticker:
        selected_types.update(("normal", "animated", "mixed"))
    for token in tokens:
        if token.isdigit() and page == 1:
            page = int(token)
        else:
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
        for pack_type in ("normal", "animated", "mixed", "emoji")
    }
    selected_types = {"normal", "animated", "mixed", "emoji"} if len(selected_types) == 4 else selected_types
    filter_types = selected_types or {"normal", "animated", "mixed", "emoji"}
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
        f"Counts: 📄 {counts['normal']} static · 💿 {counts['animated']} animated · "
        f"📺 {counts['mixed']} mixed · 🪎 {counts['emoji']} emoji"
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
        f"Use <code>{_esc(config.CMD_PREFIX)}kang &lt;number|shortname|id&gt;</code> "
        f"for an existing pack, or <code>{_esc(config.CMD_PREFIX)}kang -t &lt;theme&gt;</code> "
        "to select or create a themed pack."
    )
    await message.edit(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def sticker_command(client, message: Message):
    reply = message.reply_to_message
    if not reply:
        await message.edit("Reply to a sticker, image, GIF, or video with `.sticker`.")
        return

    kind, media = message_media(reply)
    if kind is None:
        await message.edit("Unsupported media. Reply to a sticker, image, GIF, or video.")
        return

    try:
        if kind == "sticker":
            await client.send_sticker(
                message.chat.id,
                media.file_id,
                reply_to_message_id=reply.id,
            )
            await message.delete()
            return

        with tempfile.TemporaryDirectory(prefix="nekoub-sticker-") as folder:
            source = await client.download_media(
                reply, file_name=os.path.join(folder, "source")
            )
            if not source:
                raise RuntimeError("Could not download the replied media.")
            output = os.path.join(
                folder, "sticker.webm" if kind == "video" else "sticker.webp"
            )
            if kind == "video":
                await video_to_sticker(source, output)
            else:
                await image_to_sticker(source, output)
            await client.send_sticker(
                message.chat.id,
                output,
                reply_to_message_id=reply.id,
            )
        await message.delete()
    except Exception as exc:
        logger.exception("[STICKER] Standalone sticker conversion/send failed")
        await message.edit(f"Could not create sticker: {type(exc).__name__}: {exc}")


async def sticker_debug_command(client, message: Message):
    replied = message.reply_to_message
    if replied is None or replied.sticker is None:
        await message.edit("Reply to a sticker with `.stickerdebug`.")
        return

    sticker = replied.sticker
    pack_name = getattr(sticker, "set_name", None)
    if not pack_name:
        await message.edit("The replied sticker is not associated with a sticker pack.")
        return

    try:
        sticker_set = await client.get_sticker_set(pack_name)
        payload = {
            "replied_msg": _json_value(replied, include_raw=True),
            "replied_sticker": _json_value(sticker, include_raw=True),
            "response_stickerset": _json_value(sticker_set, include_raw=True),
        }
        path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", delete=False
            ) as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, default=repr)
                path = output.name
            await client.send_document(
                message.chat.id,
                path,
                file_name=f"sticker-debug-{pack_name}.json",
                reply_to_message_id=message.id,
            )
            await message.delete()
        finally:
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    except Exception as exc:
        logger.exception("[STICKER] Could not export sticker debug data")
        await message.edit(f"Could not fetch sticker pack data: {type(exc).__name__}: {exc}")


async def kang_command(
    client, message: Message, parsed_options: ParsedOptions | None = None
):
    logger.info(
        f"[STICKER] .kang handler received chat={message.chat.id} message={message.id}"
    )
    reply = message.reply_to_message
    if reply is None and message.reply_to_message_id:
        reply = await client.get_messages(
            message.chat.id, message.reply_to_message_id
        )
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

    (
        pack_ref,
        theme,
        emoji_str,
        append_mode,
        show_sticker,
        allow_duplicates,
    ) = parse_kang_args(message.text or "", parsed_options)

    if not await _send_reaction(client, message, "👨‍💻", context="start"):
        await _send_reaction(client, message, "👀", context="start-fallback")

    # Resolve the target pack (None => default auto pack).
    target: dict | None = None
    if theme:
        try:
            target = await resolve_themed_target(
                client, theme, _get_input_pack_type(media)
            )
        except Exception as exc:
            logger.error(f"[STICKER] Themed pack lookup failed: {exc}")
            await message.reply_text(
                f"Couldn't resolve theme `{theme}`: {type(exc).__name__}"
            )
            return
    elif pack_ref:
        try:
            target = await resolve_pack_reference(client, pack_ref)
        except Exception as exc:
            logger.error(f"[STICKER] Pack reference lookup failed: {exc}")
            target = None
        if target is None:
            await message.reply_text(
                f"Couldn't find a pack matching `{pack_ref}`. Use `-t {pack_ref}` "
                "to select or create a themed pack."
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
        await enqueue_sticker(
            client, reply, None, message, target, show_sticker, allow_duplicates
        )
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
        await enqueue_sticker(
            client,
            reply,
            "".join(combined),
            message,
            target,
            show_sticker,
            allow_duplicates,
        )
    else:
        if emoji_count > MAX_EMOJI_COUNT:
            await message.reply_text(f"Too many emojis ({emoji_count}/6).")
            return
        await enqueue_sticker(
            client,
            reply,
            "".join(user_emojis),
            message,
            target,
            show_sticker,
            allow_duplicates,
        )


ensure_sticker_index_scheduler()


# ============================================================
# Monitor handler (outgoing stickers in monitored chats)
# ============================================================


async def monitor_sticker_handler(client, message: Message):
    if message.chat.id not in monitoring_chats:
        return
    if message.sticker is None:
        return

    await enqueue_sticker(client, message, None, message, None, False)


class StickerPlugin(Plugin):
    id = "sticker"
    category = "Stickers"
    description = "Manage sticker packs and process stickers."

    @h.command(
        "start_sticker_monitor",
        short_description="Monitor stickers you send in this chat.",
        description="Start watching stickers you send in this chat and queue them for your automatic pack. Monitoring is stored in memory and must be enabled again after a restart.",
        examples=(Example("{cmd}", "Start monitoring in the current chat."),),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def start_sticker_monitor_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await start_sticker_monitor(client, message)

    @h.command(
        "stop_sticker_monitor",
        short_description="Stop sticker monitoring in every chat.",
        description="Stop monitoring and clear the in-memory monitor list for all chats. This command does not delete stickers already added to your packs.",
        examples=(Example("{cmd}", "Stop monitoring in every chat."),),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def stop_sticker_monitor_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await stop_sticker_monitor(client, message)

    @h.command(
        "packs",
        aliases=("mypacks",),
        signature="[search <keyword>] [-c] [-e] [-a] [-n] [-s]",
        short_description="List your sticker packs or search public packs.",
        description="List your own sticker packs with paging, or add `search <keyword>` to search public packs. Filters: -c count only, -e emoji packs, -a animated/video packs, -n static packs, -s regular sticker packs. Combine filters as needed.",
        options=PACKS_OPTIONS,
        examples=(
            Example("{cmd}", "List your packs."),
            Example("{cmd} search cats", "Search public packs for cats."),
            Example("{cmd} -e", "Show emoji packs."),
        ),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def packs_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await packs_command(client, message, parsed)

    @h.command("sticker", signature="(reply to media)", short_description="Convert replied media into a sticker.", description="Reply to a photo, GIF, or video to convert it into a sticker. Sticker output behavior depends on the source media type.", examples=(Example("{cmd} (reply to a photo)"),), prefixes=config.CMD_PREFIX, filters=filters.me)
    async def sticker_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await sticker_command(client, message)

    @h.command(
        "stickerdebug",
        signature="(reply to a sticker)",
        short_description="Export sticker and pack metadata as JSON.",
        description="Reply to a sticker to send a JSON document containing sticker metadata, its pack details, and raw Telegram fields. It is intended for debugging sticker issues.",
        examples=(Example("{cmd} (reply to a sticker)", "Export details for the replied sticker."),),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def sticker_debug_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await sticker_debug_command(client, message)

    @h.command(
        "kang",
        signature="[pack] [emoji] [-a] [-t theme] [-d] [-s]",
        short_description="Add replied media to an automatic, themed, or named sticker pack.",
        description=(
            "Reply to a sticker, photo, GIF, or video. With no destination, add it to your automatic pack. "
            "Give a pack number, short name, or id to add to a pack you own; use -t THEME to select or create a themed volume. "
            "A bare word is resolved only as a pack reference. Photos become static stickers; GIFs and videos become video stickers. "
            "Packs are split into volumes of up to 120 stickers. With no emoji argument the source emoji is kept; up to 6 emojis are supported. "
            "-a appends to the source emojis; -d skips duplicate checking. Duplicate checks only scan the selected destination pack family. "
            "Pack names can be customized with the STICKERSET_* settings in the environment file."
        ),
        options=KANG_OPTIONS,
        help_entries=(
            {
                "signature": "[pack] [emoji] [-a] [-t theme] [-d] [-s]",
                "description": (
                    "Reply to a sticker, photo, GIF, or video. With no destination, add it to your automatic pack. "
                    "Choose an owned pack by number, short name, or id, or use -t THEME to select or create a themed volume. "
                    "No emoji keeps the source emoji; up to 6 emojis are supported. -a appends instead of replacing. "
                    "Duplicate checks scan only the selected destination pack family; -d bypasses those checks. "
                    "Pack names can be customized with the STICKERSET_* settings in the environment file."
                ),
            },
        ),
        examples=(
            Example("{cmd}", "Add the replied media to your automatic pack."),
            Example("{cmd} 2 🐱", "Add it to pack number 2 and set its emoji to 🐱."),
            Example("{cmd} -t anime -a 🐱", "Use the anime themed volume and append 🐱 to its emoji list."),
            Example("{cmd} mypack -d", "Add it to mypack even if it is already present."),
        ),
        prefixes=config.CMD_PREFIX,
        filters=filters.me,
    )
    async def kang_handler(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"],
        parsed: ParsedOptions,
    ) -> None:
        await kang_command(client, message, parsed)

    @h.message(filters=filters.me & filters.sticker)
    async def monitor_sticker(
        self, client: Client, message: Message, ctx: PluginContext["StickerPlugin"]
    ) -> None:
        await monitor_sticker_handler(client, message)
