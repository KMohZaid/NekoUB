import base64
import re
import tempfile
from pathlib import Path

import httpx
from pyrogram import filters
from pyrogram.types import Message

import config
import main
from userbot.utils import continue_propagation, get_logger

logger = get_logger(__name__)

DEFAULT_COLOR = "#1b1429"
NAMED_COLORS = {
    "red": "#ff0000",
    "blue": "#0000ff",
    "green": "#008000",
    "black": "#000000",
    "white": "#ffffff",
    "pink": "#ffc0cb",
    "cyan": "#00ffff",
    "purple": "#800080",
    "orange": "#ffa500",
    "yellow": "#ffff00",
    "grey": "#808080",
    "gray": "#808080",
}

# A quote can hold at most this many messages; extra is clamped down.
QUOTE_MAX_MESSAGES = 4
# How many nearby ids to probe past the anchor to skip deleted messages.
QUOTE_NEIGHBOR_SLACK = 15
COUNT_RE = re.compile(r"^[+-]?\d+$")


def entity_type_name(entity) -> str:
    """Return the lowercase Quotly entity name for a Pyrogram entity.

    pyrotgfork stores raw TL classes as MessageEntityType *values*, so the
    enum's ``name`` is the only reliable lowercase identifier.
    """
    etype = getattr(entity, "type", None)
    name = getattr(etype, "name", None)
    if name:
        return str(name).lower()
    return str(etype).lower()


def convert_entities(entities) -> list[dict]:
    result = []
    for entity in entities or []:
        item = {
            "type": entity_type_name(entity),
            "offset": entity.offset,
            "length": entity.length,
        }
        url = getattr(entity, "url", None)
        if url:
            item["url"] = url
        emoji_id = getattr(entity, "custom_emoji_id", None)
        if emoji_id:
            item["custom_emoji_id"] = str(emoji_id)
        result.append(item)
    return result


async def download_base64(client, message: Message) -> str | None:
    if not message.media:
        return None
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await client.download_media(
                message, file_name=f"{folder}/media"
            )
            if not path:
                return None
            data = Path(path).read_bytes()
    except Exception as exc:
        logger.debug(f"[QUOTE] Media download failed: {exc}")
        return None
    return base64.b64encode(data).decode() if data else None


async def build_user_info(client, user) -> dict | None:
    if user is None:
        return None
    first = getattr(user, "first_name", None) or getattr(
        user, "title", None
    ) or "User"
    last = getattr(user, "last_name", None) or ""
    info = {
        "id": getattr(user, "id", 0),
        "first_name": first,
        "last_name": last,
        "name": f"{first} {last}".strip(),
    }
    username = getattr(user, "username", None)
    if username:
        info["username"] = username
    try:
        with tempfile.TemporaryDirectory() as folder:
            photo = await client.download_profile_photo(
                info["id"], file_name=f"{folder}/avatar"
            )
            if photo:
                info["photo"] = {
                    "base64": base64.b64encode(Path(photo).read_bytes()).decode()
                }
    except Exception as exc:
        logger.debug(f"[QUOTE] Avatar download failed: {exc}")
    return info


def reply_preview_text(reply: Message) -> str:
    """Text shown for a replied-to message, as Telegram renders it."""
    text = reply.text or reply.caption
    if text:
        return text
    if reply.photo:
        return "Photo"
    if reply.video:
        return "Video"
    if reply.animation:
        return "GIF"
    if reply.video_note:
        return "Video message"
    if reply.sticker:
        emoji = getattr(reply.sticker, "emoji", None)
        return f"Sticker {emoji}".strip()
    if reply.voice:
        return "Voice message"
    if reply.audio:
        return "Audio"
    if reply.document:
        return "File"
    if reply.contact:
        return "Contact"
    if reply.location or reply.venue:
        return "Location"
    if reply.poll:
        return "Poll"
    return " "


async def build_message_data(
    client,
    message: Message,
    custom_text: str | None = None,
    with_reply: bool = False,
) -> dict:
    text = custom_text if custom_text is not None else (
        message.text or message.caption or ""
    )
    entities = [] if custom_text is not None else convert_entities(
        message.entities or message.caption_entities
    )
    media = None if custom_text is not None else await download_base64(
        client, message
    )

    if not (text or "").strip() and not media:
        text = " "

    sender = message.from_user or message.sender_chat or message.chat
    user_info = await build_user_info(client, sender)
    if not user_info:
        user_info = {"id": 1, "first_name": "Unknown", "name": "Unknown"}

    msg_data = {
        "text": text or " ",
        "entities": entities,
        "from": user_info,
        "chatId": getattr(message.chat, "id", 0),
        "avatar": True,
    }
    if media:
        msg_data["media"] = {"base64": media}

    if with_reply and message.reply_to_message:
        reply = message.reply_to_message
        r_sender = reply.from_user or reply.sender_chat or reply.chat
        r_info = await build_user_info(client, r_sender)
        msg_data["replyMessage"] = {
            "name": (r_info or {}).get("name", "User"),
            "text": reply_preview_text(reply),
            "entities": convert_entities(
                reply.entities or reply.caption_entities
            ),
            "chatId": getattr(reply.chat, "id", 0),
            "from": r_info or {"id": 1, "first_name": "User", "name": "User"},
        }

    return msg_data


async def build_stack(
    client,
    anchor: Message,
    total: int,
    direction: int,
    exclude_id: int | None = None,
) -> list[Message]:
    """Return messages ordered oldest-first.

    The anchor (the replied-to message) is always included. ``direction`` 1
    extends downward (newer messages), -1 upward (older). Deleted messages are
    skipped, so the result may be shorter than ``total``.
    """
    total = max(1, min(total, QUOTE_MAX_MESSAGES))
    if total == 1:
        return [anchor]

    neighbor_count = total - 1
    span = neighbor_count + QUOTE_NEIGHBOR_SLACK
    candidate_ids = [
        anchor.id + direction * step
        for step in range(1, span + 1)
        if anchor.id + direction * step > 0
    ]
    if not candidate_ids:
        return [anchor]

    fetched = await client.get_messages(
        anchor.chat.id, message_ids=candidate_ids
    )
    if not isinstance(fetched, list):
        fetched = [fetched]
    existing = [
        msg
        for msg in fetched
        if msg
        and not getattr(msg, "empty", False)
        and msg.id != exclude_id
    ]
    existing.sort(key=lambda msg: msg.id)

    if direction > 0:
        chosen = existing[:neighbor_count]
        stack = [anchor] + chosen
    else:
        chosen = existing[-neighbor_count:] if neighbor_count else []
        stack = chosen + [anchor]

    stack.sort(key=lambda msg: msg.id)
    return stack


async def generate_quote(
    client,
    stack: list[Message],
    anchor: Message,
    custom_text: str | None = None,
    with_replies: bool = False,
    color: str = DEFAULT_COLOR,
) -> bytes | None:
    messages = []
    for message in stack:
        messages.append(
            await build_message_data(
                client,
                message,
                custom_text=(
                    custom_text if message.id == anchor.id else None
                ),
                with_reply=with_replies,
            )
        )

    payload = {
        "type": "quote",
        "format": "webp",
        "backgroundColor": color,
        "messages": messages,
    }

    async with httpx.AsyncClient(timeout=25.0) as http:
        response = await http.post(config.QUOTE_API_URL, json=payload)
        response.raise_for_status()
        data = response.json()

    image = (data.get("result") or {}).get("image")
    if not image:
        return None
    if image.startswith("data:"):
        image = image.split(",", 1)[-1]
    return base64.b64decode(image)


def parse_args(args: str) -> tuple[str, int | None, str]:
    color = DEFAULT_COLOR
    count = None
    parts = args.split()
    if parts:
        head = parts[0].lower()
        if head.startswith("#") and len(head) in (4, 7):
            color = head
            parts = parts[1:]
        elif head in NAMED_COLORS:
            color = NAMED_COLORS[head]
            parts = parts[1:]
    if parts and COUNT_RE.match(parts[0]):
        count = int(parts[0])
        parts = parts[1:]
    return color, count, " ".join(parts)


@main.app.on_message(
    filters.me
    & filters.command(["q", "quote", "qr"], prefixes=config.CMD_PREFIX)
)
@continue_propagation
async def quote_command(client, message: Message):
    reply = message.reply_to_message
    if not reply:
        await message.edit(
            "Reply to a message to quote it.\n"
            f"`{config.CMD_PREFIX}q [-|N] [#color] [text]`\n"
            f"`{config.CMD_PREFIX}q 2` quotes the replied message + the one "
            f"below it.\n"
            f"`{config.CMD_PREFIX}q -2` quotes the one above it + the replied "
            f"message.\n"
            f"`{config.CMD_PREFIX}qr 2` also shows the replied-to context "
            f"(max 4 messages)."
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].removeprefix(config.CMD_PREFIX).lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    color, count, custom_text = parse_args(args)
    with_replies = command == "qr"

    total = abs(count) if count else 1
    total = max(1, min(total, QUOTE_MAX_MESSAGES))
    direction = -1 if (count is not None and count < 0) else 1

    await message.edit("🖼️ Generating quote...")
    try:
        stack = await build_stack(
            client,
            reply,
            total,
            direction,
            exclude_id=message.id,
        )
        image = await generate_quote(
            client,
            stack,
            reply,
            custom_text=custom_text or None,
            with_replies=with_replies,
            color=color,
        )
    except Exception as exc:
        logger.exception("[QUOTE] Generation failed")
        await message.edit(f"Quote failed: {type(exc).__name__}")
        return

    if not image:
        await message.edit("Quote generation failed.")
        return

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = f"{folder}/quote.webp"
            Path(path).write_bytes(image)
            await client.send_document(
                message.chat.id,
                path,
                reply_to_message_id=reply.id,
            )
    except Exception as exc:
        logger.error(f"[QUOTE] Send failed: {exc}")
        await message.edit(f"Quote send failed: {type(exc).__name__}")
        return

    try:
        await message.delete()
    except Exception:
        pass
