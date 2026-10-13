import base64
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


async def generate_quote(
    client,
    message: Message,
    custom_text: str | None = None,
    include_reply: bool = False,
    color: str = DEFAULT_COLOR,
) -> bytes | None:
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

    if include_reply and message.reply_to_message:
        reply = message.reply_to_message
        r_sender = reply.from_user or reply.sender_chat or reply.chat
        r_info = await build_user_info(client, r_sender)
        msg_data["replyMessage"] = {
            "name": (r_info or {}).get("name", "User"),
            "text": (reply.text or reply.caption or " ")[:100],
            "entities": convert_entities(
                reply.entities or reply.caption_entities
            ),
            "chatId": getattr(reply.chat, "id", 0),
            "from": r_info or {"id": 1, "first_name": "User", "name": "User"},
        }

    payload = {
        "type": "quote",
        "format": "webp",
        "backgroundColor": color,
        "messages": [msg_data],
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


def parse_args(args: str) -> tuple[str, str]:
    color = DEFAULT_COLOR
    parts = args.split()
    if parts:
        head = parts[0].lower()
        if head.startswith("#") and len(head) in (4, 7):
            color = head
            parts = parts[1:]
        elif head in NAMED_COLORS:
            color = NAMED_COLORS[head]
            parts = parts[1:]
    return color, " ".join(parts)


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
            f"`{config.CMD_PREFIX}q [#color] [text]`"
        )
        return

    parts = (message.text or "").split(maxsplit=1)
    command = parts[0].removeprefix(config.CMD_PREFIX).lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    color, custom_text = parse_args(args)

    await message.edit("🖼️ Generating quote...")
    try:
        image = await generate_quote(
            client,
            reply,
            custom_text=custom_text or None,
            include_reply=command == "qr",
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
